"""A2A client: how one NPC (or the gateway) speaks to another NPC (FR-3.1).

Always a network call to the target's endpoint — no in-process shortcuts,
even between co-hosted NPCs (hard rule 2).
"""

import httpx
from a2a.client import ClientConfig, create_client, minimal_agent_card
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest
from a2a.utils import TransportProtocol


async def send_to_npc(
    target_url: str,
    text: str,
    *,
    from_character: str,
    tick: int,
    world: str = "the-interval",
    httpx_client: httpx.AsyncClient | None = None,
) -> str:
    """Send one A2A message/send to the NPC at target_url; return the reply text."""
    message = new_text_message(text, role=Role.ROLE_USER)
    message.metadata.update({"world": world, "tick": tick, "from_character": from_character})

    card = minimal_agent_card(target_url, transports=[TransportProtocol.JSONRPC])
    config = ClientConfig(streaming=False, httpx_client=httpx_client)
    client = await create_client(card, client_config=config)

    reply_parts: list[str] = []
    async for response in client.send_message(SendMessageRequest(message=message)):
        if response.HasField("message"):
            reply_parts.append(get_message_text(response.message))
    return "\n".join(p for p in reply_parts if p)
