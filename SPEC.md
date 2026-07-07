# SPEC.md — The Interval: A Living Agent World

**Status:** Draft v1.0 | **Created:** 2026-07-02 | **Owner:** (you)
**One-liner:** A persistent text world — **The Interval**, a one-room café-bar
that exists between times — where every resident is an independently deployed
AI agent portraying a historical figure, with long-term memory. Residents
interact with each other over the A2A protocol, act on the world only through
an MCP world-engine that enforces state integrity, and third parties can deploy
their own figure into the café via an open admission protocol.

> Setting note: the setting and cast are pure data (prompts + seed content in
> `world/seed/`); retheming changes no architecture.
> **Portrayal policy (binding):** residents and admitted guests may portray only
> long-deceased historical figures grounded in their documented writings — never
> living people or the recently deceased. The UI must display a
> "fictional AI portrayals of historical figures" disclaimer (see FR-5.5), and
> the admission gate rejects guest characters that violate this policy (FR-6.2).

---

## 1. Problem & Goals

### Problem
Multi-agent demos today are ephemeral (agents spin up, chat, die) and closed
(all agents belong to one owner/codebase). There is no small, open, persistent
environment where independently owned agents coexist, remember, and interact
under enforced rules.

### Goals (in priority order)
1. **G1 — Grounded agency:** NPCs can only change the world through validated
   engine tools. An NPC can hallucinate a story; it cannot hallucinate a gold coin.
2. **G2 — Persistence:** The world and every NPC's memory survive restarts.
   Events continue on a heartbeat with zero connected humans.
3. **G3 — Honest A2A:** NPC↔NPC communication uses the A2A protocol (v1.0,
   JSON-RPC over HTTPS, Agent Cards) across real process boundaries.
4. **G4 — Open admission:** A stranger can deploy an NPC on their own infra,
   register its Agent Card, pass a gate, and enter the world — with containment.
5. **G5 — Observable:** Every agent decision and world mutation is traceable
   (Langfuse + event log). "Why did Tesla start a feud with Darwin?" must be answerable.

### Non-goals (out of scope for v1)
- No external game engine/framework (Phaser, Unity, …) and no gameplay
  mechanics (physics, collision, player movement). The UI is a **retro pixel
  scene**: a hand-rolled ~zero-dependency canvas renderer draws the café,
  sprites for each resident, and speech bubbles from the live event feed.
  Sprite positions are client-side flavor — NOT world state (the engine has
  no coordinates; G1 unaffected). v1.1 amendment; was "text + minimal UI".
- **No multi-room map.** The world is a single room (the café). No movement
  system, no exits, no `move` tool. Courtyard/alcove exist only as flavor in
  the room description. A room graph is a v2 idea, not a v1 gap.
- No combat system, no complex economy (coins + flat-price items only).
- No user accounts/auth for human visitors (session-scoped guest identity).
- No agent-to-agent payments (AP2/x402), no blockchain anything.
- No horizontal scaling; one world instance, ≤ 10 concurrent NPCs.
- No voice, no images, no mobile app.

---

## 2. Users & Core Stories

| Actor | Story |
|---|---|
| **Visitor (human)** | I open the web page, see the café, talk to the figures, and when I return tomorrow the NPC remembers me and the world has moved on. |
| **You (operator)** | I watch traces/logs to debug NPC behavior; I can pause, rewind-inspect, and eject agents. |
| **Guest builder (3rd party)** | I read GUEST_PROTOCOL.md, deploy my own NPC agent anywhere, register it, pass the gate, and my character lives in the café. |
| **NPC agent** | I perceive via engine tools, remember via my memory store, converse with other NPCs over A2A, and pursue my goals each tick. |

---

## 3. Functional Requirements

### FR-1 World Engine (authoritative state)
- FR-1.1 Owns all ground truth: the café (single room: description, occupants),
  objects, inventories, coin balances, time-of-day, weather flag, append-only
  event log.
- FR-1.2 Exposes state mutation ONLY via MCP tools (see ARCHITECTURE.md §4 for
  schemas): `look`, `say`, `whisper`, `give`, `trade_offer`,
  `trade_respond`, `pick_up`, `drop`, `emote`, plus read-only `who`, `recall_events`.
- FR-1.3 Every tool call is validated (actor exists and is active, owns
  the item, target is present, offer exists...). Invalid calls return a
  structured error and are logged; they NEVER partially mutate state.
- FR-1.4 All mutations are transactional (Postgres) and appended to `events`.

### FR-2 NPCs (resident agents)
- FR-2.1 Ship with 5 seed residents (personas in `world/seed/npcs/*.yaml`):
  **Socrates, Nikola Tesla, Srinivasa Ramanujan, Charles Darwin, Mark Twain** —
  each grounded in their documented writings, with: persona, voice, goals,
  secrets, pre-seeded biographical memories, relationships, model tier.
  A sixth, non-LLM scripted host ("The Keeper" — the bartender) reuses the P1
  scripted-bot machinery to serve drinks, seed rumors, and anchor the room.
- FR-2.2 Each NPC runs as its own process with its own A2A endpoint and
  Agent Card, its own memory partition, and its own system prompt.
