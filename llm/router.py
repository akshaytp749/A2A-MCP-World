"""LLM router — every model call in the system goes through here (conventions).

Tier routing to free-tier Gemini models, request-count budgets (the scarce
resource is requests/day, not dollars — ARCHITECTURE §7), and Langfuse tracing.
Budgets are code (hard rule 3): per-tick per-agent ceiling (FR-2.3) and a
daily quota with a warn hook at 80% (FR-4.4). Never raise these to fix a test.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

# Raced live 2026-07-03 (latency incl. network / persona quality / free RPD):
#   groq openai/gpt-oss-120b   0.9s  excellent   ~1,000/day   <- mid primary
#   groq llama-3.3-70b         0.8s  very good    1,000/day
#   groq llama-3.1-8b-instant  0.6s  plain       14,400/day
#   gemini-flash-latest        4.1s  very good   ~1,500/day (10 RPM)
#   gemini-flash-lite-latest   1.1s  good        ~1,500/day   <- cheap primary
# Gemini legacy: 2.5 models capped at 20/day, 2.0 retired — never route there.
# 'provider:' prefix selects the backend; bare ids go to Gemini. Cheap stays
# on Gemini to spread load across providers. Revisit after M3 evals (FR-2.5).
TIER_MODELS = {
    "cheap": "gemini-flash-lite-latest",  # background decide, compaction, filters
    "mid": "groq:openai/gpt-oss-120b",  # focal NPC decide, coherence judge
}
# When a tier's primary model is quota-exhausted, degrade down this chain
# rather than going silent (same idea as FR-4.4: quiet beats crashed).
TIER_FALLBACKS = {
    "cheap": ["groq:llama-3.1-8b-instant"],
    "mid": ["gemini-flash-latest"],
}
# Cross-provider last-resort rungs, tried after the tier chain and only when
# the key is present in env. Free tiers (measured/published 2026-07):
# Groq ~1,000 req/day no card; OpenRouter :free models 50 req/day no card
# (1,000/day after a one-time $10 credit purchase — optional, off by default).
# Override the model per provider via GROQ_MODEL / OPENROUTER_MODEL.
PROVIDER_FALLBACKS = [
    ("GROQ_API_KEY", "groq", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    (
        "OPENROUTER_API_KEY",
        "openrouter",
        "OPENROUTER_MODEL",
        "meta-llama/llama-3.3-70b-instruct:free",
    ),
]


def provider_rungs() -> list[str]:
    """'provider:model' entries for every provider whose key is configured."""
    rungs = []
    for key_env, provider, model_env, default_model in PROVIDER_FALLBACKS:
        if os.environ.get(key_env):
            rungs.append(f"{provider}:{os.environ.get(model_env, default_model)}")
    return rungs


TICK_CEILING = 6  # FR-2.3: max LLM calls per NPC per tick
DAILY_QUOTA_DEFAULT = 1000  # pinned here, not in docs (ARCHITECTURE §7)
WARN_FRACTION = 0.8


class TickBudgetExceeded(RuntimeError):
    pass


class DailyQuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


GenerateFn = Callable[..., LLMResult]


def _gemini_generate(*, model: str, system: str, prompt: str, max_tokens: int) -> LLMResult:
    from google import genai
    from google.genai import types

    client = genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        max_output_tokens=max_tokens,
    )
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    usage = response.usage_metadata
    return LLMResult(
        text=response.text or "",
        input_tokens=usage.prompt_token_count or 0,
        output_tokens=usage.candidates_token_count or 0,
        model=model,
    )


def _openai_compat_generate(
    *, base_url: str, api_key: str, model: str, system: str, prompt: str, max_tokens: int
) -> LLMResult:
    """Groq and OpenRouter both speak the OpenAI chat-completions dialect."""
    import httpx

    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if "gpt-oss" in model:
        # reasoning model: hidden reasoning eats the completion budget; keep it short
        body["reasoning_effort"] = "low"
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    usage = data.get("usage", {})
    return LLMResult(
        text=data["choices"][0]["message"]["content"] or "",
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        model=model,
    )


def _default_generate(*, model: str, system: str, prompt: str, max_tokens: int) -> LLMResult:
    """Dispatch on 'provider:' prefix; bare model ids go to Gemini."""
    if model.startswith("groq:"):
        return _openai_compat_generate(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ["GROQ_API_KEY"],
            model=model.removeprefix("groq:"),
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
        )
    if model.startswith("openrouter:"):
        return _openai_compat_generate(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            model=model.removeprefix("openrouter:"),
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
        )
    return _gemini_generate(model=model, system=system, prompt=prompt, max_tokens=max_tokens)


class LLMRouter:
    def __init__(
        self,
        *,
        generate_fn: GenerateFn | None = None,
        daily_quota: int = DAILY_QUOTA_DEFAULT,
        tick_ceiling: int = TICK_CEILING,
        on_quota_warn: Callable[[int, int], None] | None = None,
        langfuse_enabled: bool | None = None,
        today: Callable[[], date] = date.today,
    ):
        self._generate = generate_fn or _default_generate
        self.daily_quota = daily_quota
        self.tick_ceiling = tick_ceiling
        self._on_quota_warn = on_quota_warn
        self._today = today
        self._day: date | None = None
        self._used_today = 0
        self._warned_today = False
        self._tick_counts: dict[tuple[str, int], int] = {}
        if langfuse_enabled is None:
            langfuse_enabled = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
        self._langfuse_enabled = langfuse_enabled

    # ------------------------------------------------------------------ budgets

    def _roll_day(self) -> None:
        today = self._today()
        if self._day != today:
            self._day = today
            self._used_today = 0
            self._warned_today = False

    def _check_budgets(self, agent_id: str, tick: int) -> None:
        self._roll_day()
        if self._used_today >= self.daily_quota:
            raise DailyQuotaExceeded(
                f"daily LLM quota exhausted ({self._used_today}/{self.daily_quota})"
            )
        key = (agent_id, tick)
        if self._tick_counts.get(key, 0) >= self.tick_ceiling:
            raise TickBudgetExceeded(
                f"agent '{agent_id}' exceeded {self.tick_ceiling} LLM calls on tick {tick}"
            )

    def _record(self, agent_id: str, tick: int) -> None:
        self._used_today += 1
        key = (agent_id, tick)
        self._tick_counts[key] = self._tick_counts.get(key, 0) + 1
        # keep the tick-counter map from growing unboundedly
        if len(self._tick_counts) > 4096:
            stale = [k for k in self._tick_counts if k[1] < tick - 2]
            for k in stale:
                del self._tick_counts[k]
        if not self._warned_today and self._used_today >= WARN_FRACTION * self.daily_quota:
            self._warned_today = True
            if self._on_quota_warn is not None:
                self._on_quota_warn(self._used_today, self.daily_quota)

    def usage(self) -> dict:
        self._roll_day()
        return {"used_today": self._used_today, "daily_quota": self.daily_quota}

    # ----------------------------------------------------------------- generate

    def generate(
        self,
        *,
        tier: str,
        agent_id: str,
        tick: int,
        prompt: str,
        system: str = "",
        max_tokens: int = 512,
        name: str = "decide",
    ) -> str:
        if tier not in TIER_MODELS:
            raise ValueError(f"unknown tier '{tier}' (know: {sorted(TIER_MODELS)})")
        self._check_budgets(agent_id, tick)
        chain = [TIER_MODELS[tier], *TIER_FALLBACKS.get(tier, []), *provider_rungs()]

        last_exc: Exception | None = None
        for model in chain:
            span = None
            if self._langfuse_enabled:
                span = self._start_span(name, model, agent_id, tick, tier, system, prompt)
            try:
                result = self._generate(
                    model=model, system=system, prompt=prompt, max_tokens=max_tokens
                )
            except Exception as exc:
                last_exc = exc
                if span is not None:
                    span.update(level="ERROR", status_message="generation failed")
                    span.end()
                continue  # quota/outage on this model — degrade down the chain
            self._record(agent_id, tick)
            if span is not None:
                span.update(
                    output=result.text,
                    usage_details={"input": result.input_tokens, "output": result.output_tokens},
                )
                span.end()
            return result.text
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _start_span(name, model, agent_id, tick, tier, system, prompt):
        try:
            from langfuse import get_client

            return get_client().start_observation(
                name=name,
                as_type="generation",
                model=model,
                input={"system": system, "prompt": prompt},
                metadata={"agent_id": agent_id, "tick": tick, "tier": tier},
            )
        except Exception:
            return None  # tracing must never break the world


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        quota = int(os.environ.get("LLM_DAILY_QUOTA", DAILY_QUOTA_DEFAULT))
        _router = LLMRouter(daily_quota=quota)
    return _router
