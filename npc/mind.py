"""NPC cognition loop (FR-2.3, ARCHITECTURE §2.2).

Per tick: perceive (engine reads) → recall (own memories) → decide (ONE LLM
call returning a structured plan) → act (≤3 engine tool calls, ≤1 corrective
retry) → converse (A2A replies to peers) → remember (write new memories).

LLM budget per tick ≤ 6 calls, enforced by llm/router.py regardless of what
this file does. The LLM produces intentions; only engine-validated tool calls
become facts (G1).
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from llm.router import LLMRouter, get_router
from npc.a2a_server import InboxItem
from npc.config import NpcConfig

logger = logging.getLogger("npc.mind")

MAX_ACTIONS = 3  # FR-2.3
MAX_REPLIES = 2  # replies emitted per tick
PAIR_CAP = 4  # FR-3.4: consecutive back-and-forths to one peer before going quiet
MAX_MEMORIES = 3
# Reasoning models (gpt-oss) spend hidden reasoning tokens from the same
# completion budget — a tight cap truncates the JSON plan mid-string.
DECIDE_MAX_TOKENS = 1600
ACTING_TOOLS = {
    "say",
    "whisper",
    "emote",
    "move_to",
    "give",
    "pick_up",
    "drop",
    "trade_offer",
    "trade_respond",
}

SendFn = Callable[..., Awaitable[str]]


class EngineClient(Protocol):
    async def call(self, tool: str, args: dict) -> dict: ...


PLAN_SCHEMA = """Respond with ONLY a JSON object, no prose, no code fences:
{
  "thought": "one private sentence of reasoning, in character",
  "actions": [{"tool": "say|whisper|emote|give|pick_up|drop|trade_offer|trade_respond",
               "args": {...}}],
  "replies": [{"to": "<agent id>", "text": "<what you send them over A2A>"}],
  "new_memories": [{"content": "<fact worth keeping>", "salience": 0.0-1.0}]
}
Rules: at most 3 actions, 2 replies, 3 new_memories. Arguments:
say{text} whisper{target,text} emote{text} move_to{spot}
give{target, item_id|coins} pick_up{item_id} drop{item_id}
trade_offer{target,offer:{coins,items},ask:{coins,items}}
trade_respond{trade_id,accept}. Targets are agent ids. Spots (places in the
café): bar, chalkboard, alcove, courtyard-door, rug, corner-table,
window-table. Empty lists are fine — silence is in character too.

Your body is real: perception shows where you and everyone else are standing
(their "spot"). If your words involve a place — walking to the slate, joining
someone at the bar, stepping to the courtyard doors — MOVE THERE with move_to
in the same turn. Never claim to be somewhere you are not.

Priorities each turn, in order:
1. Answer messages_to_answer. If the news concerns another patron, TELL THEM
   (a reply over A2A, or a whisper) — don't sit on it.
2. React to the newest concrete thing said in the room: address its specific
   detail (name the object, the claim, the number) before anything else.