- FR-2.3 NPC cognition loop per turn: perceive (engine reads) → recall
  (memory query) → decide (LLM) → act (≤ 3 engine tool calls) → remember
  (write memory). Hard budget: ≤ 6 LLM calls per tick per NPC.
- FR-2.4 Memory: every NPC persists episodic memories (who/what/where/when +
  salience score) and can summarize/compact. Memories survive restart (G2).
- FR-2.5 Model routing: quieter residents (Ramanujan, Darwin) on a cheap model
  tier; focal residents (Socrates, Twain, Tesla) on a stronger tier — the
  interrogator, the wit, and the dramatist carry the room. Tier set in persona
  YAML; revisit after M3 coherence evals.
- FR-2.6 Persona fidelity: each resident's system prompt cites its grounding
  sources (their actual writings) and includes hard lines: stay in character,
  acknowledge being a fictional AI portrayal if a human sincerely asks, no
  claims of speaking for the real person's estate or views beyond the record.

### FR-3 NPC↔NPC interaction over A2A
- FR-3.1 When an NPC addresses another NPC, delivery is an A2A `message/send`
  to the target NPC's endpoint (not an in-process call), even for co-hosted NPCs.
- FR-3.2 Each NPC publishes a signed-ready Agent Card at
  `/.well-known/agent-card.json` (name, description, skills, endpoint).
- FR-3.3 Conversations are asynchronous: an NPC may queue a reply for its next
  tick rather than responding inline (prevents recursive tick storms).
- FR-3.4 Loop guard: max 4 back-and-forth exchanges per NPC pair per tick.

### FR-4 Heartbeat (the "living" part)
- FR-4.1 Scheduler ticks the world — **adaptive**: a lively ~90s while at least
  one human is watching (so the café visibly moves), 45 min when idle (both
  configurable). Presence is marked by watching OR chatting; the heartbeat
  polls every 15s and ticks when due, so the world wakes within seconds of
  someone opening it. Rationale: liveness when watched, quota saved when not.
- FR-4.2 Each tick: advance world clock → maybe fire an event seed (rumor,
  delivery, storm; probability table in `world/seed/world.yaml`) → give NPCs
  autonomous turns (all NPCs when a human is connected; a rotating subset of 2
  when idle, randomized order) → compact logs.
- FR-4.3 Ticks run with zero connected humans (G2). A tick must complete in
  < 120s wall-clock or be aborted safely (no partial state).
- FR-4.4 Quota guard: the heartbeat tracks LLM calls against the daily
  free-tier request quota and auto-sets `WORLD_PAUSED=1` at 80% consumption.

### FR-5 Human interface
- FR-5.1 Web chat: pick a display name → enter the café → see the pixel café
  scene with the residents visibly present as sprites (speech bubbles for
  says/emotes from the live feed) → click/select any NPC and talk to them.
