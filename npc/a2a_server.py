"""NPC A2A server: agent card + message/send, queued not inline (FR-3.2, FR-3.3).

Each NPC process serves /.well-known/agent-card.json and a JSON-RPC endpoint.
Incoming messages are ACKed and pushed to an inbox; the actual in-character
reply happens on the NPC's own tick (prevents recursive tick storms, ADR 3).
"""

from dataclasses import dataclass, field

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

from npc.config import NpcConfig


@dataclass
class InboxItem:
    from_character: str
    text: str
    tick: int
    world: str = ""


@dataclass
class NpcState:
    config: NpcConfig
    inbox: list[InboxItem] = field(default_factory=list)

    def drain_inbox(self) -> list[InboxItem]:
        items, self.inbox = self.inbox, []
        return items


def build_agent_card(config: NpcConfig, public_url: str) -> AgentCard:
    return AgentCard(
        name=config.name,
        description=config.description,
        version="0.1.0",
        capabilities=AgentCapabilities(),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding=TransportProtocol.JSONRPC)
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="converse",
                name="Converse in The Interval",
                description=(
                    f"Talk with {config.name} inside the café. Fictional AI portrayal "
                    "of a long-deceased historical figure (portrayal policy, SPEC §1)."
                ),
                tags=["roleplay", "the-interval"],
            )
        ],
    )


class QueueingExecutor(AgentExecutor):
    """ACK immediately, queue for the cognition loop — never think inline (FR-3.3)."""

    def __init__(self, state: NpcState):
        self.state = state

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        meta = dict(context.message.metadata) if context.message is not None else {}
        item = InboxItem(
            from_character=str(meta.get("from_character", "unknown")),
            text=text,
            tick=int(meta.get("tick", 0)),
            world=str(meta.get("world", "")),
        )
        self.state.inbox.append(item)
        ack = new_text_message(
            f"{self.state.config.name} nods — your words will be answered in due time.",
            role=Role.ROLE_AGENT,
        )
        ack.metadata.update({"ack": True, "queued": len(self.state.inbox)})
        await event_queue.enqueue_event(ack)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def create_npc_app(config: NpcConfig, public_url: str) -> FastAPI:
    state = NpcState(config=config)
    card = build_agent_card(config, public_url)
    handler = DefaultRequestHandler(
        agent_executor=QueueingExecutor(state),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title=f"NPC: {config.name}")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    app.state.npc = state

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "npc": config.id, "inbox": len(state.inbox)}

    return app
