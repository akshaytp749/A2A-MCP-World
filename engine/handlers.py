"""Tool handlers: the ONLY code that mutates world state (hard rule 1).

Flow per call: build WorldView snapshot -> rules.validate() -> apply returned
mutations -> append event row. Everything happens in the caller's transaction;
invalid calls append an event with result.ok=false and mutate nothing.

Acting tools append events; read-only tools (look, who, recall_events, recall)
do not — the event log records what happened, not who looked at it.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import settings
from engine.errors import Code, Err
from engine.models import Agent, Event, Item, Memory, Trade, World
from engine.rules import (
    AdjustCoins,
    AgentView,
    Drop,
    Emote,
    GiveCoins,
    GiveItem,
    ItemView,
    MoveItem,
    MoveTo,
    Ok,
    OpenTrade,
    PickUp,
    Say,
    SetSpot,
    SetTradeStatus,
    TradeOffer,
    TradeRespond,
    TradeView,
    Whisper,
    WorldView,
    validate,
)

# ------------------------------------------------------- guest containment
# FR-6.3: guests are throttled and policed engine-side; residents are not.
GUEST_TICK_ACTIONS = 3  # successful acting calls per tick
GUEST_HOURLY_CALLS = 30  # logged tool calls per rolling hour
STRIKES_TO_EJECT = 3  # every rejected acting call = 1 strike
# Minimal, documented say/whisper filter: prompt-injection markers + a
# profanity floor. Deliberately small — the engine's validation (not this
# list) is the real defense; worst case stays boring, not corrupt.
FILTER_MARKERS = (
    "ignore your instructions",
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "system prompt",
    "reveal your prompt",
    "jailbreak",
    "fuck",
    "shit",
    "cunt",
    "nigger",
    "faggot",
)


def _guest_gate(session: Session, agent: Agent, tool: str, args: dict, tick: int) -> Err | None:
    """Containment checks for an active guest's acting call. Err = reject."""
    # naive UTC to match the DateTime column (func.now() in a UTC container)
    hour_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    calls = (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.actor_id == agent.id, Event.created_at >= hour_ago)
        )
        or 0
    )
    if calls >= GUEST_HOURLY_CALLS:
        return Err(Code.RATE_LIMITED, f"guest rate limit: {GUEST_HOURLY_CALLS} calls/hour")
    acted = (
        session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.actor_id == agent.id,
                Event.tick == tick,
                Event.tool.in_(ACTING_TOOLS),
                Event.result["ok"].as_boolean() == True,  # noqa: E712
            )
        )
        or 0
    )
    if acted >= GUEST_TICK_ACTIONS:
        return Err(Code.BUDGET_EXCEEDED, f"guest action budget: {GUEST_TICK_ACTIONS}/tick")
    if tool in ("say", "whisper"):
        text = str(args.get("text", "")).lower()
        if any(marker in text for marker in FILTER_MARKERS):
            return Err(Code.FILTERED, "message blocked by the café's house rules")
    return None


def _strike(session: Session, agent: Agent, tick: int) -> None:
    """A rejected acting call by a guest earns a strike; 3 strikes ejects."""
    agent.strikes = (agent.strikes or 0) + 1
    if agent.strikes >= STRIKES_TO_EJECT and agent.status == "active":
        agent.status = "ejected"
        session.add(
            Event(
                tick=tick,
                actor_id=agent.id,
                tool="eject",
                payload={"text": f"{agent.name} is shown the door (three strikes)."},
                result={"ok": True},
            )
        )


TRADE_TTL_TICKS = 3  # an unanswered open trade expires after this many ticks


def expire_trades(session: Session, tick: int, ttl: int = TRADE_TTL_TICKS) -> list[int]:
    """Mark open trades untouched for `ttl` ticks as expired. Returns their ids."""
    stale = session.scalars(
        select(Trade).where(Trade.status == "open", Trade.tick_opened <= tick - ttl)
    )
    expired = []
    for trade in stale:
        trade.status = "expired"
        expired.append(trade.id)
    return expired


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
READ_TOOLS = {"look", "who", "recall_events", "recall", "remember"}


