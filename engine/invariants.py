"""World-state invariants (ARCHITECTURE §3) — one source of truth.

Used by scripts/verify_invariants.py (CLI, MUST exit 0) and by the heartbeat
after every tick (a violation pauses the world rather than letting corruption
compound).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.models import Agent, Event, Item, Trade, World

LEGAL_TRADE_STATUS = {"open", "accepted", "declined", "expired"}


def check(session: Session) -> list[str]:
    problems: list[str] = []

    agent_ids = set(session.scalars(select(Agent.id)))

    for agent in session.scalars(select(Agent)):
        if agent.coins < 0:
            problems.append(f"agent '{agent.id}' has negative coins: {agent.coins}")

    for item in session.scalars(select(Item)):
        if item.holder_agent_id is not None and item.holder_agent_id not in agent_ids:
            problems.append(f"item {item.id} held by missing agent '{item.holder_agent_id}'")

    world = session.get(World, 1)
    if world is None:
        problems.append("world singleton row missing")
    elif world.tick < 0:
        problems.append(f"world tick negative: {world.tick}")

    last_id = 0
    for eid in session.scalars(select(Event.id).order_by(Event.id)):
        if eid <= last_id:
            problems.append(f"event id ordering violated at {eid}")
        last_id = eid

    for trade in session.scalars(select(Trade)):
        if trade.status not in LEGAL_TRADE_STATUS:
            problems.append(f"trade {trade.id} has illegal status '{trade.status}'")
        for side in (trade.proposer_id, trade.target_id):
            if side not in agent_ids:
                problems.append(f"trade {trade.id} references missing agent '{side}'")

    return problems
