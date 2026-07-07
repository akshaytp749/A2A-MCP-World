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


@router.get("/room")
def room() -> dict:
    presence.mark()  # just watching the café keeps it lively (fast heartbeat)
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


@router.get("/events")
async def events_stream(since_id: int = -1):
    """SSE feed of public room happenings (whispers stay private).

    since_id=-1 (default) streams from NOW — new visitors shouldn't replay
    the world's entire history into their feed.
    """
    if since_id < 0:
        with session_scope() as session:
            since_id = session.scalar(select(func.max(Event.id))) or 0

    async def stream():
        last_id = since_id
        while True:
            rows: list[dict] = []
            with session_scope() as session:
                for e in session.scalars(
                    select(Event).where(Event.id > last_id).order_by(Event.id).limit(50)
                ):
                    last_id = max(last_id, e.id)
                    if e.tool not in FEED_TOOLS or not e.result.get("ok"):
                        continue
                    rows.append(
                        {
                            "id": e.id,
                            "tick": e.tick,
                            "actor": e.actor_id,
                            "tool": e.tool,
                            "text": str(e.payload.get("text", "")),
                        }
                    )
            for row in rows:
                yield f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
            yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")
