"""Gatekeeper registration (FR-6.2, ARCH §2.5) — the open door.

POST /gate/register {card_url, figure, died_year} →
  fetch + schema-validate the Agent Card → portrayal-policy check →
  behavioral probes over the candidate's real A2A endpoint →
  on pass: create a guest agent row + scoped token, place them in the café
  with an arrival event, and return the token + probe scorecard.

The token lets the guest use the same MCP tool surface as residents, but the
engine applies guest containment (FR-6.3) to every call. Ejection is handled
by the engine (3-strike) or the operator (`/admin/eject`).
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.auth import hash_token, new_token
from engine.db import session_scope
from engine.handlers import admit_guest
from gate.probes import card_schema_ok, portrayal_ok, run_probes, slugify_guest

logger = logging.getLogger("gate")
router = APIRouter(prefix="/gate", tags=["gate"])


class Register(BaseModel):
    card_url: str = Field(min_length=4)
    figure: str = Field(min_length=1, max_length=80)  # the long-deceased person portrayed
    died_year: int


@router.post("/register")
async def register(body: Register) -> dict:
    # 1. Portrayal policy first — cheap, and a hard line (SPEC §1 / FR-6.2).
    policy_err = portrayal_ok(body.figure, body.died_year)
    if policy_err:
        raise HTTPException(status_code=422, detail={"stage": "portrayal", "reason": policy_err})

    base = body.card_url.rstrip("/")
    # Card URL may be the well-known path or the agent's base — normalize to base.
    if base.endswith("/.well-known/agent-card.json"):
        base = base[: -len("/.well-known/agent-card.json")]

    # 2. Behavioral probes over the real endpoint (also fetches + schema-checks).
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        report = await run_probes(base, client=client)

    if not report.ok:
        raise HTTPException(
            status_code=422,
            detail={"stage": "probes", "checks": report.checks},
        )

    card = report.card or {}
    schema_err = card_schema_ok(card)
    if schema_err:  # belt and suspenders
        raise HTTPException(status_code=422, detail={"stage": "schema", "reason": schema_err})

    # 3. Admission itself is an engine-owned write (hard rule 1: gate/ never
    # touches world state) — the gate only decided the guest may enter.
    guest_id = slugify_guest(body.figure)
    token = new_token()
    with session_scope() as session:
        err = admit_guest(
            session,
            guest_id=guest_id,
            name=str(card.get("name") or body.figure),
            figure=body.figure,
            endpoint_url=base,
            token_hash=hash_token(token),
            card=card,
            probes=report.checks,
        )
        if err is not None:
            raise HTTPException(status_code=409, detail=err.msg)
    logger.info("admitted guest %s (%s)", guest_id, body.figure)
    return {"ok": True, "guest_id": guest_id, "token": token, "scorecard": report.checks}
