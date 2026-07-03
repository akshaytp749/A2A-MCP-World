"""MCP tool surface (ARCHITECTURE §4). Same surface for every agent tier.

Actor identity comes from the bearer token — tools never take an actor arg.
All state changes flow through engine.handlers.execute inside one transaction.
"""

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from engine.auth import resolve_actor
from engine.db import session_scope
from engine.errors import Code, Err


def _call(ctx: Context, tool: str, args: dict[str, Any]) -> dict:
    from engine.handlers import execute  # local import keeps module import cheap

    request = ctx.request_context.request
    authorization = request.headers.get("authorization") if request else None
    with session_scope() as session:
        actor_id = resolve_actor(session, authorization)
        if actor_id is None:
            return Err(Code.UNAUTHORIZED, "missing or unknown bearer token").to_dict()
        return execute(session, actor_id, tool, args)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "world-engine",
        instructions="The Interval world engine. All actions happen in one café room.",
        stateless_http=True,
        streamable_http_path="/",
    )

    @mcp.tool(description="Look around the café: description, occupants, visible items.")
    def look(ctx: Context) -> dict:
        return _call(ctx, "look", {})

    @mcp.tool(description="List agents present in the café (names only).")
    def who(ctx: Context) -> dict:
        return _call(ctx, "who", {})

    @mcp.tool(description="Say something aloud to everyone in the café.")
    def say(text: str, ctx: Context = None) -> dict:
        return _call(ctx, "say", {"text": text})

    @mcp.tool(description="Whisper privately to one present agent.")
    def whisper(target: str, text: str, ctx: Context = None) -> dict:
        return _call(ctx, "whisper", {"target": target, "text": text})

    @mcp.tool(description="Perform a visible non-verbal action, e.g. 'polishes a mug'.")
    def emote(text: str, ctx: Context = None) -> dict:
        return _call(ctx, "emote", {"text": text})

    @mcp.tool(description="Give an item you hold, or coins, to a present agent.")
    def give(
        target: str, item_id: int | None = None, coins: int | None = None, ctx: Context = None
    ) -> dict:
        args: dict[str, Any] = {"target": target}
        if item_id is not None:
            args["item_id"] = item_id
        if coins is not None:
            args["coins"] = coins
        return _call(ctx, "give", args)

    @mcp.tool(description="Pick up an item lying in the café.")
    def pick_up(item_id: int, ctx: Context = None) -> dict:
        return _call(ctx, "pick_up", {"item_id": item_id})

    @mcp.tool(description="Drop an item you hold.")
    def drop(item_id: int, ctx: Context = None) -> dict:
        return _call(ctx, "drop", {"item_id": item_id})

    @mcp.tool(description="Offer a trade: offer/ask are {coins:int, items:[item_id]}.")
    def trade_offer(target: str, offer: dict, ask: dict, ctx: Context = None) -> dict:
        return _call(ctx, "trade_offer", {"target": target, "offer": offer, "ask": ask})

    @mcp.tool(description="Accept or decline a trade addressed to you.")
    def trade_respond(trade_id: int, accept: bool, ctx: Context = None) -> dict:
        return _call(ctx, "trade_respond", {"trade_id": trade_id, "accept": accept})

    @mcp.tool(description="Read the event log visible to you since a tick.")
    def recall_events(since_tick: int = 0, ctx: Context = None) -> dict:
        return _call(ctx, "recall_events", {"since_tick": since_tick})

    @mcp.tool(description="Store a memory (your own memory only).")
    def remember(content: str, salience: float = 0.5, ctx: Context = None) -> dict:
        return _call(ctx, "remember", {"content": content, "salience": salience})

    @mcp.tool(description="Search your own memories.")
    def recall(query: str, k: int = 5, ctx: Context = None) -> dict:
        return _call(ctx, "recall", {"query": query, "k": k})

    return mcp
