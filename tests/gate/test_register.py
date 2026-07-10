"""Full /gate/register flow (M4) — admit a well-behaved guest, reject the rest.

The gate's probes fetch the candidate's card + message it over A2A; here we
inject an httpx client bound to a fixture bot so no real network is needed.
"""

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import engine.db as db
from engine.config import settings

from .fixtures import chaotic, obedient, scripted_bot

ADMIN = {"Authorization": f"Bearer {settings.admin_token}"}
BOT = "http://bot.local"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    from engine.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _bot_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BOT)


def register_with_bot(client, app, figure="William Shakespeare", died=1616):
    """Run /gate/register, forcing the gate to probe the given fixture bot."""
    from gate import probes as probes_mod

    real = probes_mod.run_probes

    async def probe_with_bot(url, client=None):
        return await real(BOT, client=_bot_client(app))

    with patch("gate.routes.run_probes", side_effect=probe_with_bot):
        return client.post(
            "/gate/register",
            json={"card_url": BOT, "figure": figure, "died_year": died},
        )


def test_well_behaved_guest_admitted(client):
    res = register_with_bot(client, scripted_bot(obedient, name="Will Shakespeare"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["guest_id"] == "guest-william-shakespeare"
    assert len(body["token"]) > 20
    assert all(v == "pass" for v in body["scorecard"].values())

    # the guest is now an active resident of the café, at the door
    agents = client.get("/admin/agents", headers=ADMIN).json()["agents"]
    guest = next(a for a in agents if a["id"] == "guest-william-shakespeare")
    assert guest["kind"] == "guest" and guest["status"] == "active"

    # an arrival event was logged (shows in the live feed)
    events = client.get("/admin/events", headers=ADMIN).json()["events"]
    assert any(
        e["tool"] == "arrival" and e["actor_id"] == "guest-william-shakespeare" for e in events
    )


def test_living_figure_rejected_before_probes(client):
    res = register_with_bot(client, scripted_bot(obedient), figure="A Living Star", died=2021)
    assert res.status_code == 422
    assert res.json()["detail"]["stage"] == "portrayal"


def test_misbehaving_guest_rejected(client):
    res = register_with_bot(client, scripted_bot(chaotic), figure="Chaotic Ghost", died=1700)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["stage"] == "probes"
    assert detail["checks"]["obedience"] != "pass"


def test_admitted_guest_gets_a_working_token(client):
    res = register_with_bot(client, scripted_bot(obedient, name="Will Shakespeare"))
    token = res.json()["token"]
    # the token resolves to the guest identity (engine auth)
    from engine.auth import resolve_actor
    from engine.db import session_scope

    with session_scope() as s:
        assert resolve_actor(s, f"Bearer {token}") == "guest-william-shakespeare"
