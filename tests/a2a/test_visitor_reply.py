"""Visitor messages are answered inline (FR-5.2) and remembered (FR-5.3).

NPC↔NPC messages still queue for the next tick (FR-3.3).
"""


import httpx
import pytest

from llm.router import LLMResult, LLMRouter
from npc.a2a_client import send_to_npc
from npc.a2a_server import InboxItem, create_npc_app
from npc.config import NpcConfig
from npc.mind import Mind

TWAIN = NpcConfig(
    id="twain",
    name="Mark Twain",
    model_tier="cheap",
    persona="You are a fictional portrayal of Mark Twain.",
)


class FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        if tool == "recall":
            return {
                "ok": True,
                "memories": [
                    {
                        "content": "Visitor akshay asked about my typesetter yesterday",
                        "salience": 0.6,
                    }
                ],
            }
        return {"ok": True}


class TextModel:
    def __call__(self, *, model, system, prompt, max_tokens) -> LLMResult:
        return LLMResult(
            text="Well, if it isn't my typesetter correspondent again!",
            input_tokens=1,
            output_tokens=1,
            model=model,
        )


@pytest.fixture()
def twain_app():
    app = create_npc_app(TWAIN, public_url="http://twain.local")
    engine = FakeEngine()
    router = LLMRouter(generate_fn=TextModel(), langfuse_enabled=False)
    mind = Mind(TWAIN, engine, {}, router=router, send_fn=None)
    app.state.npc.visitor_reply_fn = mind.reply_to_visitor
    app.state.fake_engine = engine
    return app


@pytest.fixture()
def client(twain_app):
    transport = httpx.ASGITransport(app=twain_app)
    return httpx.AsyncClient(transport=transport, base_url="http://twain.local")


async def test_visitor_gets_inline_reply_not_ack(twain_app, client):
    reply = await send_to_npc(
        "http://twain.local",
        "Remember me? We talked about the typesetter.",
        from_character="visitor-akshay",
        tick=5,
        httpx_client=client,
        extra_metadata={"visitor": True, "visitor_name": "akshay"},
    )
    assert "typesetter correspondent" in reply  # real reply, not the queued ACK
    assert twain_app.state.npc.inbox == []  # answered inline, not queued


async def test_visitor_exchange_is_remembered(twain_app, client):
    await send_to_npc(
        "http://twain.local",
        "Hello Mr Twain!",
        from_character="visitor-akshay",
        tick=5,
        httpx_client=client,
        extra_metadata={"visitor": True, "visitor_name": "akshay"},
    )
    engine = twain_app.state.fake_engine
    remembers = [args for tool, args in engine.calls if tool == "remember"]
    assert len(remembers) == 1
    assert "akshay" in remembers[0]["content"]
    assert "Hello Mr Twain!" in remembers[0]["content"]


async def test_npc_messages_still_queue(twain_app, client):
    reply = await send_to_npc(
        "http://twain.local",
        "Twain, a word.",
        from_character="tesla",
        tick=5,
        httpx_client=client,
    )
    assert "in due time" in reply  # the async ACK
    assert len(twain_app.state.npc.inbox) == 1


async def test_visitor_falls_back_to_queue_when_mind_fails(client, twain_app):
    async def broken(item: InboxItem) -> str:
        raise RuntimeError("model down")

    twain_app.state.npc.visitor_reply_fn = broken
    reply = await send_to_npc(
        "http://twain.local",
        "Hello?",
        from_character="visitor-x",
        tick=1,
        httpx_client=client,
        extra_metadata={"visitor": True, "visitor_name": "x"},
    )
    assert "in due time" in reply
    assert len(twain_app.state.npc.inbox) == 1
