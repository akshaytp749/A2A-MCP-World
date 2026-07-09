"""Gateway: humans ↔ the agent world (FR-5, ARCHITECTURE §2.3).

The gateway acts as the visitor's agent: it wraps a chat message as an A2A
message/send to the target NPC (the same protocol NPCs use with each other)
and returns the NPC's inline reply. It never touches world state — its only
writes are to the `visitors` table, which is the gateway's own domain.
"""

import asyncio
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from engine.db import session_scope
from engine.heartbeat import presence
from engine.models import Agent, Event, Visitor, World
from npc.a2a_client import send_to_npc

logger = logging.getLogger("gateway")

router = APIRouter(prefix="/gateway", tags=["gateway"])

FEED_TOOLS = {
    "say",
    "emote",
    "give",
    "pick_up",
    "drop",
    "trade_offer",
    "trade_respond",
    "event_seed",
    "eject",
    "arrival",
}


def _visitor_id(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")[:40]
    return f"visitor-{slug or 'anon'}"


class ChatBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    target: str  # NPC agent id
    text: str = Field(min_length=1, max_length=2000)


@router.post("/chat")
async def chat(body: ChatBody) -> dict:
    presence.mark()  # a human is here — heartbeat shifts to fast cadence
    vid = _visitor_id(body.display_name)

    with session_scope() as session:
        visitor = session.get(Visitor, vid)
        if visitor is None:
            session.add(Visitor(id=vid, display_name=body.display_name))
        else:
            visitor.last_seen = datetime.utcnow()
        npc = session.get(Agent, body.target)
        if npc is None or npc.status != "active" or not npc.endpoint_url:
            raise HTTPException(status_code=404, detail=f"no active NPC '{body.target}'")
        endpoint = npc.endpoint_url
        world = session.get(World, 1)
        tick = world.tick if world else 0

    try:
        reply = await send_to_npc(
            endpoint,
            body.text,
            from_character=vid,
            tick=tick,
            extra_metadata={"visitor": True, "visitor_name": body.display_name},
        )
    except Exception:
        logger.exception("A2A delivery to %s failed", body.target)
        raise HTTPException(
            status_code=503,
            detail=f"{npc.name} seems lost in thought — give them a moment and try again.",
        ) from None
    return {"ok": True, "from": body.target, "reply": reply, "visitor_id": vid}


@router.post("/presence")
def presence_ping() -> dict:
    """A visible tab pings this to keep the café lively (fast heartbeat).

    Separate from /room so a hidden/background tab — which still polls /room to
    stay rendered — does NOT count as watching and lets the world fall idle,
    protecting the free-tier quota (review finding #2).
    """
    presence.mark()
    return {"ok": True}


@router.get("/room")
def room() -> dict:
    with session_scope() as session:
        world = session.get(World, 1)
        occupants = [
            {"id": a.id, "name": a.name, "kind": a.kind, "spot": a.spot or "bar"}
            for a in session.scalars(
                select(Agent).where(Agent.status == "active", Agent.kind.in_(["resident", "guest"]))
            )
        ]
    return {
        "cafe": world.cafe_name,
        "description": world.cafe_description,
        "clock_time": world.clock_time,
        "weather": world.weather,
        "tick": world.tick,
        "paused": world.paused,
        "occupants": occupants,
    }


class _EventBroadcaster:
    """One shared DB tail fanned out to all SSE clients (review #4).

    Replaces per-client DB polling: a single background task reads new public
    events every 2s and pushes them to every subscriber's queue, so DB load is
    O(1) in the number of connected clients instead of O(clients).
    """

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        with session_scope() as session:
            last_id = session.scalar(select(func.max(Event.id))) or 0
        while self._subs:
            try:
                rows = []
                with session_scope() as session:
                    for e in session.scalars(
                        select(Event).where(Event.id > last_id).order_by(Event.id).limit(100)
                    ):
                        last_id = max(last_id, e.id)
                        if e.tool not in FEED_TOOLS or not e.result.get("ok"):
                            continue
                        rows.append({"id": e.id, "tick": e.tick, "actor": e.actor_id,
                                     "tool": e.tool, "text": str(e.payload.get("text", ""))})
                for row in rows:
                    for q in list(self._subs):
                        if q.qsize() < 200:
                            q.put_nowait(row)
            except Exception:
                logger.exception("event pump error")
            await asyncio.sleep(2)
        self._task = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        self._ensure_running()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)


_broadcaster = _EventBroadcaster()


BACKLOG_ROWS = 12


def _recent_public_events(limit: int = BACKLOG_ROWS) -> list[dict]:
    """Last few public happenings, oldest first — so a new client's feed isn't empty."""
    with session_scope() as session:
        rows = []
        for e in session.scalars(select(Event).order_by(Event.id.desc()).limit(limit * 4)):
            if e.tool not in FEED_TOOLS or not e.result.get("ok"):
                continue
            rows.append({"id": e.id, "tick": e.tick, "actor": e.actor_id,
                         "tool": e.tool, "text": str(e.payload.get("text", ""))})
            if len(rows) >= limit:
                break
    return list(reversed(rows))


@router.get("/events")
async def events_stream():
    """SSE feed of public room happenings (whispers stay private).

    Starts with a short backlog of recent conversation, then streams live.
    """
    q = _broadcaster.subscribe()
    backlog = _recent_public_events()
    last_backlog_id = backlog[-1]["id"] if backlog else 0

    async def stream():
        try:
            for row in backlog:
                yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
            while True:
                try:
                    row = await asyncio.wait_for(q.get(), timeout=15)
                    if row["id"] <= last_backlog_id:
                        continue  # already sent in the backlog
                    yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _broadcaster.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")
