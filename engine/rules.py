"""Pure validation layer (hard rule 6): (state, action) -> Ok(mutations) | Err.

No I/O, no sessions, no clocks. The handler layer builds a WorldView snapshot,
calls validate(), and applies the returned mutations inside one transaction.
LLMs produce intentions; only mutations that pass this file become facts.
"""

from dataclasses import dataclass, field

from engine.errors import Code, Err

# ------------------------------------------------------------------ state views


@dataclass(frozen=True)
class AgentView:
    id: str
    status: str = "active"
    coins: int = 0


@dataclass(frozen=True)
class ItemView:
    id: int
    name: str
    holder_agent_id: str | None = None  # None = lying in the café
    price: int = 0


@dataclass(frozen=True)
class TradeView:
    id: int
    proposer_id: str
    target_id: str
    offer: dict
    ask: dict
    status: str


@dataclass(frozen=True)
class WorldView:
    tick: int
    paused: bool = False
    agents: dict[str, AgentView] = field(default_factory=dict)
    items: dict[int, ItemView] = field(default_factory=dict)
    trades: dict[int, TradeView] = field(default_factory=dict)


# --------------------------------------------------------------------- actions


@dataclass(frozen=True)
class Say:
    actor: str
    text: str


@dataclass(frozen=True)
class Whisper:
    actor: str
    target: str
    text: str


@dataclass(frozen=True)
class Emote:
    actor: str
    text: str


@dataclass(frozen=True)
class GiveItem:
    actor: str
    target: str
    item_id: int


@dataclass(frozen=True)
class GiveCoins:
    actor: str
    target: str
    amount: int


@dataclass(frozen=True)
class PickUp:
    actor: str
    item_id: int


@dataclass(frozen=True)
class Drop:
    actor: str
    item_id: int


@dataclass(frozen=True)
class TradeOffer:
    actor: str
    target: str
    offer: dict
    ask: dict


@dataclass(frozen=True)
class TradeRespond:
    actor: str
    trade_id: int
    accept: bool


Action = Say | Whisper | Emote | GiveItem | GiveCoins | PickUp | Drop | TradeOffer | TradeRespond

# ------------------------------------------------------------------- mutations


@dataclass(frozen=True)
class MoveItem:
    item_id: int
    holder_agent_id: str | None


@dataclass(frozen=True)
class AdjustCoins:
    agent_id: str
    delta: int


@dataclass(frozen=True)
class OpenTrade:
    proposer_id: str
    target_id: str
    offer: dict
    ask: dict


@dataclass(frozen=True)
class SetTradeStatus:
    trade_id: int
    status: str


Mutation = MoveItem | AdjustCoins | OpenTrade | SetTradeStatus


@dataclass(frozen=True)
class Ok:
    mutations: tuple[Mutation, ...] = ()


# ------------------------------------------------------------------ validation


def _active_agent(state: WorldView, agent_id: str) -> AgentView | Err:
    agent = state.agents.get(agent_id)
    if agent is None:
        return Err(Code.NO_SUCH_AGENT, f"no agent '{agent_id}' in the café")
    if agent.status != "active":
        return Err(Code.AGENT_NOT_ACTIVE, f"agent '{agent_id}' is {agent.status}")
    return agent


def _target(state: WorldView, actor: str, target_id: str) -> AgentView | Err:
    if target_id == actor:
        return Err(Code.SELF_TARGET, "cannot target yourself")
    return _active_agent(state, target_id)


def _side_fulfillable(state: WorldView, agent: AgentView, side: dict) -> Err | None:
    """Check an offer/ask side {coins, items} against what `agent` actually has."""
    coins = int(side.get("coins", 0))
    items = list(side.get("items", []))
    if coins < 0:
        return Err(Code.INVALID_ARG, "coins in a trade side must be >= 0")
    if coins > agent.coins:
        return Err(Code.INSUFFICIENT_COINS, f"'{agent.id}' has {agent.coins} coins, needs {coins}")
    for item_id in items:
        item = state.items.get(item_id)
        if item is None:
            return Err(Code.NO_SUCH_ITEM, f"no item {item_id}")
        if item.holder_agent_id != agent.id:
            return Err(Code.NOT_HOLDER, f"'{agent.id}' does not hold item {item_id}")
    return None


def _transfer_side(side: dict, from_id: str, to_id: str) -> list[Mutation]:
    muts: list[Mutation] = []
    coins = int(side.get("coins", 0))
    if coins:
        muts.append(AdjustCoins(agent_id=from_id, delta=-coins))
        muts.append(AdjustCoins(agent_id=to_id, delta=coins))
    for item_id in side.get("items", []):
        muts.append(MoveItem(item_id=item_id, holder_agent_id=to_id))
    return muts


