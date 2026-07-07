"""Cognition-loop unit tests: budgets, plan execution, reply routing.

Fake engine + fake model — no network, no real LLM (mock in unit tests,
per CLAUDE.md workflow).
"""

import json

from llm.router import LLMResult, LLMRouter
from npc.a2a_server import InboxItem
from npc.config import NpcConfig
from npc.mind import MAX_ACTIONS, Mind

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
        if tool == "look":
            return {
                "ok": True,
                "occupants": [],
                "items": [],
                "you": {"coins": 10},
                "description": "the café",
            }
        if tool == "recall_events":
            return {"ok": True, "events": []}
        if tool == "recall":
            return {"ok": True, "memories": []}
        return {"ok": True}

    def acting_calls(self):
        return [
            (t, a)
            for t, a in self.calls
            if t
            in (
                "say",
                "whisper",
                "emote",
                "give",
                "pick_up",
                "drop",
                "trade_offer",
                "trade_respond",
            )
        ]


class PlanModel:
    """Returns a fixed plan (or sequence of raw texts) as the LLM."""

    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.n_calls = 0

    def __call__(self, *, model, system, prompt, max_tokens) -> LLMResult:
        text = self.texts[min(self.n_calls, len(self.texts) - 1)]
        self.n_calls += 1
        return LLMResult(text=text, input_tokens=1, output_tokens=1, model=model)


def plan_json(**kw) -> str:
    base = {"thought": "hm", "actions": [], "replies": [], "new_memories": []}
    base.update(kw)
    return json.dumps(base)


def make_mind(engine, model, peers=None, sent=None):
    async def fake_send(url, text, *, from_character, tick, **_):
        (sent if sent is not None else []).append((url, text))
        return "ack"

    router = LLMRouter(generate_fn=model, langfuse_enabled=False)
    return Mind(
        TWAIN, engine, peers or {"tesla": "http://tesla.local"}, router=router, send_fn=fake_send
    )


async def test_happy_turn_acts_replies_remembers():
    engine = FakeEngine()
    sent: list = []
    model = PlanModel(
        plan_json(
            actions=[{"tool": "say", "args": {"text": "Well now."}}],
            replies=[{"to": "tesla", "text": "Heard about the whiskey?"}],
            new_memories=[{"content": "The Keeper waters the whiskey", "salience": 0.7}],
        )
    )
    mind = make_mind(engine, model, sent=sent)
    summary = await mind.run_tick(5, [InboxItem("tesla", "Twain, any news?", 5)])

    assert ("say", {"text": "Well now."}) in engine.acting_calls()
    assert sent == [("http://tesla.local", "Heard about the whiskey?")]
    assert summary["memories_written"] == 1
    assert model.n_calls == 1  # exactly one decide call on the happy path


async def test_action_cap_enforced():
    engine = FakeEngine()
    many = [{"tool": "emote", "args": {"text": f"gesture {i}"}} for i in range(7)]
    mind = make_mind(engine, PlanModel(plan_json(actions=many)))
    await mind.run_tick(1, [])
    assert len(engine.acting_calls()) == MAX_ACTIONS


async def test_unknown_tool_skipped_not_executed():
    engine = FakeEngine()
    mind = make_mind(
        engine,
        PlanModel(
            plan_json(
                actions=[{"tool": "teleport", "args": {}}, {"tool": "say", "args": {"text": "hi"}}]
            )
        ),
    )
    await mind.run_tick(1, [])
    tools = [t for t, _ in engine.acting_calls()]
    assert tools == ["say"]


async def test_bad_json_gets_exactly_one_repair_call():
    engine = FakeEngine()
    model = PlanModel("I think I shall simply chat today!", plan_json())
    mind = make_mind(engine, model)
    summary = await mind.run_tick(1, [])
    assert model.n_calls == 2  # decide + one repair, never more
    assert summary["actions"] == []


async def test_reply_to_unknown_peer_dropped():
    engine = FakeEngine()
    sent: list = []
    mind = make_mind(
        engine,
        PlanModel(
            plan_json(replies=[{"to": "ghost", "text": "hello?"}, {"to": "twain", "text": "self"}])
        ),
        sent=sent,
    )
    await mind.run_tick(1, [])
    assert sent == []


async def test_failed_action_gets_one_corrective_retry():
    class FailingEngine(FakeEngine):
        async def call(self, tool, args):
            if tool == "give" and args.get("item_id") == 99:
                self.calls.append((tool, args))
                return {"ok": False, "code": "NO_SUCH_ITEM", "msg": "no item 99"}
            return await super().call(tool, args)

    engine = FailingEngine()
    model = PlanModel(
        plan_json(actions=[{"tool": "give", "args": {"target": "tesla", "item_id": 99}}]),
        plan_json(actions=[{"tool": "give", "args": {"target": "tesla", "coins": 1}}]),
    )
    mind = make_mind(engine, model)
    await mind.run_tick(1, [])
    assert model.n_calls == 2  # decide + retry
    assert ("give", {"target": "tesla", "coins": 1}) in engine.acting_calls()


async def test_llm_outage_makes_quiet_turn_not_crash():
    class ExplodingModel:
        def __call__(self, *, model, system, prompt, max_tokens):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    engine = FakeEngine()
    mind = make_mind(engine, ExplodingModel())
    summary = await mind.run_tick(1, [InboxItem("tesla", "hello?", 1)])
    assert summary["actions"] == [] and summary["replied_to"] == []
    assert engine.acting_calls() == []  # silence, not a 500


async def test_pair_cap_stops_after_four_consecutive(monkeypatch):
    from npc.mind import PAIR_CAP
    engine = FakeEngine()
    sent: list = []
    # a plan that always tries to reply to tesla
    model = PlanModel(plan_json(replies=[{"to": "tesla", "text": "and another thing"}]))
    mind = make_mind(engine, model, sent=sent)
    for tick in range(1, 9):                       # consecutive ticks
        await mind.run_tick(tick, [])
    # only PAIR_CAP replies to tesla get through; the rest are withheld
    assert len(sent) == PAIR_CAP


async def test_pair_cap_resets_after_a_lull():
    engine = FakeEngine()
    sent: list = []
    model = PlanModel(plan_json(replies=[{"to": "tesla", "text": "hello again"}]))
    mind = make_mind(engine, model, sent=sent)
    await mind.run_tick(1, [])
    await mind.run_tick(2, [])  # 2 exchanges
    await mind.run_tick(20, [])  # big gap -> fresh run
    assert sent[-1] == ("http://tesla.local", "hello again")  # allowed again


async def test_memory_compaction_writes_a_summary():
    from npc.mind import COMPACT_EVERY

    class ManyMemsEngine(FakeEngine):
        async def call(self, tool, args):
            if tool == "recall":
                return {"ok": True, "memories": [{"content": f"m{i}"} for i in range(20)]}
            return await super().call(tool, args)

    engine = ManyMemsEngine()
    # plan writes 3 memories per tick; after COMPACT_EVERY writes -> a summary
    model = PlanModel(plan_json(new_memories=[{"content": "a", "salience": 0.5},
                                              {"content": "b", "salience": 0.5},
                                              {"content": "c", "salience": 0.5}]))
    mind = make_mind(engine, model)
    for t in range(1, COMPACT_EVERY // 3 + 2):
        await mind.run_tick(t, [])
    summaries = [a for t, a in engine.calls if t == "remember" and a.get("kind") == "summary"]
    assert len(summaries) >= 1
    assert summaries[0]["content"].startswith("[recollection]")
