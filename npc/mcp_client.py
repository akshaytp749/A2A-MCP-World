"""MCP engine client for NPC processes: one tool call = one HTTP round trip."""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def parse_tool_result(result: Any) -> dict:
    """Normalize a CallToolResult into the engine handler's plain dict."""
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        inner = sc.get("result")
        return inner if isinstance(inner, dict) else sc
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {"ok": False, "code": "CLIENT_PARSE", "msg": "unparseable tool result"}


class McpEngineClient:
    """Satisfies npc.mind.EngineClient over the engine's streamable-HTTP MCP mount."""

    def __init__(self, engine_url: str, token: str):
        self.mcp_url = engine_url.rstrip("/") + "/mcp/"
        self.token = token

    async def call(self, tool: str, args: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with (
            streamablehttp_client(self.mcp_url, headers=headers) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return parse_tool_result(await session.call_tool(tool, args))