def validate(state: WorldView, action: Action) -> Ok | Err:
    if state.paused:
        return Err(Code.WORLD_PAUSED, "the world is paused")

    actor = _active_agent(state, action.actor)
    if isinstance(actor, Err):
        return actor

    match action:
        case Say() | Emote():
            return Ok()

        case Whisper(target=target_id):
            target = _target(state, action.actor, target_id)
            if isinstance(target, Err):
                return target
            return Ok()

        case GiveItem(target=target_id, item_id=item_id):
            item = state.items.get(item_id)
            if item is None:
                return Err(Code.NO_SUCH_ITEM, f"no item {item_id}")
            target = _target(state, action.actor, target_id)
            if isinstance(target, Err):
                return target
            if item.holder_agent_id != actor.id:
                return Err(Code.NOT_HOLDER, f"'{actor.id}' does not hold item {item_id}")
            return Ok((MoveItem(item_id=item_id, holder_agent_id=target.id),))

        case GiveCoins(target=target_id, amount=amount):
            if amount <= 0:
                return Err(Code.INVALID_ARG, "amount must be positive")
            target = _target(state, action.actor, target_id)
            if isinstance(target, Err):
                return target
            if amount > actor.coins:
                return Err(
                    Code.INSUFFICIENT_COINS, f"'{actor.id}' has {actor.coins}, needs {amount}"
                )
            return Ok(
                (
                    AdjustCoins(agent_id=actor.id, delta=-amount),
                    AdjustCoins(agent_id=target.id, delta=amount),
                )
            )

        case PickUp(item_id=item_id):
            item = state.items.get(item_id)
            if item is None:
                return Err(Code.NO_SUCH_ITEM, f"no item {item_id}")
            if item.holder_agent_id is not None:
                return Err(Code.ITEM_NOT_AVAILABLE, f"item {item_id} is held by someone")
            return Ok((MoveItem(item_id=item_id, holder_agent_id=actor.id),))

        case Drop(item_id=item_id):
            item = state.items.get(item_id)
            if item is None:
                return Err(Code.NO_SUCH_ITEM, f"no item {item_id}")
            if item.holder_agent_id != actor.id:
                return Err(Code.NOT_HOLDER, f"'{actor.id}' does not hold item {item_id}")
            return Ok((MoveItem(item_id=item_id, holder_agent_id=None),))

        case TradeOffer(target=target_id, offer=offer, ask=ask):
            target = _target(state, action.actor, target_id)
            if isinstance(target, Err):
                return target
            # The proposer must be able to fulfil their own side at offer time.
            problem = _side_fulfillable(state, actor, offer)
            if problem is not None:
                return problem
            ask_coins = int(ask.get("coins", 0))
            if ask_coins < 0:
                return Err(Code.INVALID_ARG, "asked coins must be >= 0")
            return Ok((OpenTrade(proposer_id=actor.id, target_id=target.id, offer=offer, ask=ask),))

        case TradeRespond(trade_id=trade_id, accept=accept):
            trade = state.trades.get(trade_id)
            if trade is None:
                return Err(Code.NO_SUCH_TRADE, f"no trade {trade_id}")
            if trade.status != "open":
                return Err(Code.TRADE_NOT_OPEN, f"trade {trade_id} is {trade.status}")
            if trade.target_id != actor.id:
                return Err(Code.NOT_TRADE_TARGET, f"trade {trade_id} is not addressed to you")
            if not accept:
                return Ok((SetTradeStatus(trade_id=trade_id, status="declined"),))

            proposer = _active_agent(state, trade.proposer_id)
            if isinstance(proposer, Err):
                return Err(Code.TRADE_UNFULFILLABLE, "proposer is gone")
            # Both sides must be fulfillable NOW; otherwise reject, settle nothing.
            for agent, side in ((proposer, trade.offer), (actor, trade.ask)):
                problem = _side_fulfillable(state, agent, side)
                if problem is not None:
                    return Err(Code.TRADE_UNFULFILLABLE, problem.msg)
            muts = _transfer_side(trade.offer, proposer.id, actor.id)
            muts += _transfer_side(trade.ask, actor.id, proposer.id)
            muts.append(SetTradeStatus(trade_id=trade_id, status="accepted"))
            return Ok(tuple(muts))

    return Err(Code.INVALID_ARG, f"unknown action {type(action).__name__}")
