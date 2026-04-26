"""AI settings singleton — DAO + Pydantic model.

Owns the ``ai_settings`` table introduced by system-schema migration
v4. A single row (``id = 1``) holds the admin-configurable parameters
of the AI chat panel: master switch, Bedrock model id + region,
optional system prompt, and the Claude Desktop-compatible MCP config
JSON.

**No secrets here.** AWS credentials come from the process-wide boto3
default chain (``AWS_PROFILE`` / env vars / IMDS / SSO cache). MCP
HTTP servers that need auth headers can embed them inside the JSON
blob (admin's responsibility), but nothing else is stashed here.

Validation of the MCP JSON itself lives in :mod:`posrat.ai.mcp_client`
— this module only persists the raw TEXT so operators can paste
whatever the aws-knowledge-mcp docs recommend and iterate without
schema churn.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


#: Default Bedrock model id — Anthropic Claude Sonnet 4.5.
#: The admin can override in the UI; this default keeps fresh
#: installs functional without any manual config.
DEFAULT_MODEL_ID: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"

#: Default AWS region for the Bedrock runtime endpoint.
DEFAULT_REGION: str = "eu-west-1"

#: Default system prompt handed to the agent when the admin has not
#: customised one. Kept terse — per-question context is injected as a
#: separate system message by :mod:`posrat.ai.context`.
DEFAULT_SYSTEM_PROMPT: str = (
    "You are a helpful AWS certification study tutor. "
    "Answer concisely in the same language as the question. "
    "Use the aws-knowledge MCP tool when a factual AWS reference "
    "is needed. Never pretend to know — if unsure, say so."
)


@dataclass(frozen=True)
class AISettings:
    """Immutable snapshot of the AI chat configuration.

    The ``enabled`` flag gates both the floating FAB and the header
    button: when ``False`` (or when the row is missing entirely), the
    AI widgets are not rendered at all. The Bedrock model id + region
    are used by :mod:`posrat.ai.agent` to build the Strands model
    wrapper. ``system_prompt`` may be ``None`` — in which case the
    agent factory falls back to :data:`DEFAULT_SYSTEM_PROMPT`.
    ``mcp_config_json`` is the raw Claude-Desktop-shaped blob; the
    MCP client parses it on demand.
    """

    enabled: bool
    model_id: str
    region: str
    system_prompt: Optional[str]
    mcp_config_json: Optional[str]
    updated_at: Optional[str]

    @classmethod
    def default(cls) -> "AISettings":
        """Return the default settings used when no row exists yet.

        ``enabled=False`` by default — a fresh install must not start
        making Bedrock calls until an admin explicitly opts in.
        """

        return cls(
            enabled=False,
            model_id=DEFAULT_MODEL_ID,
            region=DEFAULT_REGION,
            system_prompt=None,
            mcp_config_json=None,
            updated_at=None,
        )


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 (``...Z``) form."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _row_to_settings(row: sqlite3.Row) -> AISettings:
    """Hydrate a ``ai_settings`` row into :class:`AISettings`."""

    return AISettings(
        enabled=bool(row["enabled"]),
        model_id=str(row["model_id"]),
        region=str(row["region"]),
        system_prompt=row["system_prompt"],
        mcp_config_json=row["mcp_config_json"],
        updated_at=row["updated_at"],
    )


def load_ai_settings(db: sqlite3.Connection) -> AISettings:
    """Return the singleton AI settings row (or defaults when missing).

    Never raises on an empty table — a fresh install returns
    :meth:`AISettings.default`, which carries ``enabled=False`` so
    downstream widgets silently stay hidden until the admin saves the
    form at least once.
    """

    row = db.execute(
        "SELECT enabled, model_id, region, system_prompt,"
        " mcp_config_json, updated_at"
        " FROM ai_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        return AISettings.default()
    return _row_to_settings(row)


def save_ai_settings(
    db: sqlite3.Connection,
    *,
    enabled: bool,
    model_id: str,
    region: str,
    system_prompt: Optional[str] = None,
    mcp_config_json: Optional[str] = None,
) -> AISettings:
    """Upsert the singleton AI settings row.

    Trims whitespace on text inputs and normalises empty strings to
    ``None`` so the admin form "clear this field" UX round-trips
    through the DB cleanly. Returns the freshly hydrated settings so
    callers can update their caches / refresh the UI immediately.

    Raises:
        ValueError: when ``model_id`` or ``region`` are blank after
            trimming — both are mandatory Bedrock parameters and
            silently saving empty values would blow up at the next
            agent invocation with a confusing boto error.
    """

    model_id = (model_id or "").strip()
    region = (region or "").strip()
    if not model_id:
        raise ValueError("model_id must not be empty")
    if not region:
        raise ValueError("region must not be empty")

    prompt_clean: Optional[str] = None
    if system_prompt is not None:
        stripped = system_prompt.strip()
        prompt_clean = stripped or None

    mcp_clean: Optional[str] = None
    if mcp_config_json is not None:
        stripped = mcp_config_json.strip()
        mcp_clean = stripped or None

    updated_at = _utc_now_iso()
    with db:
        db.execute(
            "INSERT INTO ai_settings (id, enabled, model_id, region,"
            " system_prompt, mcp_config_json, updated_at)"
            " VALUES (1, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            "   enabled = excluded.enabled,"
            "   model_id = excluded.model_id,"
            "   region = excluded.region,"
            "   system_prompt = excluded.system_prompt,"
            "   mcp_config_json = excluded.mcp_config_json,"
            "   updated_at = excluded.updated_at",
            (
                1 if enabled else 0,
                model_id,
                region,
                prompt_clean,
                mcp_clean,
                updated_at,
            ),
        )

    return AISettings(
        enabled=enabled,
        model_id=model_id,
        region=region,
        system_prompt=prompt_clean,
        mcp_config_json=mcp_clean,
        updated_at=updated_at,
    )


__all__ = [
    "AISettings",
    "DEFAULT_MODEL_ID",
    "DEFAULT_REGION",
    "DEFAULT_SYSTEM_PROMPT",
    "load_ai_settings",
    "save_ai_settings",
]
