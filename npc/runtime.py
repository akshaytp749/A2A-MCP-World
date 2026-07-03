"""NPC service entrypoint: A2A server + /tick webhook around the cognition loop.

One codebase, persona injected via env (ADR 5):
  NPC_ID       persona id from world/seed/npcs/*.yaml (e.g. "twain")
  ENGINE_URL   world engine base url
  PUBLIC_URL   this NPC's externally reachable base url (goes in the agent card)
  PEERS        JSON map {agent_id: a2a_base_url} of co-residents
  ADMIN_TOKEN  used ONCE at boot to self-register with the engine (residents
               are operator-owned; guests get tokens from the gate instead)

Run: uvicorn "npc.runtime:create_app" --factory --port 9000
"""

import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from npc.a2a_server import create_npc_app
from npc.config import NpcConfig, load_personas
from npc.mcp_client import McpEngineClient
from npc.mind import Mind


def _register_with_engine(engine_url: str, admin_token: str, config: NpcConfig) -> tuple[str, bool]:
    """Create (or re-key) this NPC's agent row. Returns (bearer token, created_now)."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    public_url = os.environ.get("PUBLIC_URL", "http://127.0.0.1:9000")
    with httpx.Client(base_url=engine_url, timeout=15) as client:
        res = client.post(
            "/admin/agents",
            headers=headers,
            json={
                "id": config.id,
                "name": config.name,
                "kind": "resident",
                "coins": 10,
                "endpoint_url": public_url,
            },
        )
        if res.status_code == 200:
            return res.json()["token"], True
        if res.status_code == 409:
            res = client.post(
                f"/admin/agents/{config.id}/token",
                headers=headers,
                json={"endpoint_url": public_url},
            )
            res.raise_for_status()
            return res.json()["token"], False
        res.raise_for_status()
        raise RuntimeError("unreachable")


async def _write_seed_memories(engine: McpEngineClient, config: NpcConfig) -> int:
    n = 0
    for mem in config.seed_memories:
        content = str(mem.get("content", "")).strip()
        if content:
            await engine.call(
                "remember", {"content": content, "salience": float(mem.get("salience", 0.5))}
            )
            n += 1
    return n


class TickBody(BaseModel):
    tick: int


def create_app() -> FastAPI:
    npc_id = os.environ["NPC_ID"]
    engine_url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8000")
    public_url = os.environ.get("PUBLIC_URL", "http://127.0.0.1:9000")
    peers: dict[str, str] = json.loads(os.environ.get("PEERS", "{}"))
    admin_token = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
    npcs_dir = os.environ.get("NPCS_DIR", "world/seed/npcs")

    personas = load_personas(npcs_dir)
    if npc_id not in personas:
        raise RuntimeError(f"no persona '{npc_id}' in {npcs_dir} (have: {sorted(personas)})")
    config = personas[npc_id]

    app = create_npc_app(config, public_url)
    state = app.state.npc

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        token, _created = _register_with_engine(engine_url, admin_token, config)
        engine = McpEngineClient(engine_url, token)
        app.state.engine = engine
        app.state.mind = Mind(config, engine, peers)
        # Seed biographical memories iff the agent has none yet — idempotent
        # even if an earlier boot died between registration and seeding.
        existing = await engine.call("recall", {"query": "", "k": 1})
        if not existing.get("memories"):
            app.state.seeded = await _write_seed_memories(engine, config)
        yield

    app.router.lifespan_context = lifespan

    @app.post("/tick")
    async def tick(body: TickBody) -> dict:
        inbox = state.drain_inbox()
        mind: Mind = app.state.mind
        return await mind.run_tick(body.tick, inbox)

    @app.get("/llm-usage")
    def llm_usage() -> dict:
        from llm.router import get_router

        return get_router().usage()

    return app
