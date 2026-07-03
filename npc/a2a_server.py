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
    visitor: bool = False
    visitor_name: str = ""


@dataclass
class NpcState:
    config: NpcConfig
    inbox: list[InboxItem] = field(default_factory=list)
    # Set by the runtime once the mind exists: async (InboxItem) -> reply text.
    # Visitor messages are answered inline (FR-5.2); NPC↔NPC still queues.
    visitor_reply_fn: object = None

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
            visitor=bool(meta.get("visitor", False)),
            visitor_name=str(meta.get("visitor_name", "")),
        )

        # Visitors are answered inline (FR-5.2) — a human won't wait for a tick.
        if item.visitor and self.state.visitor_reply_fn is not None:
            try:
                reply_text = await self.state.visitor_reply_fn(item)
            except Exception:
                reply_text = ""
            if reply_text:
                reply = new_text_message(reply_text, role=Role.ROLE_AGENT)
                reply.metadata.update({"inline": True})
                await event_queue.enqueue_event(reply)
                return
            # fall through to the queued path if the mind couldn't answer

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
