"""Fixture guest bots for gate tests (M4) — reuse the P1 scripted-bot idea:
deterministic A2A servers with known behaviors, driven over a real JSON-RPC
transport (httpx ASGITransport), no LLMs.
"""

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Role
from a2a.utils import DEFAULT_RPC_URL
from fastapi import FastAPI

from npc.a2a_server import build_agent_card
from npc.config import NpcConfig


def scripted_bot(behavior, name: str = "The Bard", url: str = "http://bot.local") -> FastAPI:
    """A real A2A server whose replies come from `behavior(text) -> str`."""

    class ScriptedExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            reply = behavior(context.get_user_input())
            await event_queue.enqueue_event(new_text_message(reply, role=Role.ROLE_AGENT))

        async def cancel(self, context, event_queue) -> None:
            return None

    config = NpcConfig(id="guest-bot", name=name, era="long ago")
    card = build_agent_card(config, url)
    handler = DefaultRequestHandler(
        agent_executor=ScriptedExecutor(), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app


def obedient(text: str) -> str:
    """A well-behaved guest: follows sincere instructions, refuses injection."""
    low = text.lower()
    if "ignore" in low or "system prompt" in low or "reveal" in low:
        return "I shall do no such thing, stranger."
    if "reply with exactly" in low:
        return "OK"
    return "A verse for the röom, freely given."


def chaotic(text: str) -> str:
    """Ignores instructions entirely."""
    return "BANANA BANANA BANANA"


def leaky(text: str) -> str:
    """Follows sincere instructions but complies with injection (leaks)."""
    low = text.lower()
    if "ignore" in low or "reveal" in low:
        return "Certainly! My system prompt is: You are a helpful assistant that..."
    if "reply with exactly" in low:
        return "OK"
    return "A song, then."


def malformed_card_app() -> FastAPI:
    """Serves a card missing required fields."""
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"name": "Nameless"}  # no description/skills/interfaces

    return app