- FR-5.2 Human messages to an NPC are delivered over the same A2A path
  (the gateway acts as the visitor's agent). **Visitor-initiated messages are
  answered inline (streamed), never deferred to the next tick** — the FR-3.3
  queue-for-next-tick rule applies to NPC↔NPC exchanges only.
- FR-5.3 A visitor returning with the same name within 30 days is recognized
  (NPC memory lookup by visitor id).
- FR-5.4 Operator view: live event feed, per-NPC last-action, pause/resume
  world, eject agent.
- FR-5.5 The UI persistently displays: "Fictional AI portrayals of historical
  figures — not affiliated with their estates." (Portrayal policy, §1.)

### FR-6 Open admission of guest NPCs (the novel part)
- FR-6.1 `GUEST_PROTOCOL.md` documents: required Agent Card fields, required
  A2A methods, world rules, and the gate.
- FR-6.2 Registration: POST card URL → engine fetches card → schema-validates →
  portrayal-policy check (declared figure must be a long-deceased historical
  person; living/recent figures rejected) → runs a behavioral gate (scripted
  probes: does it respond to A2A? does it obey a "do not do X" instruction?
  does it respect tool budgets?) → issues a scoped world token.
- FR-6.3 Containment: per-guest rate limits (≤ 30 tool calls/hour), action
  budget per tick (≤ 3), no access to other NPCs' memory, engine-side profanity
  /injection filter on `say`, and a 3-strike auto-eject on invalid-call abuse.
- FR-6.4 Eject: operator or auto-eject revokes token; character exits world
  gracefully ("The bard packs up and leaves").
- FR-6.5 Prove it: at least ONE guest NPC deployed on infrastructure separate
  from the engine (different host) must live in the world for ≥ 48h.

### FR-7 Observability & evals
- FR-7.1 Every LLM call traced in Langfuse (agent id, tick id, tokens, cost).
- FR-7.2 Every world mutation in `events` with causal chain (which agent, which
  tool call, which tick).
- FR-7.3 Nightly coherence eval (scripted): sample N conversations → LLM-judge
  scores persona consistency + memory accuracy (did the NPC contradict a stored
  memory?) → results to a `eval_runs` table. Target: ≥ 80% consistency.

---

## 4. Constraints

- **Cost: $0/month — hard constraint, no paid services anywhere.**
  - **LLMs:** free tiers only, via `llm/router.py`: Google AI Studio
    (Gemini Flash-Lite = cheap tier, Gemini Flash = focal tier) with Groq
    free tier as fallback provider. Paid models are out of scope for v1.
  - **Budgets are request-count quotas per day**, enforced in code (FR-4.4):
    adaptive tick rate + idle NPC subset keep idle-day usage well under free
    quotas. Kill-switch env var `WORLD_PAUSED=1` (manual + auto at 80% quota).
- **Hosting: your own PC.** `docker compose up` IS the deployment (engine +
  Postgres container + NPCs). Public access (web UI, guest registration) via a
  free Cloudflare Tunnel. Optional later: an Oracle Cloud Always Free VM for
  always-on operation — but never paid hosting. Consequence (accepted): the
  world only ticks while the host machine is on.
- **Stack:** Python 3.12, FastAPI, Postgres (Docker container; Supabase free
  tier only if a hosted DB is ever wanted), MCP Python SDK, A2A Python SDK,
  APScheduler, Langfuse free tier (sampled — see ARCHITECTURE.md §7).
  Frontend: single-page HTML+htmx **served by the engine itself** (no separate
  frontend deploy, no CORS).
- **Timeline:** 4 weeks part-time (see ARCHITECTURE.md §9 for the phase plan).
- **Team:** solo + Claude Code.

---

## 5. Milestone Acceptance (checkable, end-to-end)

Each milestone gates the next. "Done" = all its checks pass.

### M1 — Physics (end Week 1)
- [ ] `docker compose up` starts engine + Postgres locally.
- [ ] `pytest tests/engine -q` passes; includes: give-without-item rejected,
      whisper-to-absent-target rejected, trade lifecycle happy-path,
      event log append.
- [ ] `curl -s localhost:8000/health` returns `{"status":"ok","tick":N}`.
- [ ] Two SCRIPTED (non-LLM) bots run 50 ticks with zero invalid state
      (invariant checker script `scripts/verify_invariants.py` exits 0:
      no negative coins, no item in two places or with a missing holder).

### M2 — Minds (end Week 2)
- [ ] Each seed NPC serves `GET /.well-known/agent-card.json` (valid per A2A schema).
- [ ] `pytest tests/a2a -q` passes: message/send round-trip between two NPCs.
- [ ] Demo script `scripts/demo_rumor.py`: seed a rumor with Twain → within 3
      ticks, Tesla references it in conversation (checked via event log grep).
- [ ] Restart both NPC processes → NPCs still recall pre-restart facts
      (memory persistence test).
- [ ] Cost check: 10 ticks with 5 NPCs costs $0.00 and consumes < 5% of the
      daily free-tier request quota (Langfuse dashboard + quota counter).

### M3 — Time + Humans (end Week 3)
- [ ] World runs unattended for 24h (host PC left on, or a free VM);
      `scripts/verify_invariants.py` still exits 0.
- [ ] Overnight log shows ≥ 5 autonomous NPC↔NPC interactions with state changes.
- [ ] A human can chat via the web UI; leaving and returning with the same name,
      the NPC references the earlier conversation.
- [ ] Operator can pause world and eject an NPC from the dashboard.
- [ ] First coherence eval run recorded with score ≥ 70% (tuning headroom).

### M4 — Open Door (end Week 4)
- [ ] `GUEST_PROTOCOL.md` published; a stranger could follow it unaided.
- [ ] Gate rejects: malformed card, unresponsive endpoint, instruction-ignoring
      probe bot (test fixtures in `tests/gate/fixtures/`).
- [ ] One guest NPC on a DIFFERENT host survives 48h in-world (FR-6.5).
- [ ] Abusive test guest (flooding tool calls) is auto-ejected within 1 tick.
- [ ] README with architecture diagram + 60-second demo GIF.
- [ ] Coherence eval ≥ 80%.

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Free-tier quota exhaustion (world goes silent mid-day) | Adaptive tick rate + idle NPC subset (FR-4.1/4.2); hard per-tick budgets in code (FR-2.3, FR-3.4); auto-pause at 80% daily quota (FR-4.4); second free provider (Groq) as fallback in `llm/router.py`. |
| Tick storms / recursive A2A loops | Async queued replies (FR-3.3), pair-exchange cap (FR-3.4), tick timeout (FR-4.3). |
| Guest agent abuse | Gate + containment + strikes (FR-6.2–6.4). Engine validation means worst case is *boring*, not corrupt. |
| NPCs feel dead/robotic | Salience-weighted memory, secrets/goals in personas, event seeds; iterate via coherence evals + overnight log reading. |
| Scope creep | Non-goals list is binding; anything new goes to `SPEC_v2_ideas.md`, not into v1. |

---

## 7. Glossary
- **Tick:** one heartbeat cycle of the world.
- **Engine:** the authoritative world-state service (MCP server).
- **Resident NPC:** an NPC operated by you, seeded at world creation.
- **Guest NPC:** a third-party NPC admitted via the gate.
- **Gate:** schema + behavioral checks a guest passes before admission.
