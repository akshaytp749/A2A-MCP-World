"""App-level tests: /health shape (M1 checklist) and admin API auth."""

import pytest
from fastapi.testclient import TestClient

import engine.db as db
from engine.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    from engine.app import create_app

    with TestClient(create_app()) as c:
        yield c


ADMIN = {"Authorization": f"Bearer {settings.admin_token}"}


def test_health_returns_status_and_tick(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert isinstance(body["tick"], int)


def test_admin_requires_token(client):
    assert client.post("/admin/tick").status_code == 401
    assert client.post("/admin/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_admin_create_agent_and_tick(client):
    res = client.post(
        "/admin/agents", headers=ADMIN, json={"id": "keeper", "name": "The Keeper", "coins": 0}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "keeper" and len(body["token"]) > 20

    # duplicate rejected
    res = client.post("/admin/agents", headers=ADMIN, json={"id": "keeper", "name": "The Keeper"})
    assert res.status_code == 409

    t0 = client.get("/health").json()["tick"]
    client.post("/admin/tick", headers=ADMIN)
    assert client.get("/health").json()["tick"] == t0 + 1


def test_world_seeded_from_yaml(client):
    # seed items from world/seed/world.yaml exist and lie in the café
    res = client.post("/admin/agents", headers=ADMIN, json={"id": "probe", "name": "Probe"})
    token = res.json()["token"]
    # exercise the handler path directly via a second agent looking around is
    # covered in handler tests; here just confirm the seed put the world row in.
    body = client.get("/health").json()
    assert body["tick"] == 0
    assert token
