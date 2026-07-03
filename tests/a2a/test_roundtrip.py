"""M2 checks: agent card served at the well-known path; message/send round-trip
between two NPCs over the real JSON-RPC path (in-memory ASGI transport)."""

import httpx
import pytest

from npc.a2a_client import send_to_npc
from npc.a2a_server import create_npc_app
from npc.config import NpcConfig

TWAIN = NpcConfig(id="twain", name="Mark Twain", era="1835–1910", model_tier="mid")
TESLA = NpcConfig(id="tesla", name="Nikola Tesla", era="1856–1943", model_tier="mid")


@pytest.fixture()
def tesla_app():
    return create_npc_app(TESLA, public_url="http://tesla.local")


@pytest.fixture()
def tesla_client(tesla_app):
    transport = httpx.ASGITransport(app=tesla_app)
    return httpx.AsyncClient(transport=transport, base_url="http://tesla.local")


async def test_agent_card_served_at_well_known_path(tesla_client):
    res = await tesla_client.get("/.well-known/agent-card.json")
    assert res.status_code == 200
    card = res.json()
    assert card["name"] == "Nikola Tesla"
    assert card["description"].startswith("Fictional AI portrayal")
    assert card["skills"], "card must declare at least one skill"
    interfaces = card.get("supportedInterfaces") or card.get("supported_interfaces")
    assert interfaces and interfaces[0]["url"] == "http://tesla.local"


async def test_message_send_roundtrip_queues_and_acks(tesla_app, tesla_client):
    reply = await send_to_npc(
        "http://tesla.local",
        "Tesla, I heard the Keeper waters the whiskey. Pass it on.",
        from_character="twain",
        tick=7,
        httpx_client=tesla_client,
    )
    # sender got an ACK (async etiquette, FR-3.3), not an inline monologue
    assert "Nikola Tesla" in reply

    # receiver queued the message with grounded metadata
    inbox = tesla_app.state.npc.inbox
    assert len(inbox) == 1
    assert inbox[0].from_character == "twain"
    assert inbox[0].tick == 7
    assert "whiskey" in inbox[0].text


async def test_inbox_drains_once(tesla_app, tesla_client):
    await send_to_npc(
        "http://tesla.local", "one", from_character="twain", tick=1, httpx_client=tesla_client
    )
    await send_to_npc(
        "http://tesla.local", "two", from_character="twain", tick=1, httpx_client=tesla_client
    )
    state = tesla_app.state.npc
    drained = state.drain_inbox()
    assert [i.text for i in drained] == ["one", "two"]
    assert state.drain_inbox() == []
