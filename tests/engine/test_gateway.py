"""Gateway + operator tests: chat plumbing, visitor rows, eject."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import engine.db as db
from engine.config import settings

ADMIN = {"Authorization": f"Bearer {settings.admin_token}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    from engine.app import create_app

    with TestClient(create_app()) as c:
        c.post(
            "/admin/agents",
            headers=ADMIN,
            json={"id": "twain", "name": "Mark Twain", "endpoint_url": "http://npc-twain:9000"},
        )
        yield c


def test_chat_wraps_message_as_a2a_and_returns_reply(client):
    with patch("gateway.routes.send_to_npc", new=AsyncMock(return_value="Well hello!")) as m:
        res = client.post(
            "/gateway/chat",
            json={"display_name": "Akshay T", "target": "twain", "text": "Evening, Mr Twain."},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Well hello!"
    assert body["visitor_id"] == "visitor-akshay-t"
    kwargs = m.call_args.kwargs
    assert kwargs["extra_metadata"]["visitor"] is True
    assert kwargs["from_character"] == "visitor-akshay-t"


def test_chat_unknown_npc_404(client):
    res = client.post("/gateway/chat", json={"display_name": "A", "target": "ghost", "text": "hi"})
    assert res.status_code == 404


def test_room_snapshot(client):
    res = client.get("/gateway/room")
    assert res.status_code == 200
    body = res.json()
    assert body["cafe"] and isinstance(body["occupants"], list)


def test_eject_marks_agent_and_logs_event(client):
    res = client.post("/admin/eject/twain", headers=ADMIN)
    assert res.status_code == 200
    agents = client.get("/admin/agents", headers=ADMIN).json()["agents"]
    twain = next(a for a in agents if a["id"] == "twain")
    assert twain["status"] == "ejected"
    events = client.get("/admin/events", headers=ADMIN).json()["events"]
    assert any(e["tool"] == "eject" and "packs up" in e["payload"]["text"] for e in events)


def test_ui_served_with_disclaimer(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Fictional AI portrayals of historical figures" in res.text  # FR-5.5
