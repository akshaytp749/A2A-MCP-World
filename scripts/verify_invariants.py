"""World-state invariant checker (M1 acceptance; MUST exit 0).

Checks (ARCHITECTURE §3):
  1. no agent has negative coins
  2. every item has exactly one valid holder (an existing agent, or NULL = café)
  3. the world singleton exists with tick >= 0
  4. event ids are strictly increasing (append-only ordering)
  5. trades reference existing agents and carry a legal status

Reads DATABASE_URL from the environment (defaults to the local dev SQLite db).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from engine.models import Agent, Event, Item, Trade, World  # noqa: E402

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


def main() -> int:
    url = os.environ.get("DATABASE_URL", "sqlite:///./interval.db")
    engine = create_engine(url)
    with Session(engine) as session:
        problems = check(session)
        n_events = len(list(session.scalars(select(Event.id))))
        n_agents = len(list(session.scalars(select(Agent.id))))

    if problems:
        print(f"INVARIANT VIOLATIONS ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"all invariants hold ({n_agents} agents, {n_events} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
