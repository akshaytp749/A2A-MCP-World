"""World Engine service: /health, admin API, and the MCP mount (ARCHITECTURE §2.1)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from engine.auth import hash_token, new_token
from engine.config import settings
from engine.db import init_db, session_scope
from engine.mcp_server import build_mcp
from engine.models import Agent, Event, World
from engine.seed import load_seed, seed_world


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.admin_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="admin token required")


class CreateAgent(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,40}$")  # slugs (conventions)
    name: str
    kind: str = "resident"
    coins: int = 10
    endpoint_url: str | None = None  # NPC's A2A/tick base url (heartbeat webhooks)


class RotateToken(BaseModel):
    endpoint_url: str | None = None  # refresh the endpoint on re-registration


class SetPaused(BaseModel):
    paused: bool


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict:
        with session_scope() as session:
            world = session.get(World, 1)
            tick = world.tick if world else -1
        return {"status": "ok", "tick": tick}

    @app.post("/admin/agents", dependencies=[Depends(require_admin)])
    def create_agent(body: CreateAgent) -> dict:
        with session_scope() as session:
            if session.get(Agent, body.id) is not None:
                raise HTTPException(status_code=409, detail=f"agent '{body.id}' exists")
            token = new_token()
            session.add(
                Agent(
                    id=body.id,
                    kind=body.kind,
                    name=body.name,
                    coins=body.coins,
                    endpoint_url=body.endpoint_url,
                    token_hash=hash_token(token),
                    status="active",
                )
            )
        # The plaintext token is returned exactly once and never stored or logged.
        return {"id": body.id, "token": token}

    @app.post("/admin/agents/{agent_id}/token", dependencies=[Depends(require_admin)])
    def rotate_token(agent_id: str, body: RotateToken | None = None) -> dict:
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                raise HTTPException(status_code=404, detail=f"no agent '{agent_id}'")
            token = new_token()
            agent.token_hash = hash_token(token)
            if body is not None and body.endpoint_url:
                agent.endpoint_url = body.endpoint_url
        return {"id": agent_id, "token": token}

    @app.post("/admin/tick", dependencies=[Depends(require_admin)])
    def advance_tick() -> dict:
        with session_scope() as session:
            world = session.get(World, 1)
            if world is None:
                raise HTTPException(status_code=500, detail="world not seeded")
            world.tick += 1
            tick = world.tick
        return {"tick": tick}

    @app.post("/admin/pause", dependencies=[Depends(require_admin)])
    def set_paused(body: SetPaused) -> dict:
        with session_scope() as session:
            world = session.get(World, 1)
            world.paused = body.paused
        return {"paused": body.paused}

    @app.get("/admin/events", dependencies=[Depends(require_admin)])
    def list_events(since_tick: int = 0, limit: int = 500) -> dict:
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(Event)
                    .where(Event.tick >= since_tick)
                    .order_by(Event.id.desc())
                    .limit(limit)
                )
            )
            events = [
                {
                    "id": e.id,
                    "tick": e.tick,
                    "actor_id": e.actor_id,
                    "tool": e.tool,
                    "payload": e.payload,
                    "ok": e.result.get("ok"),
                }
                for e in reversed(rows)
            ]
        return {"events": events}

    @app.post("/admin/eject/{agent_id}", dependencies=[Depends(require_admin)])
    def eject(agent_id: str) -> dict:
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                raise HTTPException(status_code=404, detail=f"no agent '{agent_id}'")
            agent.status = "ejected"
            world = session.get(World, 1)
            session.add(
                Event(
                    tick=world.tick if world else 0,
                    actor_id=agent_id,
                    tool="eject",
                    payload={"text": f"{agent.name} packs up and leaves."},
                    result={"ok": True},
                )
            )
        return {"id": agent_id, "status": "ejected"}

    @app.get("/admin/agents", dependencies=[Depends(require_admin)])
    def list_agents() -> dict:
        with session_scope() as session:
            agents = [
                {"id": a.id, "name": a.name, "kind": a.kind, "status": a.status, "coins": a.coins}
                for a in session.scalars(select(Agent))
            ]
        return {"agents": agents}


def create_app() -> FastAPI:
    """App factory: each app owns a fresh FastMCP (its session manager runs once)."""
    mcp = build_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        seed_path = Path(settings.seed_path)
        if seed_path.exists():
            with session_scope() as session:
                seed_world(session, load_seed(seed_path))
        scheduler = None
        if settings.heartbeat_enabled:
            from engine.heartbeat import start_heartbeat

            scheduler = start_heartbeat()
        # The MCP session manager must run for the mounted streamable-HTTP app.
        async with mcp.session_manager.run():
            yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="The Interval — World Engine", lifespan=lifespan)
    app.mount("/mcp", mcp.streamable_http_app())
    _register_routes(app)

    from gate.routes import router as gate_router
    from gateway.routes import router as gateway_router

    app.include_router(gateway_router)
    app.include_router(gate_router)

    assets = Path("web/assets")
    if assets.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    ui = Path("web/index.html")
    if ui.exists():

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(ui)

    return app


app = create_app()  # uvicorn entrypoint: engine.app:app
