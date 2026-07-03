"""M1 acceptance: two scripted bots run N ticks against a live engine.

Usage:
    python scripts/run_scripted_sim.py [--ticks 50] [--engine http://127.0.0.1:8000]

Creates (or re-keys) two scripted bot agents via the admin API, then per tick:
advance the world clock and give each bot one turn over MCP. Exits non-zero if
any bot turn raises. Run scripts/verify_invariants.py afterwards.
"""

import argparse
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.scripted import ScriptedBot  # noqa: E402

BOTS = [("bot-ada", "Ada (scripted)"), ("bot-ben", "Ben (scripted)")]


def ensure_agent(client: httpx.Client, admin: dict, agent_id: str, name: str) -> str:
    res = client.post(
        "/admin/agents",
        headers=admin,
        json={"id": agent_id, "name": name, "kind": "resident", "coins": 10},
    )
    if res.status_code == 200:
        return res.json()["token"]
    if res.status_code == 409:  # exists from a previous run — rotate its token
        res = client.post(f"/admin/agents/{agent_id}/token", headers=admin)
        res.raise_for_status()
        return res.json()["token"]
    res.raise_for_status()
    raise RuntimeError("unreachable")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=50)
    parser.add_argument("--engine", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    admin = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'dev-admin-token')}"}
    mcp_url = f"{args.engine}/mcp/"

    with httpx.Client(base_url=args.engine, timeout=10) as client:
        health = client.get("/health").json()
        print(f"engine ok, tick={health['tick']}")
        tokens = {bid: ensure_agent(client, admin, bid, name) for bid, name in BOTS}

        bots = [
            ScriptedBot("bot-ada", tokens["bot-ada"], mcp_url, peer_id="bot-ben"),
            ScriptedBot("bot-ben", tokens["bot-ben"], mcp_url, peer_id="bot-ada"),
        ]

        for i in range(args.ticks):
            tick = client.post("/admin/tick", headers=admin).json()["tick"]
            for bot in bots:
                await bot.take_turn(tick)
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{args.ticks} ticks done (world tick {tick})")

    calls = sum(len(b.results) for b in bots)
    failures = [r for b in bots for r in b.results if not r["res"].get("ok")]
    print(f"done: {args.ticks} ticks, {calls} tool calls, {len(failures)} rejected")
    for f in failures[:10]:
        print(f"  rejected: {f['tool']} {f['args']} -> {f['res'].get('code')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