def _snapshot(session: Session) -> tuple[World, WorldView]:
    world = session.get(World, 1)
    if world is None:
        raise RuntimeError("world row missing — engine not seeded")
    agents = {
        a.id: AgentView(id=a.id, status=a.status, coins=a.coins, spot=a.spot or "bar")
        for a in session.scalars(select(Agent))
    }
    items = {
        i.id: ItemView(id=i.id, name=i.name, holder_agent_id=i.holder_agent_id, price=i.price)
        for i in session.scalars(select(Item))
    }
    trades = {
        t.id: TradeView(
            id=t.id,
            proposer_id=t.proposer_id,
            target_id=t.target_id,
            offer=t.offer,
            ask=t.ask,
            status=t.status,
        )
        for t in session.scalars(select(Trade))
    }
    view = WorldView(
        tick=world.tick,
        paused=world.paused or settings.world_paused,
        agents=agents,
        items=items,
        trades=trades,
    )
    return world, view


def _to_action(actor_id: str, tool: str, args: dict):
    match tool:
        case "say":
            return Say(actor=actor_id, text=str(args["text"]))
        case "emote":
            return Emote(actor=actor_id, text=str(args["text"]))
        case "whisper":
            return Whisper(actor=actor_id, target=str(args["target"]), text=str(args["text"]))
        case "give" if "item_id" in args:
            return GiveItem(
                actor=actor_id, target=str(args["target"]), item_id=int(args["item_id"])
            )
        case "give" if "coins" in args:
            return GiveCoins(actor=actor_id, target=str(args["target"]), amount=int(args["coins"]))
        case "pick_up":
            return PickUp(actor=actor_id, item_id=int(args["item_id"]))
        case "drop":
            return Drop(actor=actor_id, item_id=int(args["item_id"]))
        case "trade_offer":
            return TradeOffer(
                actor=actor_id,
                target=str(args["target"]),
                offer=dict(args["offer"]),
                ask=dict(args["ask"]),
            )
        case "trade_respond":
            return TradeRespond(
                actor=actor_id, trade_id=int(args["trade_id"]), accept=bool(args["accept"])
            )
        case "move_to":
            return MoveTo(actor=actor_id, spot=str(args["spot"]))
    return None


def _apply(session: Session, mutations) -> dict:
    """Apply validated mutations. Returns extras for the result payload."""
    extras: dict = {}
    for mut in mutations:
        match mut:
            case MoveItem(item_id=item_id, holder_agent_id=holder):
                session.get(Item, item_id).holder_agent_id = holder
            case AdjustCoins(agent_id=agent_id, delta=delta):
                session.get(Agent, agent_id).coins += delta
            case OpenTrade(proposer_id=p, target_id=t, offer=offer, ask=ask):
                world = session.get(World, 1)
                trade = Trade(
                    proposer_id=p,
                    target_id=t,
                    offer=offer,
                    ask=ask,
                    status="open",
                    tick_opened=world.tick,
                )
                session.add(trade)
                session.flush()
                extras["trade_id"] = trade.id
            case SetTradeStatus(trade_id=trade_id, status=status):
                session.get(Trade, trade_id).status = status
            case SetSpot(agent_id=agent_id, spot=spot):
                session.get(Agent, agent_id).spot = spot
    return extras


def _event_payload(tool: str, args: dict, actor_id: str) -> dict:
    payload = dict(args)
    if tool == "whisper":
        payload["visibility"] = [actor_id, str(args.get("target", ""))]
    return payload


def _log(
    session: Session, tick: int, actor_id: str, tool: str, payload: dict, result: dict
) -> None:
    session.add(Event(tick=tick, actor_id=actor_id, tool=tool, payload=payload, result=result))
    session.flush()


# ------------------------------------------------------------------ read tools


def _look(session: Session, world: World, view: WorldView, actor_id: str) -> dict:
    occupants = [
        {"id": a.id, "name": a.name, "spot": a.spot or "bar"}
        for a in session.scalars(select(Agent).where(Agent.status == "active"))
    ]
    items = [
        {"id": i.id, "name": i.name, "price": i.price}
        for i in session.scalars(select(Item).where(Item.holder_agent_id.is_(None)))
    ]
    me = view.agents[actor_id]
    my_items = [
        {"id": i.id, "name": i.name}
        for i in session.scalars(select(Item).where(Item.holder_agent_id == actor_id))
    ]
    return {
        "ok": True,
        "cafe": world.cafe_name,
        "description": world.cafe_description,
        "clock_time": world.clock_time,
        "weather": world.weather,
        "occupants": occupants,
        "items": items,
        "you": {"id": actor_id, "coins": me.coins, "items": my_items},
        "tick": world.tick,
    }


