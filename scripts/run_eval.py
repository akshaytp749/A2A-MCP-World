"""Coherence eval (FR-7.3): LLM-judge scores persona consistency + memory accuracy.

For each resident: sample their recent utterances from the event log and their
stored memories, ask the judge (mid tier via llm/router.py) whether the words
match the persona and contradict any memory. Writes an eval_runs row.

    python scripts/run_eval.py [--per-npc 12]

Exit 0 if average consistency >= 70 (M3 target), else 1.
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for line in Path(".env").read_text(encoding="utf-8").splitlines():  # noqa: E402
    line = line.split("#")[0].strip()
    if line and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from engine.models import EvalRun, Event, Memory  # noqa: E402
from llm.router import LLMRouter  # noqa: E402
from npc.config import load_personas  # noqa: E402
from npc.mind import _extract_json  # noqa: E402

JUDGE_PROMPT = """You are an evaluator for a persistent roleplay world.

PERSONA SPEC (what this character should sound like and believe):
{persona}

THEIR STORED MEMORIES:
{memories}

THEIR RECENT UTTERANCES (public says/whispers from the event log):
{utterances}

Score 0-100:
- persona_consistency: voice, era, temperament, claims match the spec. Deduct
  for modern idioms, out-of-character knowledge, generic assistant tone.
- memory_accuracy: utterances don't contradict stored memories or the spec's
  biography. Deduct only for actual contradictions, not omissions.

Respond with ONLY JSON: {{"persona_consistency": int, "memory_accuracy": int,
"notes": "one sentence"}}"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-npc", type=int, default=12)
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL", "sqlite:///./interval.db"))
    args = parser.parse_args()

    personas = load_personas("world/seed/npcs")
    router = LLMRouter()
    engine = create_engine(args.db)

    results = {}
    with Session(engine) as session:
        for npc_id, cfg in personas.items():
            utterances = list(
                session.scalars(
                    select(Event)
                    .where(Event.actor_id == npc_id, Event.tool.in_(["say", "whisper"]))
                    .order_by(Event.id.desc())
                    .limit(args.per_npc)
                )
            )
            if len(utterances) < 3:
                continue  # not enough material to judge
            memories = list(
                session.scalars(
                    select(Memory)
                    .where(Memory.agent_id == npc_id)
                    .order_by(Memory.salience.desc())
                    .limit(10)
                )
            )
            prompt = JUDGE_PROMPT.format(
                persona=json.dumps(
                    {
                        "persona": cfg.persona,
                        "voice": cfg.voice,
                        "era": cfg.era,
                        "hard_lines": cfg.hard_lines,
                    },
                    ensure_ascii=False,
                ),
                memories=json.dumps([m.content for m in memories], ensure_ascii=False),
                utterances=json.dumps(
                    [str(e.payload.get("text", "")) for e in reversed(utterances)],
                    ensure_ascii=False,
                ),
            )
            text = router.generate(
                tier="mid",
                agent_id="eval-judge",
                tick=0,
                prompt=prompt,
                max_tokens=1200,
                name="coherence-judge",
            )
            verdict = _extract_json(text) or {}
            results[npc_id] = {
                "persona": int(verdict.get("persona_consistency", 0)),
                "memory": int(verdict.get("memory_accuracy", 0)),
                "notes": str(verdict.get("notes", ""))[:200],
                "n_utterances": len(utterances),
            }
            print(
                f"{npc_id:10} persona={results[npc_id]['persona']:3} "
                f"memory={results[npc_id]['memory']:3}  {results[npc_id]['notes']}"
            )

        if not results:
            print("not enough conversation in the event log to evaluate")
            return 1

        consistency = statistics.mean(r["persona"] for r in results.values())
        memory_score = statistics.mean(r["memory"] for r in results.values())
        session.add(
            EvalRun(
                sample_size=sum(r["n_utterances"] for r in results.values()),
                consistency_score=consistency,
                memory_score=memory_score,
                notes=results,
            )
        )
        session.commit()

    print(f"\nconsistency={consistency:.1f} memory={memory_score:.1f} (target: consistency >= 70)")
    return 0 if consistency >= 70 else 1


if __name__ == "__main__":
    raise SystemExit(main())
