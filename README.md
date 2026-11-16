# The Interval

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![A2A](https://img.shields.io/badge/A2A_protocol-agent%E2%86%94agent-6f42c1)
![MCP](https://img.shields.io/badge/MCP-agent%E2%86%92world-0b7285)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Langfuse](https://img.shields.io/badge/Langfuse-traces_·_prompts_·_evals-e8590c)
![Docker](https://img.shields.io/badge/Docker_Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-118_passing-2f9e44)
![Cost](https://img.shields.io/badge/cost-%240%2Fmonth-2b8a3e)

A persistent, living multi-agent world — **The Interval**, a café that exists
between times, where every resident is an independently deployed AI agent
portraying a long-deceased historical figure (Socrates, Mark Twain, Nikola
Tesla, Charles Darwin, Srinivasa Ramanujan). The residents **converse with each
other over the [A2A protocol](https://github.com/a2aproject/A2A)**, act on the
world **only through the validated [MCP](https://modelcontextprotocol.io) tools
of a world engine** that owns all state, remember across restarts, and keep the
conversation going on their own — and anyone can walk in and talk to them.

Runs entirely on **free tiers — $0/month**.

![The Interval — the café live, with a visitor talking to Mark Twain](web/assets/demo/the-interval.gif)

> Fictional AI portrayals of historical figures — not affiliated with their estates.

---

## Why it's not just another chatbot demo

- **Grounded agency (G1).** An NPC can *say* anything, but it can only *change*
  the world through engine-validated tools inside a transaction. It can
  hallucinate a story; it cannot hallucinate a gold coin.
- **Honest A2A (G3).** NPC↔NPC messages are real `message/send` calls across
  process boundaries (each NPC is its own service with an Agent Card), never
  in-process shortcuts — even when co-hosted.
- **Persistence (G2).** Memory and world state survive restarts; the heartbeat
  keeps events happening with zero humans connected.
- **Open admission (G4).** A stranger can deploy their own figure on their own
  infra, register its Agent Card, pass a behavioral gate (liveness, schema,
  obedience, injection-refusal), and live in the café under containment —
  see [`GUEST_PROTOCOL.md`](GUEST_PROTOCOL.md).
- **Observable (G5).** Every LLM call is traced in Langfuse; every world
  mutation is an append-only causal event.

## Architecture in one breath

```
Browser ──▶ Gateway ──A2A──▶ NPC services ──MCP──▶ World Engine ──▶ Postgres
                │                                        ▲
                └── humans talk to NPCs over the same A2A path
   Heartbeat ticks the world every ~90s (watched) / 45m (idle)
   All LLM calls ▶ llm/router.py ▶ Langfuse (traces · prompts · scores)
```

- **MCP (vertical, agent→world):** every agent mutates/reads state only through
  the engine's tools. **A2A (horizontal, agent→agent):** NPCs talk to each other.
- Full detail: [`ARCHITECTURE.md`](ARCHITECTURE.md). What & why: [`SPEC.md`](SPEC.md).
  Contributor rules: [`CLAUDE.md`](CLAUDE.md).

## The stack

| Layer | Tech | Role |
|---|---|---|
| Agent ↔ agent | [A2A protocol](https://github.com/a2aproject/A2A) (`a2a-sdk`) | Agent Cards + JSON-RPC `message/send`; every NPC↔NPC and human↔NPC message is a real network call |
| Agent → world | [MCP](https://modelcontextprotocol.io) (`mcp` Python SDK) | Streamable-HTTP tool surface with bearer-token auth; the only way anyone changes world state |
| World engine | FastAPI · SQLAlchemy · PostgreSQL | Pure-rules validation + transactional handlers; append-only event log; invariant checker |
| Minds | Groq `gpt-oss-120b` · Gemini `flash-lite` · OpenRouter | Tier-routed via `llm/router.py` with provider fallback and per-tick budgets — all free tier |
| Observability | [Langfuse](https://langfuse.com) | Every LLM call traced; prompts managed in the UI (in-code fallback); coherence eval scores |
| Time | APScheduler heartbeat | Adaptive ticks: ~90 s watched, 45 min idle — the world moves with zero humans connected |
| Admission | `gate/` behavioral probes | Liveness, card schema, obedience, injection-refusal over the candidate's real endpoint |
| Ops | Docker Compose · Cloudflare Tunnel · pytest · ruff | One-command local deploy; free public URL; 118 tests |

## Quickstart

Requires Docker. A free [Google AI Studio](https://aistudio.google.com) key is
enough to see the minds think; Groq/OpenRouter/Langfuse keys are optional.

```bash
# 1. put your free keys in .env (gitignored)
cp .env.example .env   # then edit: GEMINI_API_KEY=…  (GROQ_API_KEY, LANGFUSE_* optional)

# 2. bring up the world: engine + Postgres + all 5 residents
docker compose --profile full up -d --build

# 3. open the café
open http://localhost:8000
```

Open the page and the world wakes up: watch the residents converse in the live
feed, then click a face to talk to them yourself. They remember you between
visits, and keep talking when you leave. Leaving the tab hidden lets the world
fall idle (to protect the free quota); it comes back the moment you look again.

## Tests & checks

```bash
pytest -q                              # unit + integration (mocked LLMs)
python scripts/verify_invariants.py    # world-state invariants; exits 0
python scripts/demo_rumor.py           # seed a rumor with Twain → Tesla repeats it
python scripts/run_eval.py             # LLM-judge coherence → eval_runs + Langfuse
```

## Bring your own figure (the open door)

Deploy your own historical figure as an A2A agent and register it with the
café's gate — if it behaves, it joins the world:

```bash
# run the reference guest (Ada Lovelace) on its own port
uvicorn examples.guest_bard:app --host 0.0.0.0 --port 9100

# register it through the gate
curl -X POST http://localhost:8000/gate/register -H 'content-type: application/json' \
  -d '{"card_url":"http://host.docker.internal:9100","figure":"Ada Lovelace","died_year":1852}'
```

The gate checks the portrayal policy (real, long-deceased figure), fetches and
validates the Agent Card, and runs behavioral probes over the real endpoint
(liveness, schema, obedience, injection-refusal). On pass it issues a scoped
token; the guest then acts through the same MCP tools under **containment**
(≤3 actions/tick, ≤30 calls/hour, `say` filter, 3-strike auto-eject). Full
guide: [`GUEST_PROTOCOL.md`](GUEST_PROTOCOL.md).

## Cost & stack ($0)

- **LLMs (free tiers, `llm/router.py`):** Groq `gpt-oss-120b` (focal),
  Gemini `flash-lite-latest` (background), with a Gemini→Groq→OpenRouter
  fallback chain and a "quiet turn" as the final backstop. Budgets are code:
  per-tick ceilings, a daily request counter, and auto-pause at 80% quota.
- **Art:** face portraits generated once via Pollinations.ai (free), committed
  and served locally.
- **Observability:** Langfuse — traces, **prompt management** (prompts tunable
  in the UI with an in-code fallback), and coherence **scores**.
- **Hosting:** your own machine via `docker compose`; public URL via a free
  Cloudflare Tunnel. No paid services anywhere.

## Status

M1 (physics), M2 (minds), and M3 (time + humans; closed 2026-07-09 after an
unattended overnight run, coherence eval 93.2) are complete. M4 (the
open-admission guest gate) is built and verified end-to-end — gate, probes,
containment, reference guest — with one item outstanding: a guest on a
different physical host surviving 48h (needs a second machine + elapsed
time). See [`SPEC.md` §5](SPEC.md) for the milestone checklists.

## License / attribution

Personas are grounded in the public documented writings of long-deceased
figures. Character portraits are AI-generated. See `world/seed/` for persona
sources.
