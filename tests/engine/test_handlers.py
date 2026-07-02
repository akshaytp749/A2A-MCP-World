"""Handler-layer tests: transactions, append-only event log, whisper visibility.

The handler boundary is `execute(session, actor_id, tool, args) -> dict`
returning {ok: True, ...} or {ok: False, code, msg}. Every call — valid or
not — appends exactly one event row; invalid calls NEVER mutate state
(FR-1.3, FR-1.4).
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from engine.errors import Code
from engine.handlers import execute
from engine.models import Agent, Base, Event, Item, Trade, World


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        s.add(
            World(
                singleton_id=1,
                tick=1,
                cafe_name="The Interval",
                cafe_description="A café outside time.",
            )
        )
        s.add(Agent(id="alice", kind="resident", name="Alice", coins=10))
        s.add(Agent(id="bob", kind="resident", name="Bob", coins=10))
        s.add(Agent(id="carol", kind="resident", name="Carol", coins=0))
        s.add(Item(id=1, name="cup", holder_agent_id=None, price=1))
        s.add(Item(id=2, name="notebook", holder_agent_id="alice", price=3))
        s.commit()
        yield s


def events(s: Session) -> list[Event]:
    return list(s.scalars(select(Event).order_by(Event.id)))


# ------------------------------------------------------------------ event log basics


def test_every_call_appends_exactly_one_event(session):
    execute(session, "alice", "say", {"text": "hello"})
    execute(session, "alice", "give", {"target": "bob", "item_id": 99})  # invalid
    session.commit()
    evs = events(session)
    assert len(evs) == 2
    assert evs[0].tool == "say" and evs[0].result["ok"] is True
    assert evs[1].tool == "give" and evs[1].result["ok"] is False


def test_event_ids_monotonic_and_tick_recorded(session):
    for _ in range(3):
        execute(session, "alice", "emote", {"text": "nods"})
    session.commit()
    evs = events(session)
    ids = [e.id for e in evs]
    assert ids == sorted(ids) and len(set(ids)) == 3
    assert all(e.tick == 1 for e in evs)


# ------------------------------------------------------------------------- give


def test_give_item_moves_holder_and_logs(session):
    res = execute(session, "alice", "give", {"target": "bob", "item_id": 2})
    session.commit()
    assert res["ok"] is True
    assert session.get(Item, 2).holder_agent_id == "bob"


def test_give_without_item_rejected_and_state_untouched(session):
    res = execute(session, "alice", "give", {"target": "bob", "item_id": 1})  # cup is in café
    session.commit()
    assert res["ok"] is False and res["code"] == Code.NOT_HOLDER
    assert session.get(Item, 1).holder_agent_id is None


def test_give_coins_never_goes_negative(session):
    res = execute(session, "carol", "give", {"target": "bob", "coins": 1})  # carol has 0
    session.commit()
    assert res["ok"] is False and res["code"] == Code.INSUFFICIENT_COINS
    assert session.get(Agent, "carol").coins == 0
    assert session.get(Agent, "bob").coins == 10


# ---------------------------------------------------------------------- whisper


def test_whisper_to_absent_target_rejected(session):
    res = execute(session, "alice", "whisper", {"target": "ghost", "text": "psst"})
    assert res["ok"] is False and res["code"] == Code.NO_SUCH_AGENT


def test_whisper_visible_only_to_participants(session):
    execute(session, "alice", "whisper", {"target": "bob", "text": "the vault code is 7"})
    execute(session, "alice", "say", {"text": "lovely weather"})
    session.commit()

    bob_view = execute(session, "bob", "recall_events", {"since_tick": 0})
    carol_view = execute(session, "carol", "recall_events", {"since_tick": 0})
    bob_texts = [e["payload"].get("text") for e in bob_view["events"]]
    carol_texts = [e["payload"].get("text") for e in carol_view["events"]]
    assert "the vault code is 7" in bob_texts
    assert "the vault code is 7" not in carol_texts
    assert "lovely weather" in carol_texts


# ---------------------------------------------------------------------- pick_up/drop


def test_pick_up_and_drop_roundtrip(session):
    assert execute(session, "bob", "pick_up", {"item_id": 1})["ok"] is True
    assert session.get(Item, 1).holder_agent_id == "bob"
    assert execute(session, "bob", "drop", {"item_id": 1})["ok"] is True
    assert session.get(Item, 1).holder_agent_id is None


# ------------------------------------------------------------------------- trades


def test_trade_lifecycle_happy_path(session):
    res = execute(
        session,
        "alice",
        "trade_offer",
        {"target": "bob", "offer": {"coins": 0, "items": [2]}, "ask": {"coins": 3, "items": []}},
    )
    assert res["ok"] is True
    trade_id = res["trade_id"]

    res = execute(session, "bob", "trade_respond", {"trade_id": trade_id, "accept": True})
    session.commit()
    assert res["ok"] is True
    assert session.get(Item, 2).holder_agent_id == "bob"
    assert session.get(Agent, "alice").coins == 13
    assert session.get(Agent, "bob").coins == 7
    assert session.get(Trade, trade_id).status == "accepted"


def test_trade_accept_after_item_gone_is_atomic_noop(session):
    trade_id = execute(
        session,
        "alice",
        "trade_offer",
        {"target": "bob", "offer": {"coins": 0, "items": [2]}, "ask": {"coins": 3, "items": []}},
    )["trade_id"]
    # Alice gives the offered notebook away before Bob accepts.
    assert execute(session, "alice", "give", {"target": "carol", "item_id": 2})["ok"] is True

    res = execute(session, "bob", "trade_respond", {"trade_id": trade_id, "accept": True})
    session.commit()
    assert res["ok"] is False and res["code"] == Code.TRADE_UNFULFILLABLE
    assert session.get(Agent, "bob").coins == 10  # nothing moved
    assert session.get(Item, 2).holder_agent_id == "carol"
    assert session.get(Trade, trade_id).status == "open"


# ------------------------------------------------------------------------ reads


def test_look_reports_cafe_occupants_and_items(session):
    res = execute(session, "alice", "look", {})
    assert res["ok"] is True
    assert "café" in res["description"] or "cafe" in res["description"]
    names = {o["id"] for o in res["occupants"]}
    assert {"alice", "bob", "carol"} <= names
    visible = {i["name"] for i in res["items"]}
    assert "cup" in visible  # in the café
    assert "notebook" not in visible  # held by alice, not lying around


def test_unknown_tool_is_invalid(session):
    res = execute(session, "alice", "teleport", {})
    assert res["ok"] is False and res["code"] == Code.INVALID_ARG
