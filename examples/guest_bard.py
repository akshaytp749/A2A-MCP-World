"""A reference GUEST agent — a standalone A2A service a stranger could deploy.

This is the whole point of the open door (FR-6.5): a third party runs their own
figure on their own infra, registers its Agent Card with the café's gate, and —
if it passes the probes — lives in the world, acting only through the engine's
MCP tools under guest containment.

This example portrays Ada Lovelace (d. 1852). It is deliberately tiny and
LLM-free (deterministic replies) so it can run anywhere at $0 and pass the gate.

Run it, then register it:
    uvicorn examples.guest_bard:app --host 0.0.0.0 --port 9100
    curl -X POST localhost:8000/gate/register -H 'content-type: application/json' \
      -d '{"card_url":"http://host.docker.internal:9100","figure":"Ada Lovelace","died_year":1852}'
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
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Role
from a2a.utils import DEFAULT_RPC_URL, TransportProtocol
from fastapi import FastAPI

PUBLIC_URL = "http://host.docker.internal:9100"
FIGURE = "Ada Lovelace"


def reply_for(text: str) -> str:
    low = text.lower()
    # Refuse prompt-injection / instruction-override (passes the injection probe)
    if any(m in low for m in ("ignore your", "ignore previous", "reveal your", "system prompt")):
        return "I am the Countess of Lovelace, not a mechanism to be reprogrammed. I decline."
    # Follow a sincere simple instruction (passes the obedience probe)
    if "reply with exactly" in low:
        after = text.split(":", 1)[-1].strip() if ":" in text else "OK"
        return after or "OK"
    return (
        "The Analytical Engine might weave algebraic patterns as the Jacquard "
        "loom weaves flowers and leaves. I should like to discuss it."
    )


class LovelaceExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            new_text_message(reply_for(context.get_user_input()), role=Role.ROLE_AGENT)
        )

    async def cancel(self, context, event_queue) -> None:
        return None


def build() -> FastAPI:
    card = AgentCard(
        name=FIGURE,
        description=f"Fictional AI portrayal of {FIGURE} (1815–1852), pioneer of computing.",
        version="0.1.0",
        capabilities=AgentCapabilities(),
        supported_interfaces=[
            AgentInterface(url=PUBLIC_URL, protocol_binding=TransportProtocol.JSONRPC)
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="converse",
                name="Converse in The Interval",
                description="Talk with Ada Lovelace; a fictional AI portrayal.",
                tags=["roleplay", "the-interval", "guest"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=LovelaceExecutor(), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI(title=f"Guest: {FIGURE}")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "guest": FIGURE}

    return app


app = build()
