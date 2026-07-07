"""Push the code's default prompts into Langfuse prompt management (one-time).

After this, the prompts can be edited/versioned in the Langfuse UI and picked
up at runtime (llm/prompts.managed) without a redeploy; the in-code copies stay
as the offline fallback. Safe to re-run — it creates a new version each time.

    python scripts/seed_langfuse_prompts.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for line in Path(".env").read_text(encoding="utf-8").splitlines():  # noqa: E402
    line = line.split("#")[0].strip()
    if line and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from npc.mind import PLAN_SCHEMA  # noqa: E402
from scripts.run_eval import JUDGE_PROMPT  # noqa: E402

PROMPTS = {
    "npc-plan-schema": PLAN_SCHEMA,
    "coherence-judge": JUDGE_PROMPT,
}


def main() -> int:
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("no LANGFUSE_PUBLIC_KEY — nothing to seed")
        return 1
    from langfuse import get_client

    lf = get_client()
    for name, text in PROMPTS.items():
        lf.create_prompt(name=name, prompt=text, labels=["production"], type="text")
        print(f"seeded {name} ({len(text)} chars)")
    lf.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
