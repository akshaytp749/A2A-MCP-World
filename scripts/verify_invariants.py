"""World-state invariant checker (M1 acceptance; MUST exit 0).

Thin CLI over engine/invariants.py (the heartbeat runs the same checks after
every tick). Reads DATABASE_URL from env (defaults to the local dev SQLite db).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from engine.invariants import check  # noqa: E402
from engine.models import Agent, Event  # noqa: E402


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
