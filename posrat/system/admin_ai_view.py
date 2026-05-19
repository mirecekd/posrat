"""Admin → **AI chat** tab.

Singleton-row settings form backed by :mod:`posrat.ai.config`:

* **Enabled** checkbox (master switch — the floating FAB and the
  header robot button only render when this is on).
* **Bedrock model id** + **AWS region** (Strands uses the process-wide
  boto3 default credential chain; this panel never asks for keys).
* **System prompt** textarea (optional; empty falls back to
  :data:`posrat.ai.config.DEFAULT_SYSTEM_PROMPT`).
* **MCP servers (JSON)** textarea — Claude Desktop-compatible
  ``{"mcpServers": {...}}`` blob. Validation is light on save (is it
  JSON? Does it have an ``mcpServers`` object?) so operators can
  paste aws-knowledge-mcp snippets verbatim without jumping through
  a schema.

The save button commits via :func:`save_ai_settings`; the form
refreshes so the user sees the ``updated_at`` caption update.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from nicegui import ui

from posrat.ai.config import (
    DEFAULT_ENRICH_PROMPT,
    DEFAULT_MODEL_ID,
    DEFAULT_REGION,
    DEFAULT_SYSTEM_PROMPT,
    AISettings,
    load_ai_settings,
    save_ai_settings,
)

from posrat.designer.browser import resolve_data_dir
from posrat.system.system_db import open_system_db, resolve_system_db_path


#: Placeholder shown inside the MCP JSON textarea when the admin has
#: not yet configured anything. Uses the aws-knowledge-mcp public
#: endpoint — the primary target for this feature's MVP.
_MCP_JSON_PLACEHOLDER = json.dumps(
    {
        "mcpServers": {
            "aws-knowledge": {
                "url": "https://knowledge-mcp.global.api.aws"
            }
        }
    },
    indent=2,
)


def _validate_mcp_json(raw: str) -> Optional[str]:
    """Return an error message when ``raw`` is not a valid MCP config.

    Returns ``None`` when the blob parses into a dict with a top-level
    ``"mcpServers"`` object (the Claude Desktop schema shared by
    Strands). Empty / whitespace-only input is treated as "no MCP"
    and also returns ``None`` — operators can disable MCP by clearing
    the field.
    """

    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc.msg} (line {exc.lineno})"
    if not isinstance(parsed, dict):
        return "Top-level value must be a JSON object."
    servers = parsed.get("mcpServers")
    if servers is None:
        return 'Missing "mcpServers" key.'
    if not isinstance(servers, dict):
        return '"mcpServers" must be a JSON object.'
    return None


@ui.refreshable
def render_ai_tab() -> None:
    """Render the /admin AI chat tab body.

    Reads the singleton settings row each refresh — keeps the form in
    sync with any save that happened elsewhere (e.g. a parallel
    admin tab).
    """

    ui.label("AI chat").classes("text-h6 q-mt-md")
    ui.label(
        "Configure the Bedrock-backed chat assistant. Credentials "
        "come from the process-wide AWS default chain "
        "(AWS_PROFILE / env vars / IMDS); no secrets are stored "
        "here."
    ).classes("text-caption text-grey q-mb-md")

    db = _open_system_db()
    try:
        settings = load_ai_settings(db)
    finally:
        db.close()

    _render_form(settings)


def _render_form(settings: AISettings) -> None:
    """Render the form widgets pre-filled from ``settings``."""

    enabled = ui.checkbox("Enabled", value=settings.enabled)

    with ui.row().classes("w-full q-gutter-md no-wrap"):
        model = ui.input(
            label="Bedrock model id",
            value=settings.model_id or DEFAULT_MODEL_ID,
            placeholder=DEFAULT_MODEL_ID,
        ).classes("col-grow").props("dense")
        region = ui.input(
            label="AWS region",
            value=settings.region or DEFAULT_REGION,
            placeholder=DEFAULT_REGION,
        ).classes("col-grow").props("dense")

    prompt = ui.textarea(
        label="System prompt (optional)",
        value=settings.system_prompt or "",
        placeholder=DEFAULT_SYSTEM_PROMPT,
    ).classes("w-full").props("outlined type=textarea rows=4")

    # MCP JSON textarea — outlined + fixed rows so it stays
    # comfortably wide even when empty, and monospace font so
    # bracket alignment is obvious when editing the blob by hand.
    mcp = ui.textarea(
        label="MCP servers (JSON, optional)",
        value=settings.mcp_config_json or "",
        placeholder=_MCP_JSON_PLACEHOLDER,
    ).classes("w-full").props(
        "outlined type=textarea rows=10 "
        "input-style=font-family:monospace"
    )

    # Auto-enrich prompt textarea. The Designer "Auto-enrich" button
    # sends this prompt verbatim to the LLM whenever the operator
    # one-clicks an explanation; tweaking it in the admin UI lets
    # operators iterate on the markdown template (heading structure,
    # formatting rules, language) without redeploying.
    #
    # The textarea is pre-filled with the built-in default whenever
    # the admin has not customised it yet. Using ``placeholder``
    # would only ghost the text into the background where it cannot
    # be selected, copied or tweaked — which defeats the whole point
    # of exposing the prompt for editing. On save, the helper below
    # treats "value identical to the default" as "still using the
    # default" and persists ``NULL`` so future default changes
    # propagate automatically.
    enrich_initial = settings.enrich_prompt or DEFAULT_ENRICH_PROMPT
    enrich = ui.textarea(
        label="Auto-enrich prompt",
        value=enrich_initial,
    ).classes("w-full").props(
        "outlined type=textarea rows=20 "
        "input-style=font-family:monospace"
    )
    ui.label(
        "Sent verbatim by the Designer Auto-enrich button. Reset "
        "to the built-in template by clearing the field and saving."
    ).classes("text-caption text-grey q-mt-xs")

    def _on_reset_enrich() -> None:
        """Restore the textarea to the built-in default template."""

        enrich.value = DEFAULT_ENRICH_PROMPT

    ui.button(
        "Reset to default template",
        icon="restart_alt",
        on_click=_on_reset_enrich,
    ).props("flat dense").classes("q-mt-xs")



    if settings.updated_at:

        ui.label(f"Last saved: {settings.updated_at}").classes(
            "text-caption text-grey q-mt-xs"
        )

    def _on_save() -> None:
        error = _validate_mcp_json(mcp.value or "")
        if error is not None:
            ui.notify(f"MCP JSON: {error}", type="negative")
            return

        # Treat "value identical to the built-in default" as
        # "still using the default" — persisted as NULL so future
        # default-template changes propagate to this admin without
        # them having to manually re-paste.
        enrich_value = (enrich.value or "").strip() or None
        if enrich_value == DEFAULT_ENRICH_PROMPT.strip():
            enrich_value = None

        db = _open_system_db()
        try:
            save_ai_settings(
                db,
                enabled=bool(enabled.value),
                model_id=model.value or "",
                region=region.value or "",
                system_prompt=prompt.value or None,
                mcp_config_json=mcp.value or None,
                enrich_prompt=enrich_value,
            )


        except (ValueError, sqlite3.DatabaseError) as exc:
            ui.notify(f"Cannot save: {exc}", type="negative")
            return
        finally:
            db.close()

        ui.notify("AI settings saved.", type="positive")
        render_ai_tab.refresh()

    with ui.row().classes("justify-end q-mt-md w-full"):
        ui.button("Save", on_click=_on_save).props("color=primary")


def _open_system_db() -> sqlite3.Connection:
    """Open the system DB using the shared data-dir resolver."""

    return open_system_db(resolve_system_db_path(resolve_data_dir()))


__all__ = ["render_ai_tab"]
