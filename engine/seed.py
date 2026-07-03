"""Load world/seed/world.yaml into an empty database. Idempotent: seeds once."""

from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from engine.models import Item, World

DEFAULT_STARTING_COINS = 10


def load_seed(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def seed_world(session: Session, seed: dict) -> None:
    """Create the world row and café items if the world doesn't exist yet."""
    if session.get(World, 1) is not None:
        return
    place = seed.get("place", {})
    room = place.get("room", {})
    session.add(
        World(
            singleton_id=1,
            tick=0,
            cafe_name=place.get("name", "The Interval"),
            cafe_description=room.get("description", "").strip(),
        )
    )
    for entry in seed.get("economy", {}).get("items", []):
        session.add(
            Item(name=entry["name"], price=int(entry.get("price", 0)), holder_agent_id=None)
        )
    session.flush()


def starting_coins(seed: dict) -> int:
    return int(seed.get("economy", {}).get("starting_coins", DEFAULT_STARTING_COINS))
