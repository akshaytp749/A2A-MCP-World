"""Bearer-token auth: token -> agent identity. Tokens are never logged (hard rule 5)."""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.models import Agent


def new_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_actor(session: Session, authorization: str | None) -> str | None:
    """Return the agent id for a 'Bearer <token>' header, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    agent = session.scalar(select(Agent).where(Agent.token_hash == hash_token(token)))
    return agent.id if agent else None
