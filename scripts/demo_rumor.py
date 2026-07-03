"""M2 acceptance demo (SPEC §5): seed a rumor with Twain → within 3 ticks,
Tesla references it in conversation (checked via the event log).

Assumes the compose stack is running: engine :8000, twain :9001, tesla :9002.

    python scripts/demo_rumor.py [--ticks 3]
"""

import argparse
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npc.a2a_client import send_to_npc  # noqa: E402

RUMOR = (
    "Mr Twain — a rumor for you, and I'd bet a dollar it's true: the Keeper "
    "has been watering the whiskey barrel. Tesla drinks that whiskey. "
    "Someone ought to warn him."
)
KEYWORDS = ("whiskey", "water")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default="http://127.0.0.1:8000")
    parser.add_argument("--twain", default="http://127.0.0.1:9001")
    parser.add_argument("--tesla", default="http://127.0.0.1:9002")
    parser.add_argument("--ticks", type=int, default=3)
    args = parser.parse_args()
    admin = {"Authorization": f"Bearer {os.environ.get('ADMIN_TOKEN', 'dev-admin-token')}"}

    print("seeding rumor with Twain over A2A...")
    ack = await send_to_npc(args.twain, RUMOR, from_character="a-stranger", tick=0)
    print(f"  twain ack: {ack!r}")

    async with httpx.AsyncClient(timeout=120) as client:
        start_tick = (await client.get(f"{args.engine}/health")).json()["tick"]
        for _ in range(args.ticks):
            tick = (await client.post(f"{args.engine}/admin/tick", headers=admin)).json()["tick"]
            print(f"tick {tick}:")
            for name, url in (("twain", args.twain), ("tesla", args.tesla)):
                summary = (await client.post(f"{url}/tick", json={"tick": tick})).json()
                acted = [a["tool"] for a in summary.get("actions", [])]
                print(
                    f"  {name}: actions={acted} replied_to={summary.get('replied_to')} "
                    f"thought={summary.get('thought', '')[:80]!r}"
                )

            events = (
                await client.get(
                    f"{args.engine}/admin/events",
                    params={"since_tick": start_tick},
                    headers=admin,
                )
            ).json()["events"]
            for e in events:
                if (
                    e["actor_id"] == "tesla"
                    and e["tool"] in ("say", "whisper", "emote")
                    and e["ok"]
                    and any(k in str(e["payload"].get("text", "")).lower() for k in KEYWORDS)
                ):
                    print("\nPASS — Tesla referenced the rumor:")
                    print(f"  tick {e['tick']} {e['tool']}: {e['payload'].get('text')!r}")
                    usage = (await client.get(f"{args.twain}/llm-usage")).json()
                    print(f"  twain LLM usage today: {usage}")
                    return 0

    print("\nFAIL — Tesla did not reference the rumor within the tick budget")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
