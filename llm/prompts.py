"""Langfuse prompt management with a local fallback (conventions: every LLM
prompt is versionable, but the world must run at $0 / offline).

`managed(name, fallback)` returns the Langfuse-managed prompt text if Langfuse
is configured AND reachable, otherwise the hardcoded `fallback` that ships in
code. So prompts can be tuned/versioned in the Langfuse UI without a redeploy,
while a missing key or a Langfuse outage silently degrades to the in-code copy.
Seed the managed versions with scripts/seed_langfuse_prompts.py.
"""

import os

_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


def managed(name: str, fallback: str, cache_ttl_seconds: int = 300):
    """Return (text, prompt_client|None). Never raises — falls back to `fallback`."""
    if not _ENABLED:
        return fallback, None
    try:
        from langfuse import get_client

        client = get_client().get_prompt(
            name, fallback=fallback, cache_ttl_seconds=cache_ttl_seconds
        )
        return client.prompt, client
    except Exception:
        return fallback, None