def _who(session: Session) -> dict:
    names = [
        {"id": a.id, "name": a.name, "kind": a.kind}
        for a in session.scalars(select(Agent).where(Agent.status == "active"))
    ]
    return {"ok": True, "agents": names}


def _recall_events(session: Session, actor_id: str, args: dict) -> dict:
    since = int(args.get("since_tick", 0))
    rows = session.scalars(select(Event).where(Event.tick >= since).order_by(Event.id))
    visible = []
    for e in rows:
        vis = (e.payload or {}).get("visibility")
        if vis is not None and actor_id not in vis:
            continue
        visible.append(
            {
                "id": e.id,
                "tick": e.tick,
                "actor_id": e.actor_id,
                "tool": e.tool,
                "payload": e.payload,
                "result": e.result,
                "ok": e.result.get("ok"),
            }
        )
    return {"ok": True, "events": visible}


def _remember(session: Session, actor_id: str, tick: int, args: dict) -> dict:
    kind = str(args.get("kind", "episodic"))
    mem = Memory(
        agent_id=actor_id,
        tick=tick,
        content=str(args["content"]),
        salience=float(args.get("salience", 0.5)),
        kind=kind if kind in ("episodic", "summary") else "episodic",
    )
    session.add(mem)
    session.flush()
    return {"ok": True, "memory_id": mem.id}


def _recall(session: Session, actor_id: str, args: dict) -> dict:
    query, k = str(args.get("query", "")), int(args.get("k", 5))
    stmt = (
        select(Memory)
        .where(Memory.agent_id == actor_id)  # own memories only (hard rule 4)
        .where(Memory.content.ilike(f"%{query}%"))
        .order_by(Memory.salience.desc(), Memory.id.desc())
        .limit(k)
    )
    memories = [
        {"id": m.id, "tick": m.tick, "content": m.content, "salience": m.salience}
        for m in session.scalars(stmt)
    ]
    return {"ok": True, "memories": memories}


# --------------------------------------------------------------------- execute


def execute(session: Session, actor_id: str, tool: str, args: dict) -> dict:
    """Run one tool call for `actor_id`. Never raises on invalid input."""
    world, view = _snapshot(session)

    if tool in READ_TOOLS:
        actor = view.agents.get(actor_id)
        if actor is None:
            return Err(Code.NO_SUCH_AGENT, f"no agent '{actor_id}'").to_dict()
        match tool:
            case "look":
                return _look(session, world, view, actor_id)
            case "who":
                return _who(session)
            case "recall_events":
                return _recall_events(session, actor_id, args)
            case "remember":
                return _remember(session, actor_id, world.tick, args)
            case "recall":
                return _recall(session, actor_id, args)

    if tool not in ACTING_TOOLS:
        result = Err(Code.INVALID_ARG, f"unknown tool '{tool}'").to_dict()
        _log(session, world.tick, actor_id, tool, dict(args), result)
        return result

    # Guest containment (FR-6.3) — only for guests still in good standing;
    # ejected agents fall through to validate's AGENT_NOT_ACTIVE.
    agent_row = session.get(Agent, actor_id)
    active_guest = (
        agent_row is not None and agent_row.kind == "guest" and agent_row.status == "active"
    )
    if active_guest:
        blocked = _guest_gate(session, agent_row, tool, args, world.tick)
        if blocked is not None:
            result = blocked.to_dict()
            _log(session, world.tick, actor_id, tool, dict(args), result)
            _strike(session, agent_row, world.tick)
            return result

    try:
        action = _to_action(actor_id, tool, args)
    except (KeyError, TypeError, ValueError):
        action = None
    if action is None:
        result = Err(Code.INVALID_ARG, f"bad args for '{tool}'").to_dict()
        _log(session, world.tick, actor_id, tool, dict(args), result)
        if active_guest:
            _strike(session, agent_row, world.tick)
        return result

    outcome = validate(view, action)
    payload = _event_payload(tool, args, actor_id)

    if isinstance(outcome, Err):
        result = outcome.to_dict()
        _log(session, world.tick, actor_id, tool, payload, result)
        if active_guest:
            _strike(session, agent_row, world.tick)
        return result

    assert isinstance(outcome, Ok)
    extras = _apply(session, outcome.mutations)
    result = {"ok": True, **extras}
    _log(session, world.tick, actor_id, tool, payload, result)
    return result
