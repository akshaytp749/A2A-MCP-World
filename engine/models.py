"""SQLAlchemy models — the authoritative world state (ARCHITECTURE §3).

Single-room world: no rooms table, no agent location. Items with
holder_agent_id = NULL are lying in the café. JSON (not JSONB) keeps the
schema portable between the Postgres container and SQLite test databases.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (CheckConstraint("coins >= 0", name="ck_agents_coins_nonneg"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug, e.g. "twain"
    kind: Mapped[str] = mapped_column(
        Enum("resident", "guest", "visitor", name="agent_kind", native_enum=False)
    )
    name: Mapped[str] = mapped_column(String(120))
    persona_ref: Mapped[str | None] = mapped_column(String(255))
    endpoint_url: Mapped[str | None] = mapped_column(String(255))
    card_json: Mapped[dict | None] = mapped_column(JSON)
    model_tier: Mapped[str | None] = mapped_column(String(32))
    token_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        Enum("active", "paused", "ejected", name="agent_status", native_enum=False),
        default="active",
    )
    coins: Mapped[int] = mapped_column(Integer, default=0)
    strikes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    # NULL = lying in the café (on the bar, the floor...)
    holder_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    price: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    tick: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(
        Enum("episodic", "summary", name="memory_kind", native_enum=False), default="episodic"
    )
    content: Mapped[str] = mapped_column(Text)
    salience: Mapped[float] = mapped_column(Float, default=0.5)
    refs: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposer_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    offer: Mapped[dict] = mapped_column(JSON)  # {"coins": int, "items": [item_id, ...]}
    ask: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        Enum("open", "accepted", "declined", "expired", name="trade_status", native_enum=False),
        default="open",
    )
    tick_opened: Mapped[int] = mapped_column(Integer)


class Visitor(Base):
    __tablename__ = "visitors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    cookie_hash: Mapped[str | None] = mapped_column(String(128))
    first_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sample_size: Mapped[int] = mapped_column(Integer)
    consistency_score: Mapped[float | None] = mapped_column(Float)
    memory_score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[dict | None] = mapped_column(JSON)


class World(Base):
    __tablename__ = "world"

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tick: Mapped[int] = mapped_column(Integer, default=0)
    clock_time: Mapped[str] = mapped_column(String(32), default="evening")
    weather: Mapped[str] = mapped_column(String(32), default="clear")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    cafe_name: Mapped[str] = mapped_column(String(120), default="The Interval")
    cafe_description: Mapped[str] = mapped_column(Text, default="")
