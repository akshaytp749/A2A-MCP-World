"""Pure-rules tests (TDD — committed before engine/rules.py exists).

engine.rules must stay pure: (state, action) -> Ok(mutations) | Err(code, msg).
No I/O, no sessions, no clocks.
"""

from engine.errors import Code, Err
from engine.rules import (
    AdjustCoins,
    AgentView,
    Drop,
    Emote,
    GiveCoins,
    GiveItem,
    ItemView,
    MoveItem,
    Ok,
    OpenTrade,
    PickUp,
    Say,
    SetTradeStatus,
    TradeOffer,
    TradeRespond,
    TradeView,
    Whisper,
    WorldView,
    validate,
)


def world(agents=None, items=None, trades=None, paused=False) -> WorldView:
    return WorldView(
        tick=1,
        paused=paused,
        agents={a.id: a for a in (agents or [])},
        items={i.id: i for i in (items or [])},
        trades={t.id: t for t in (trades or [])},
    )


def alice(coins=10) -> AgentView:
    return AgentView(id="alice", status="active", coins=coins)


def bob(coins=10, status="active") -> AgentView:
    return AgentView(id="bob", status=status, coins=coins)


# ---------------------------------------------------------------- say / whisper / emote


def test_say_ok_no_mutations():
    res = validate(world([alice()]), Say(actor="alice", text="hello"))
    assert isinstance(res, Ok)
    assert res.mutations == ()


def test_emote_ok():
    res = validate(world([alice()]), Emote(actor="alice", text="polishes a mug"))
    assert isinstance(res, Ok)


def test_whisper_to_absent_target_rejected():
    res = validate(world([alice()]), Whisper(actor="alice", target="ghost", text="psst"))
    assert isinstance(res, Err) and res.code == Code.NO_SUCH_AGENT


def test_whisper_to_ejected_target_rejected():
    res = validate(
        world([alice(), bob(status="ejected")]),
        Whisper(actor="alice", target="bob", text="psst"),
    )
    assert isinstance(res, Err) and res.code == Code.AGENT_NOT_ACTIVE


def test_whisper_self_rejected():
    res = validate(world([alice()]), Whisper(actor="alice", target="alice", text="hm"))
    assert isinstance(res, Err) and res.code == Code.SELF_TARGET


def test_unknown_actor_rejected():
    res = validate(world([alice()]), Say(actor="nobody", text="hi"))
    assert isinstance(res, Err) and res.code == Code.NO_SUCH_AGENT


# ---------------------------------------------------------------------------- give


def test_give_item_not_held_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id=None)  # in the café, not held
    res = validate(world([alice(), bob()], [cup]), GiveItem(actor="alice", target="bob", item_id=1))
    assert isinstance(res, Err) and res.code == Code.NOT_HOLDER


def test_give_nonexistent_item_rejected():
    res = validate(world([alice(), bob()]), GiveItem(actor="alice", target="bob", item_id=99))
    assert isinstance(res, Err) and res.code == Code.NO_SUCH_ITEM


def test_give_item_happy_path():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(world([alice(), bob()], [cup]), GiveItem(actor="alice", target="bob", item_id=1))
    assert isinstance(res, Ok)
    assert MoveItem(item_id=1, holder_agent_id="bob") in res.mutations


def test_give_to_missing_target_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(world([alice()], [cup]), GiveItem(actor="alice", target="bob", item_id=1))
    assert isinstance(res, Err) and res.code == Code.NO_SUCH_AGENT


def test_give_to_self_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(world([alice()], [cup]), GiveItem(actor="alice", target="alice", item_id=1))
    assert isinstance(res, Err) and res.code == Code.SELF_TARGET


def test_give_coins_happy_path():
    res = validate(world([alice(coins=5), bob()]), GiveCoins(actor="alice", target="bob", amount=5))
    assert isinstance(res, Ok)
    assert AdjustCoins(agent_id="alice", delta=-5) in res.mutations
    assert AdjustCoins(agent_id="bob", delta=5) in res.mutations


def test_give_coins_insufficient_rejected():
    res = validate(world([alice(coins=4), bob()]), GiveCoins(actor="alice", target="bob", amount=5))
    assert isinstance(res, Err) and res.code == Code.INSUFFICIENT_COINS


def test_give_coins_nonpositive_rejected():
    res = validate(world([alice(), bob()]), GiveCoins(actor="alice", target="bob", amount=0))
    assert isinstance(res, Err) and res.code == Code.INVALID_ARG


# ---------------------------------------------------------------------- pick_up / drop


def test_pick_up_free_item():
    cup = ItemView(id=1, name="cup", holder_agent_id=None)
    res = validate(world([alice()], [cup]), PickUp(actor="alice", item_id=1))
    assert isinstance(res, Ok)
    assert MoveItem(item_id=1, holder_agent_id="alice") in res.mutations


def test_pick_up_held_item_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id="bob")
    res = validate(world([alice(), bob()], [cup]), PickUp(actor="alice", item_id=1))
    assert isinstance(res, Err) and res.code == Code.ITEM_NOT_AVAILABLE


def test_drop_not_held_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id=None)
    res = validate(world([alice()], [cup]), Drop(actor="alice", item_id=1))
    assert isinstance(res, Err) and res.code == Code.NOT_HOLDER


