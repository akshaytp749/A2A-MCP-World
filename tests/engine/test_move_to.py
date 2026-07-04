"""move_to: an agent's spot inside the café is world state, not UI flavor."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from engine.errors import Code, Err
from engine.handlers import execute
from engine.models import Agent, Base, World
from engine.rules import (
    CAFE_SPOTS,
    AgentView,
    MoveTo,
    Ok,
    SetSpot,
    WorldView,
    validate,
)

# ---------------------------------------------------------------- pure rules


def _world(spot="bar"):
    a = AgentView(id="twain", status="active", coins=5, spot=spot)
    return WorldView(tick=1, agents={"twain": a})


def test_move_to_valid_spot():
    res = validate(_world(), MoveTo(actor="twain", spot="chalkboard"))
    assert isinstance(res, Ok)
    assert SetSpot(agent_id="twain", spot="chalkboard") in res.mutations


def test_move_to_unknown_spot_rejected():
    res = validate(_world(), MoveTo(actor="twain", spot="the-moon"))
    assert isinstance(res, Err) and res.code == Code.INVALID_ARG


def test_move_to_same_spot_is_noop():
    res = validate(_world(spot="rug"), MoveTo(actor="twain", spot="rug"))
    assert isinstance(res, Ok) and res.mutations == ()


def test_all_seeded_spots_are_legal():
    for spot in CAFE_SPOTS:
        res = validate(
            _world(spot="alcove" if spot != "alcove" else "bar"), MoveTo(actor="twain", spot=spot)
        )
        assert isinstance(res, Ok)


# ------------------------------------------------------------------ handlers


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        s.add(World(singleton_id=1, tick=1, cafe_name="The Interval", cafe_description="x"))
        s.add(Agent(id="twain", kind="resident", name="Mark Twain", coins=5))
        s.add(Agent(id="tesla", kind="resident", name="Nikola Tesla", coins=5))
        s.commit()
        yield s


def test_move_to_persists_and_logs(session):
    res = execute(session, "twain", "move_to", {"spot": "chalkboard"})
    session.commit()
    assert res["ok"] is True
    assert session.get(Agent, "twain").spot == "chalkboard"


def test_look_reports_spots(session):
    execute(session, "twain", "move_to", {"spot": "alcove"})
    res = execute(session, "tesla", "look", {})
    spots = {o["id"]: o["spot"] for o in res["occupants"]}
    assert spots["twain"] == "alcove"
    assert spots["tesla"] == "bar"  # the default
