"""POSRAT AI chat subsystem.

Provides a Bedrock-backed chat assistant via Strands Agents with
optional MCP server integration. Configuration lives as a singleton
row in ``system.sqlite`` (table ``ai_settings``) and is managed
through the ``/admin`` panel.

Public entry points:

* :func:`posrat.ai.config.load_ai_settings` — read the singleton.
* :func:`posrat.ai.config.save_ai_settings` — upsert the singleton.

Strands / boto3 / MCP imports are kept inside the respective submodule
bodies (``agent``, ``mcp_client``) so importing :mod:`posrat.ai` stays
cheap — the admin panel loads the config module but never needs the
LLM stack until the user actually opens the chat.
"""

from __future__ import annotations

from posrat.ai.config import (
    AISettings,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_SYSTEM_PROMPT,
    load_ai_settings,
    save_ai_settings,
)

__all__ = [
    "AISettings",
    "DEFAULT_MODEL_ID",
    "DEFAULT_REGION",
    "DEFAULT_SYSTEM_PROMPT",
    "load_ai_settings",
    "save_ai_settings",
]
