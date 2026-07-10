"""Structured error codes for the World Engine.

Every tool failure is `{ok: false, code, msg}` (ARCHITECTURE §4). Codes are
defined once here and reused by tests — never invent codes inline.
"""

from dataclasses import dataclass


class Code:
    NO_SUCH_AGENT = "NO_SUCH_AGENT"
    AGENT_NOT_ACTIVE = "AGENT_NOT_ACTIVE"
    NO_SUCH_ITEM = "NO_SUCH_ITEM"
    NOT_HOLDER = "NOT_HOLDER"
    ITEM_NOT_AVAILABLE = "ITEM_NOT_AVAILABLE"
    INSUFFICIENT_COINS = "INSUFFICIENT_COINS"
    SELF_TARGET = "SELF_TARGET"
    NO_SUCH_TRADE = "NO_SUCH_TRADE"
    TRADE_NOT_OPEN = "TRADE_NOT_OPEN"
    NOT_TRADE_TARGET = "NOT_TRADE_TARGET"
    TRADE_UNFULFILLABLE = "TRADE_UNFULFILLABLE"
    WORLD_PAUSED = "WORLD_PAUSED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    FILTERED = "FILTERED"
    INVALID_ARG = "INVALID_ARG"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Err:
    code: str
    msg: str

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code, "msg": self.msg}
