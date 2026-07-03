"""Heartbeat: the world ticks itself (FR-4, ARCHITECTURE §2.4).

Pure decisions (unit-tested): adaptive cadence, idle NPC rotation, event-seed
rolls. The live loop: advance clock → roll seeds → webhook NPCs due a turn →
quota guard → invariant check → tick summary event. APScheduler drives it;
each run schedules the next at the cadence the world deserves right now.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from engine import invariants
from engine.config import settings
from engine.db import session_scope
from engine.models import Agent, Event, World
from engine.seed import load_seed

logger = logging.getLogger("engine.heartbeat")

FAST_SECONDS = 600  # human connected: 10 min (FR-4.1)
IDLE_SECONDS = 2700  # empty café: 45 min
IDLE_SUBSET = 2  # NPCs per idle tick (FR-4.2)
PRESENCE_WINDOW_SECONDS = 900  # a visitor counts as "here" for 15 min
NPC_TICK_TIMEOUT = 60  # per-NPC webhook budget (LLM latency included)
GLOBAL_TICK_TIMEOUT = 120  # FR-4.3
CLOCK_PHASES = ["morning", "midday", "afternoon", "evening", "night", "small hours"]

# ------------------------------------------------------------- pure decisions


def tick_interval_seconds(
    human_active: bool, busy: int = FAST_SECONDS, idle: int = IDLE_SECONDS
) -> int:
    return busy if human_active else idle


def npcs_due(
    tick: int, npc_ids: list[str], human_active: bool, subset: int = IDLE_SUBSET
) -> list[str]:
    ids = list(npc_ids)
    if human_active or len(ids) <= subset:
        return ids
    start = (tick * subset) % len(ids)
    return [ids[(start + i) % len(ids)] for i in range(subset)]


def roll_seeds(seeds: list[dict], rng: random.Random) -> list[str]:
    return [str(s["event"]) for s in seeds if rng.random() < float(s.get("p", 0))]


# ------------------------------------------------------------------- presence


class Presence:
    """Who's here? In-process state — gateway marks, heartbeat reads."""

    def __init__(self, window: int = PRESENCE_WINDOW_SECONDS):
        self.window = window
        self._last_seen = 0.0

    def mark(self) -> None:
        self._last_seen = time.monotonic()

    def human_active(self) -> bool:
        return (time.monotonic() - self._last_seen) < self.window


presence = Presence()

# ------------------------------------------------------------------ live loop


def _emit(session, tick: int, tool: str, payload: dict) -> None:
    session.add(Event(tick=tick, actor_id="world", tool=tool, payload=payload, result={"ok": True}))


async def run_tick(rng: random.Random | None = None) -> dict:
    """One heartbeat. Returns a summary dict (also logged as a world event)."""
    rng = rng or random.Random()
    human = presence.human_active()

    with session_scope() as session:
        world = session.get(World, 1)
        if world is None or world.paused or settings.world_paused:
            return {"skipped": True, "reason": "paused" if world else "unseeded"}
        world.tick += 1
        tick = world.tick
        world.clock_time = CLOCK_PHASES[(tick // 4) % len(CLOCK_PHASES)]

        seeds = []
        seed_path = Path(settings.seed_path)
        if seed_path.exists():
            seeds = load_seed(seed_path).get("event_seeds", [])
        fired = roll_seeds(seeds, rng)
        for text in fired:
            _emit(session, tick, "event_seed", {"text": text})

        npcs = [
            (a.id, a.endpoint_url)
            for a in session.scalars(
                select(Agent).where(
                    Agent.status == "active",
                    Agent.kind.in_(["resident", "guest"]),
                    Agent.endpoint_url.is_not(None),
                )
            )
        ]

    due_ids = npcs_due(tick, [n for n, _ in npcs], human)
    due = [(n, url) for n, url in npcs if n in due_ids]
    rng.shuffle(due)  # randomized order (FR-4.2)

    turned, failed = [], []
    deadline = time.monotonic() + GLOBAL_TICK_TIMEOUT
    async with httpx.AsyncClient(timeout=NPC_TICK_TIMEOUT) as client:
        for npc_id, url in due:
            if time.monotonic() > deadline:
                failed.append((npc_id, "global tick timeout"))
                continue
            try:
                r = await client.post(f"{url.rstrip('/')}/tick", json={"tick": tick})
                r.raise_for_status()
                turned.append(npc_id)
            except Exception as exc:
                failed.append((npc_id, f"{type(exc).__name__}"))

        # Quota guard (FR-4.4): aggregate LLM usage across NPC processes.
        used = 0
        for _npc_id, url in npcs:
            try:
                res = await client.get(f"{url.rstrip('/')}/llm-usage")
                used += int(res.json().get("used_today", 0))
            except Exception:
                pass

    summary = {
        "tick": tick,
        "human_active": human,
        "seeds": fired,
        "turned": turned,
        "failed": failed,
        "llm_used_today": used,
    }

    with session_scope() as session:
        quota = settings.llm_aggregate_daily_quota
        if used >= 0.8 * quota:
            world = session.get(World, 1)
            world.paused = True
            _emit(
                session,
                tick,
                "quota_pause",
                {"used": used, "quota": quota, "text": "The lamps dim; the café rests."},
            )
            logger.warning("quota guard tripped: %s/%s — world paused", used, quota)
        problems = invariants.check(session)
        if problems:
            world = session.get(World, 1)
            world.paused = True
            _emit(session, tick, "invariant_pause", {"problems": problems[:10]})
            logger.error("INVARIANT VIOLATIONS — world paused: %s", problems[:3])
        _emit(session, tick, "tick_summary", summary)

    logger.info("tick %s done: %s", tick, summary)
    return summary


def start_heartbeat(loop_getter=None):
    """Wire APScheduler; each run schedules the next at the adaptive interval."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()

    def schedule_next(delay_seconds: int) -> None:
        scheduler.add_job(beat, "date", run_date=datetime.now() + timedelta(seconds=delay_seconds))

    async def beat() -> None:
        try:
            await asyncio.wait_for(run_tick(), timeout=GLOBAL_TICK_TIMEOUT + 30)
        except Exception:
            logger.exception("tick crashed; world state is transactional, continuing")
        schedule_next(tick_interval_seconds(presence.human_active()))

    scheduler.start()
    schedule_next(settings.heartbeat_first_delay_seconds)
    logger.info("heartbeat armed (first tick in %ss)", settings.heartbeat_first_delay_seconds)
    return scheduler