def test_drop_happy_path():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(world([alice()], [cup]), Drop(actor="alice", item_id=1))
    assert isinstance(res, Ok)
    assert MoveItem(item_id=1, holder_agent_id=None) in res.mutations


# ---------------------------------------------------------------------------- trades


def _open_trade(offer=None, ask=None) -> TradeView:
    return TradeView(
        id=7,
        proposer_id="alice",
        target_id="bob",
        offer=offer or {"coins": 0, "items": [1]},
        ask=ask or {"coins": 3, "items": []},
        status="open",
    )


def test_trade_offer_happy_path():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(
        world([alice(), bob()], [cup]),
        TradeOffer(
            actor="alice",
            target="bob",
            offer={"coins": 0, "items": [1]},
            ask={"coins": 3, "items": []},
        ),
    )
    assert isinstance(res, Ok)
    assert (
        OpenTrade(
            proposer_id="alice",
            target_id="bob",
            offer={"coins": 0, "items": [1]},
            ask={"coins": 3, "items": []},
        )
        in res.mutations
    )


def test_trade_offer_unowned_item_rejected():
    cup = ItemView(id=1, name="cup", holder_agent_id="bob")
    res = validate(
        world([alice(), bob()], [cup]),
        TradeOffer(
            actor="alice",
            target="bob",
            offer={"coins": 0, "items": [1]},
            ask={"coins": 1, "items": []},
        ),
    )
    assert isinstance(res, Err) and res.code == Code.NOT_HOLDER


def test_trade_offer_overdrawn_coins_rejected():
    res = validate(
        world([alice(coins=2), bob()]),
        TradeOffer(
            actor="alice",
            target="bob",
            offer={"coins": 5, "items": []},
            ask={"coins": 0, "items": []},
        ),
    )
    assert isinstance(res, Err) and res.code == Code.INSUFFICIENT_COINS


def test_trade_accept_happy_path_settles_both_sides():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    trade = _open_trade()
    res = validate(
        world([alice(), bob(coins=5)], [cup], [trade]),
        TradeRespond(actor="bob", trade_id=7, accept=True),
    )
    assert isinstance(res, Ok)
    assert MoveItem(item_id=1, holder_agent_id="bob") in res.mutations
    assert AdjustCoins(agent_id="bob", delta=-3) in res.mutations
    assert AdjustCoins(agent_id="alice", delta=3) in res.mutations
    assert SetTradeStatus(trade_id=7, status="accepted") in res.mutations


def test_trade_decline():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(
        world([alice(), bob()], [cup], [_open_trade()]),
        TradeRespond(actor="bob", trade_id=7, accept=False),
    )
    assert isinstance(res, Ok)
    assert res.mutations == (SetTradeStatus(trade_id=7, status="declined"),)


def test_trade_respond_by_non_target_rejected():
    res = validate(
        world(
            [alice(), bob()], [ItemView(id=1, name="cup", holder_agent_id="alice")], [_open_trade()]
        ),
        TradeRespond(actor="alice", trade_id=7, accept=True),
    )
    assert isinstance(res, Err) and res.code == Code.NOT_TRADE_TARGET


def test_trade_respond_missing_trade_rejected():
    res = validate(world([alice(), bob()]), TradeRespond(actor="bob", trade_id=99, accept=True))
    assert isinstance(res, Err) and res.code == Code.NO_SUCH_TRADE


def test_trade_respond_settled_trade_rejected():
    settled = TradeView(
        id=7,
        proposer_id="alice",
        target_id="bob",
        offer={"coins": 0, "items": []},
        ask={"coins": 0, "items": []},
        status="declined",
    )
    res = validate(
        world([alice(), bob()], trades=[settled]),
        TradeRespond(actor="bob", trade_id=7, accept=True),
    )
    assert isinstance(res, Err) and res.code == Code.TRADE_NOT_OPEN


def test_trade_accept_unfulfillable_when_offer_item_gone():
    # Alice offered the cup but no longer holds it at accept time.
    cup = ItemView(id=1, name="cup", holder_agent_id=None)
    res = validate(
        world([alice(), bob(coins=5)], [cup], [_open_trade()]),
        TradeRespond(actor="bob", trade_id=7, accept=True),
    )
    assert isinstance(res, Err) and res.code == Code.TRADE_UNFULFILLABLE


def test_trade_accept_unfulfillable_when_target_cannot_pay():
    cup = ItemView(id=1, name="cup", holder_agent_id="alice")
    res = validate(
        world([alice(), bob(coins=1)], [cup], [_open_trade()]),  # ask is 3 coins
        TradeRespond(actor="bob", trade_id=7, accept=True),
    )
    assert isinstance(res, Err) and res.code == Code.TRADE_UNFULFILLABLE


# ---------------------------------------------------------------------------- paused


def test_paused_world_blocks_all_acting_tools():
    w = world([alice(), bob()], paused=True)
    for action in (
        Say(actor="alice", text="hi"),
        GiveCoins(actor="alice", target="bob", amount=1),
        PickUp(actor="alice", item_id=1),
    ):
        res = validate(w, action)
        assert isinstance(res, Err) and res.code == Code.WORLD_PAUSED
