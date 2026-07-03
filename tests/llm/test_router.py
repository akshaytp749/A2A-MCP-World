"""LLM router budget tests (TDD — committed before llm/router.py exists).

Budgets are code, not vibes (CLAUDE.md hard rule 3): per-tick call ceilings
(FR-2.3: ≤6 LLM calls per NPC per tick) and a daily free-tier request quota
with a warn hook at 80% (FR-4.4). NEVER fix a failing budget test by raising
the budget.
"""

import pytest

from llm.router import (
    TIER_MODELS,
    DailyQuotaExceeded,
    LLMResult,
    LLMRouter,
    TickBudgetExceeded,
)


class FakeModel:
    """Records calls; returns canned text. Injected instead of the Gemini client."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, *, model, system, prompt, max_tokens) -> LLMResult:
        self.calls.append({"model": model, "system": system, "prompt": prompt,
                           "max_tokens": max_tokens})
        return LLMResult(text="ok", input_tokens=10, output_tokens=2, model=model)


@pytest.fixture()
def fake():
    return FakeModel()


def make_router(fake, **kw):
    kw.setdefault("daily_quota", 100)
    kw.setdefault("langfuse_enabled", False)
    return LLMRouter(generate_fn=fake, **kw)


# ------------------------------------------------------------------ tier routing


def test_tiers_map_to_pinned_models(fake):
    router = make_router(fake)
    router.generate(tier="cheap", agent_id="darwin", tick=1, prompt="hi")
    router.generate(tier="mid", agent_id="socrates", tick=1, prompt="hi")
    assert fake.calls[0]["model"] == TIER_MODELS["cheap"]
    assert fake.calls[1]["model"] == TIER_MODELS["mid"]


def test_unknown_tier_rejected(fake):
    router = make_router(fake)
    with pytest.raises(ValueError):
        router.generate(tier="premium", agent_id="x", tick=1, prompt="hi")
    assert fake.calls == []


# ------------------------------------------------------------- per-tick ceiling


def test_tick_ceiling_is_six_calls_per_agent(fake):
    router = make_router(fake)
    for _ in range(6):
        router.generate(tier="cheap", agent_id="twain", tick=3, prompt="hi")
    with pytest.raises(TickBudgetExceeded):
        router.generate(tier="cheap", agent_id="twain", tick=3, prompt="hi")
    assert len(fake.calls) == 6  # the 7th call never reached the model


def test_tick_ceiling_is_per_agent(fake):
    router = make_router(fake)
    for _ in range(6):
        router.generate(tier="cheap", agent_id="twain", tick=3, prompt="hi")
    # a different agent still has budget on the same tick
    router.generate(tier="cheap", agent_id="tesla", tick=3, prompt="hi")
    assert len(fake.calls) == 7


def test_tick_ceiling_resets_on_new_tick(fake):
    router = make_router(fake)
    for _ in range(6):
        router.generate(tier="cheap", agent_id="twain", tick=3, prompt="hi")
    router.generate(tier="cheap", agent_id="twain", tick=4, prompt="hi")
    assert len(fake.calls) == 7


# ---------------------------------------------------------------- daily quota


def test_daily_quota_blocks_further_calls(fake):
    router = make_router(fake, daily_quota=3)
    for i in range(3):
        router.generate(tier="cheap", agent_id=f"a{i}", tick=i, prompt="hi")
    with pytest.raises(DailyQuotaExceeded):
        router.generate(tier="cheap", agent_id="a9", tick=99, prompt="hi")
    assert len(fake.calls) == 3


def test_quota_warn_hook_fires_once_at_80_percent(fake):
    warned = []
    router = make_router(fake, daily_quota=10, on_quota_warn=lambda used, quota:
                         warned.append((used, quota)))
    for i in range(9):
        router.generate(tier="cheap", agent_id="a", tick=i, prompt="hi")
    assert warned == [(8, 10)]  # fired exactly once, at 80%


def test_usage_snapshot_reports_counts(fake):
    router = make_router(fake, daily_quota=50)
    for i in range(4):
        router.generate(tier="cheap", agent_id="a", tick=1, prompt="hi")
    usage = router.usage()
    assert usage["used_today"] == 4
    assert usage["daily_quota"] == 50
