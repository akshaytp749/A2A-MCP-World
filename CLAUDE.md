# CLAUDE.md — The Interval (living agent world)

Persistent multi-agent world: NPC agents (separate processes) interact over the
**A2A protocol** and act on the world ONLY via the **MCP tools** of the World
Engine, which owns all state in Postgres. Heartbeat ticks the world; third-party
"guest" NPCs can be admitted through a gate.

**Read before non-trivial work:** SPEC.md (WHAT, milestones M1–M4) and
ARCHITECTURE.md (HOW, schemas §3–§4, phase plan §9). Specs win over chat memory.
New scope → propose a SPEC.md edit first; do NOT silently expand scope
(non-goals in SPEC §1 are binding).

## Commands
```bash
docker compose up -d            # local: engine + postgres + 2 NPCs
pytest -q                       # all tests; subsets: tests/engine tests/a2a tests/gate
pytest tests/e2e -q             # slower, needs compose stack running
python scripts/verify_invariants.py   # world-state invariants; MUST exit 0
python scripts/demo_rumor.py    # M2 acceptance demo
ruff check . && ruff format .   # lint/format (pre-commit runs both)
cloudflared tunnel run interval # expose local stack publicly (free tunnel)
```
Deployment = `docker compose up` on this machine (or a free VM). There is no
paid hosting and no paid API key anywhere; cost constraint is $0 (SPEC §4).

## Architecture in one breath
Agents → (MCP tools) → Engine → Postgres. Agents ↔ agents via A2A only.
Humans → Gateway → A2A. Heartbeat drives autonomous ticks. Gatekeeper admits
guests. Diagram + tool table: ARCHITECTURE.md §1 and §4.

## Hard rules (enforced in review — violating these is a bug)
1. **State integrity:** world state changes ONLY inside engine transactions via
   MCP tool handlers. Never write to `agents/items/trades/world` from
   npc/, gateway/, or gate/ code. `events` is append-only.
2. **Protocol honesty:** NPC↔NPC communication goes over A2A `message/send`
   (network call), even between co-hosted NPCs. No in-process shortcuts,
   no shared memory between agents.
3. **Budgets are code:** per-tick LLM-call ceilings and A2A pair caps live in
   code with tests. Never "fix" a budget test by raising the budget; flag it.
4. **Memory scoping:** an agent reads/writes only its own `memories` rows.
5. **Secrets:** tokens/keys via env only. Never log bearer tokens or full
   Agent Card auth sections.
6. **Validation pattern:** engine/rules.py stays pure
   `(state, action) -> Ok|Err`; side effects only in the handler layer.
7. **Portrayal policy:** characters are fictional portrayals of long-deceased
   historical figures only (SPEC §1). The gate enforces this for guests; the
   UI disclaimer (FR-5.5) is not optional.

## Workflow (how we build here)
- **TDD, strictly for engine/rules and gate/probes:** write failing tests →
  commit them → implement until green → NEVER modify committed tests to pass;
  if a test seems wrong, stop and say so.
- Work in milestone order (SPEC §5). Before claiming a milestone done, run its
  checklist literally and paste the outputs.
- Small diffs; one FR or one test-cluster per commit; commit message references
  the FR (e.g. `FR-3.4: pair exchange cap + tests`).
- After any change to engine/ or npc/: run `pytest -q` and
  `verify_invariants.py` before moving on.
- Don't mock the LLM in e2e "minds" tests sparingly — use the cheap tier with
  tiny max_tokens; mock in unit tests.
- When touching A2A or MCP integration, check the SDK docs first
  (a2a-python, mcp Python SDK) rather than guessing APIs from memory.

## Conventions
- Python 3.12, FastAPI, SQLAlchemy+Alembic, pydantic models everywhere at
  boundaries; type hints required; ruff clean.
- Structured errors: `{ok, code, msg}` — codes in engine/errors.py, reused
  by tests.
- Personas/world content = YAML in world/seed/ (content changes ≠ code changes).
- Every LLM call goes through llm/router.py (tier routing + Langfuse trace);
  no direct SDK calls elsewhere.
- Naming: tick ids are ints; agent ids are slugs (`marta`, `guest-bard-7f2`).

## Pointers
- Milestone checklists: SPEC.md §5 (checkable; run them, don't eyeball them)
- Tool schemas + error codes: ARCHITECTURE.md §4
- DB schema + invariants: ARCHITECTURE.md §3
- Gate probes: ARCHITECTURE.md §6
- Cost rules: ARCHITECTURE.md §7 — cost = LLM requests/day against free-tier
  quotas ($0 hard constraint). If a change plausibly raises per-tick request
  volume, say so explicitly in the PR/commit description.