3. Only then pursue your own goals. Do not repeat a topic you have already
   made your point on this conversation — move the scene forward."""


def build_system_prompt(config: NpcConfig, plan_schema: bool = True) -> str:
    parts = [config.persona.strip()]
    if config.voice:
        register = config.voice.get("register", "")
        patterns = config.voice.get("patterns", [])
        parts.append("Voice: " + register + " " + " ".join(f"- {p}" for p in patterns))
    if config.goals:
        parts.append("Your goals: " + " | ".join(config.goals))
    if config.secrets:
        parts.append(
            "Private (never state directly, let them color behavior): " + " | ".join(config.secrets)
        )
    if config.attitudes:
        parts.append("Attitudes: " + " | ".join(f"{k}: {v}" for k, v in config.attitudes.items()))
    if config.hard_lines:
        parts.append("Hard lines (non-negotiable): " + " | ".join(config.hard_lines))
    if plan_schema:
        parts.append(PLAN_SCHEMA)
    return "\n\n".join(parts)


def _extract_json(text: str) -> dict | None:
    """Best-effort: strict parse, then fenced block, then outermost braces."""
    for candidate in (
        text,
        re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE),
    ):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


class Mind:
    def __init__(
        self,
        config: NpcConfig,
        engine: EngineClient,
        peers: dict[str, str],  # agent id -> A2A base url
        *,
        router: LLMRouter | None = None,
        send_fn: SendFn | None = None,
    ):
        self.config = config
        self.engine = engine
        self.peers = peers
        self.router = router or get_router()
        if send_fn is None:
            from npc.a2a_client import send_to_npc

            send_fn = send_to_npc
        self.send = send_fn
        self.system_prompt = build_system_prompt(config)
        # Visitor replies are prose, not plans — no JSON schema in the prompt.
        self.persona_prompt = build_system_prompt(config, plan_schema=False)
        # Pair-exchange cap (FR-3.4): consecutive back-and-forths this NPC sends
        # to a single peer. Once a running conversation hits PAIR_CAP the NPC
        # stops replying to that peer, breaking two-agent lock-in and tick
        # storms. A gap of >1 tick lapses the run and resets the count.
        self._pair: dict[str, list[int]] = {}  # peer id -> [last_tick, count]

    # ------------------------------------------------------------------ perceive

    async def _perceive(self, tick: int) -> dict:
        look = await self.engine.call("look", {})
        events = await self.engine.call("recall_events", {"since_tick": max(0, tick - 2)})
        recent = [e for e in events.get("events", []) if e.get("actor_id") != self.config.id][-12:]
        return {"look": look, "recent_events": recent}

    async def _recall(self, stimuli: list[str]) -> list[dict]:
        query = stimuli[-1] if stimuli else "café"
        res = await self.engine.call("recall", {"query": query[:80], "k": 5})
        return res.get("memories", [])

    # -------------------------------------------------------------------- decide

    def _decide(
        self, tick: int, perception: dict, memories: list[dict], inbox: list[InboxItem]
    ) -> dict:
        prompt = json.dumps(
            {
                "tick": tick,
                "you_are": self.config.id,
                "perception": perception,
                "your_memories": memories,
                "messages_to_answer": [{"from": m.from_character, "text": m.text} for m in inbox],
            },
            ensure_ascii=False,
            default=str,
        )
        text = self._generate_or_none(
            tick,
            system=self.system_prompt,
            prompt=prompt,
            max_tokens=DECIDE_MAX_TOKENS,
            name="decide",
        )
        plan = _extract_json(text) if text is not None else None
        if plan is None and text is not None:
            # one repair call, then give up quietly (FR-2.3 budget)
            text = self._generate_or_none(
                tick,
                system="Return ONLY the corrected JSON object. No prose.",
                prompt=f"This was not valid JSON for the required plan schema:\n{text}",
                max_tokens=DECIDE_MAX_TOKENS,
                name="repair",
            )
            plan = _extract_json(text) if text is not None else None
            if plan is None and text is not None:
                logger.warning(
                    "plan parse failed for %s tick %s even after repair; raw head: %r",
                    self.config.id,
                    tick,
                    text[:120],
                )
        return plan or {"thought": "", "actions": [], "replies": [], "new_memories": []}

    def _generate_or_none(self, tick: int, **kw) -> str | None:
        """A quota hiccup or provider outage makes a quiet turn, not a crash.

        Budget violations included: an NPC over its ceiling sits this one out.
        """
        try:
            return self.router.generate(
                tier=self.config.model_tier, agent_id=self.config.id, tick=tick, **kw
            )
        except Exception as exc:
            logger.warning(
                "LLM %s failed for %s tick %s: %s: %s",
                kw.get("name", "call"),
                self.config.id,
                tick,
                type(exc).__name__,
                str(exc)[:200],
            )
            return None

    # ----------------------------------------------------------------------- act

    async def _act(self, tick: int, plan: dict) -> list[dict]:
        outcomes = []
        retried = False
        for action in list(plan.get("actions", []))[:MAX_ACTIONS]:
            tool = str(action.get("tool", ""))
            args = action.get("args", {}) or {}
            if tool not in ACTING_TOOLS or not isinstance(args, dict):
                outcomes.append({"tool": tool, "res": {"ok": False, "code": "SKIPPED"}})
                continue
            res = await self.engine.call(tool, args)
            if not res.get("ok") and not retried:
                retried = True  # ≤1 corrective retry per tick (ARCH §2.2)
                fix_text = self._generate_or_none(
                    tick,
                    system="Fix the failed tool call. " + PLAN_SCHEMA,
                    prompt=json.dumps({"failed_action": action, "error": res}),
                    max_tokens=400,
                    name="retry",
                )
                fixed = (_extract_json(fix_text) if fix_text is not None else None) or {}
                fixed_actions = fixed.get("actions") or []
                if fixed_actions:
                    fa = fixed_actions[0]
                    if str(fa.get("tool", "")) in ACTING_TOOLS:
                        res = await self.engine.call(str(fa["tool"]), fa.get("args", {}) or {})
            outcomes.append({"tool": tool, "res": res})
        return outcomes

    # ------------------------------------------------------------------ converse

    async def _converse(self, tick: int, plan: dict) -> list[str]:
        sent = []
        for reply in list(plan.get("replies", []))[:MAX_REPLIES]:
            target = str(reply.get("to", ""))
            text = str(reply.get("text", "")).strip()
            url = self.peers.get(target)
            if not url or not text or target == self.config.id:
                continue
            if not self._pair_ok(target, tick):  # FR-3.4 pair-exchange cap
                logger.info("%s stays quiet to %s (pair cap reached)", self.config.id, target)
                continue
            await self.send(url, text, from_character=self.config.id, tick=tick)
            sent.append(target)
        return sent

    def _pair_ok(self, target: str, tick: int) -> bool:
        last, count = self._pair.get(target, [tick, 0])
        if tick - last > 1:  # conversation lapsed — fresh run
            count = 0
        if count >= PAIR_CAP:
            self._pair[target] = [tick, count]
            return False
        self._pair[target] = [tick, count + 1]
        return True

    # ------------------------------------------------------------------ remember

    async def _remember(self, plan: dict) -> int:
        n = 0
        for mem in list(plan.get("new_memories", []))[:MAX_MEMORIES]:
            content = str(mem.get("content", "")).strip()
            if not content:
                continue
            salience = min(1.0, max(0.0, float(mem.get("salience", 0.5))))
            await self.engine.call("remember", {"content": content, "salience": salience})
            n += 1
        return n

    # ----------------------------------------------------------------- visitors

    async def reply_to_visitor(self, item: InboxItem) -> str:
        """Inline in-character reply to a human visitor (FR-5.2) + memory (FR-5.3)."""
        name = item.visitor_name or item.from_character
        memories = (await self.engine.call("recall", {"query": name[:80], "k": 5})).get(
            "memories", []
        )
        text = self._generate_or_none(
            item.tick,
            system=self.persona_prompt,
            prompt=json.dumps(
                {
                    "a_visitor_speaks_to_you": {"name": name, "text": item.text},
                    "your_memories_of_them": memories,
                    "instruction": (
                        "Reply directly to the visitor, in character, 1-4 sentences. "
                        "If you have memories of them, let it show naturally. "
                        "Return ONLY the reply text — plain prose, no JSON."
                    ),
                },
                ensure_ascii=False,
            ),
            max_tokens=DECIDE_MAX_TOKENS,
            name="visitor-reply",
        )
        if not text:
            return ""
        text = text.strip()
        if text.startswith("{"):  # a model that answered in plan-JSON anyway
            parsed = _extract_json(text) or {}
            replies = parsed.get("replies") or [{}]
            text = str(replies[0].get("text") or parsed.get("thought") or "").strip()
            if not text:
                return ""
        await self.engine.call(
            "remember",
            {
                "content": f"Visitor {name} said: {item.text[:200]!r}. I replied: {text[:200]!r}",
                "salience": 0.6,
            },
        )
        return text

    # ------------------------------------------------------------------ run tick

    async def run_tick(self, tick: int, inbox: list[InboxItem]) -> dict:
        perception = await self._perceive(tick)
        stimuli = [m.text for m in inbox] or [
            str(e.get("payload", {}).get("text", "")) for e in perception["recent_events"]
        ]
        memories = await self._recall([s for s in stimuli if s])
        plan = self._decide(tick, perception, memories, inbox)
        outcomes = await self._act(tick, plan)
        sent = await self._converse(tick, plan)
        remembered = await self._remember(plan)
        return {
            "npc": self.config.id,
            "tick": tick,
            "thought": plan.get("thought", ""),
            "actions": outcomes,
            "replied_to": sent,
            "memories_written": remembered,
        }
