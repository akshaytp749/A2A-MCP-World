"""Deterministic scripted bots (P1 — no LLMs).

A ScriptedBot acts on the world ONLY through the engine's MCP tools over
streamable HTTP, exactly like any future LLM NPC or guest (hard rules 1–2).
This machinery is reused later as the admission gate's probe bots (ADR 4).

Per-turn policy (deterministic, cycles with the tick number):
  every turn: respond to any open trade addressed to me (accept if affordable)
  tick % 6 == 0: look + say          == 3: drop a held item
  tick % 6 == 1: pick up a café item == 4: trade_offer 1 coin for 1 coin
  tick % 6 == 2: give peer 1 coin    == 5: whisper + emote
"""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _parse(result: Any) -> dict:
    """Normalize a CallToolResult into the handler's plain dict."""
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        inner = sc.get("result")
        return inner if isinstance(inner, dict) else sc
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {"ok": False, "code": "CLIENT_PARSE", "msg": "unparseable tool result"}


class ScriptedBot:
    def __init__(self, agent_id: str, token: str, mcp_url: str, peer_id: str):
        self.id = agent_id
        self.token = token
        self.mcp_url = mcp_url
        self.peer_id = peer_id
        self.results: list[dict] = []  # every tool result, for the sim to inspect

    async def take_turn(self, tick: int) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with (
            streamablehttp_client(self.mcp_url, headers=headers) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(tool: str, args: dict | None = None) -> dict:
                res = _parse(await session.call_tool(tool, args or {}))
                self.results.append({"tool": tool, "args": args or {}, "res": res})
                return res

            me = await call("look")
            await self._respond_to_trades(call, me, tick)

            step = tick % 6
            if step == 0:
                await call("say", {"text": f"[{self.id}] tick {tick}: all well."})
            elif step == 1:
                free = me.get("items", [])
                if free:
                    await call("pick_up", {"item_id": free[0]["id"]})
            elif step == 2:
                if me.get("you", {}).get("coins", 0) >= 1:
                    await call("give", {"target": self.peer_id, "coins": 1})
            elif step == 3:
                held = me.get("you", {}).get("items", [])
                if held:
                    await call("drop", {"item_id": held[0]["id"]})
            elif step == 4:
                if me.get("you", {}).get("coins", 0) >= 1:
                    await call(
                        "trade_offer",
                        {
                            "target": self.peer_id,
                            "offer": {"coins": 1, "items": []},
                            "ask": {"coins": 1, "items": []},
                        },
                    )
            elif step == 5:
                await call(
                    "whisper",
                    {"target": self.peer_id, "text": f"[{self.id}] between us: tick {tick}."},
                )
                await call("emote", {"text": "taps the bar thoughtfully"})

    async def _respond_to_trades(self, call, me: dict, tick: int) -> None:
        """Accept open trades addressed to me (decline if I can't afford the ask)."""
        events = (await call("recall_events", {"since_tick": max(0, tick - 1)})).get("events", [])
        responded = {
            e["payload"].get("trade_id")
            for e in events
            if e["tool"] == "trade_respond" and e["actor_id"] == self.id
        }
        my_coins = me.get("you", {}).get("coins", 0)
        for e in events:
            if e["tool"] != "trade_offer" or e["payload"].get("target") != self.id:
                continue
            if not e.get("result", {}).get("ok"):
                continue
            trade_id = e["result"].get("trade_id")
            if trade_id is None or trade_id in responded:
                continue
            affordable = int(e["payload"].get("ask", {}).get("coins", 0)) <= my_coins
            res = await call("trade_respond", {"trade_id": trade_id, "accept": affordable})
            if res.get("ok") or res.get("code") in ("TRADE_NOT_OPEN", "NO_SUCH_TRADE"):
                responded.add(trade_id)
