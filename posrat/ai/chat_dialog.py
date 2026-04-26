"""Chat dialog — modal with message bubbles + prompt textarea.

Opened by :func:`posrat.ai.widget.render_ai_fab` and
:func:`posrat.ai.widget.render_ai_header_button`. Keeps streaming
UI, history persistence and error reporting in one place so both
entry points share identical look & feel.

The dialog is intentionally a :class:`ui.dialog` (not a ``ui.drawer``)
so it works uniformly on every route — the Designer and Runner
already use their own drawers for navigation, and stacking drawers
would fight for the same screen edge.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from nicegui import app, ui

from posrat.ai.agent import run_chat_turn
from posrat.ai.chat_state import (
    clear_history,
    extract_user_and_assistant_text,
    load_history,
    save_history,
)
from posrat.ai.config import AISettings
from posrat.ai.context import build_question_context
from posrat.ai.mcp_client import build_mcp_clients, parse_mcp_config
from posrat.models import Question


def render_chat_dialog(
    *,
    settings: AISettings,
    question: Optional[Question],
    context_id: str,
) -> None:
    """Build and open the chat dialog for ``context_id``.

    Re-reads the conversation history from ``app.storage.tab`` each
    time, so re-opening the dialog on the same question restores the
    previous exchange.
    """

    storage = app.storage.tab
    history: list[dict] = load_history(storage, context_id)
    question_ctx = build_question_context(question) if question else ""

    with ui.dialog() as dialog, ui.card().classes(
        "w-full max-w-2xl no-wrap"
    ).style("width: 640px; max-height: 80vh;"):
        # Header
        with ui.row().classes(
            "items-center q-pa-sm q-gutter-sm bg-grey-2 w-full"
        ):
            ui.icon("smart_toy").classes("text-primary")
            title = "AI study assistant"
            if question is not None:
                title = f"AI assistant — {question.type}"
            ui.label(title).classes("text-subtitle1 text-weight-bold")
            ui.space()

            def _on_clear() -> None:
                clear_history(storage, context_id)
                messages_container.clear()
                with messages_container:
                    _render_intro(question_ctx)

            ui.button(
                icon="clear_all",
                on_click=_on_clear,
            ).props("flat dense").tooltip("Clear chat history")
            ui.button(
                icon="close",
                on_click=dialog.close,
            ).props("flat dense")

        # Messages area — scrollable; bubbles render into
        # ``messages_container`` so we can refresh / append live.
        messages_container = ui.scroll_area().classes(
            "col-grow q-pa-sm"
        ).style("height: 50vh;")

        with messages_container:
            if history:
                _render_bubbles(
                    extract_user_and_assistant_text(history)
                )
            else:
                _render_intro(question_ctx)

        # Prompt row.
        prompt = ui.textarea(
            placeholder="Ask about this question, AWS services, best practices…",
        ).classes("w-full").props("autogrow dense outlined rows=2")

        async def _on_send() -> None:
            text = (prompt.value or "").strip()
            if not text:
                return
            prompt.value = ""

            # Append the user bubble immediately so the UI feels
            # responsive before the first streamed token arrives.
            with messages_container:
                _render_bubble("user", text)

            # Streaming assistant bubble — we mutate ``content`` on
            # each token delta to rebuild markdown in place.
            with messages_container:
                assistant_label = ui.chat_message(
                    text="…", name="Assistant", sent=False,
                ).props("bg-color=grey-3")

            assistant_buffer = {"text": ""}
            try:
                await _stream_assistant_reply(
                    settings=settings,
                    question_ctx=question_ctx,
                    user_prompt=text,
                    history=history,
                    storage=storage,
                    context_id=context_id,
                    on_delta=lambda delta: _append_delta(
                        assistant_label, assistant_buffer, delta
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — surface everything
                ui.notify(f"AI error: {exc}", type="negative")
                assistant_label.text = f"⚠ Error: {exc}"

        with ui.row().classes("items-end q-pa-sm w-full q-gutter-sm"):
            ui.space()
            ui.button(
                "Send",
                icon="send",
                on_click=lambda _evt=None: asyncio.create_task(_on_send()),
            ).props("color=primary")

        prompt.on(
            "keydown.enter",
            lambda _evt=None: asyncio.create_task(_on_send()),
        )

    dialog.open()


def _render_intro(question_ctx: str) -> None:
    """Render the empty-state intro shown before any chat turn."""

    if question_ctx:
        ui.label(
            "I can see the current question and its choices. Ask me "
            "anything — I'll use AWS docs via MCP when useful."
        ).classes("text-caption text-grey q-pa-md")
    else:
        ui.label(
            "Generic AWS chat — no question context attached."
        ).classes("text-caption text-grey q-pa-md")


def _render_bubbles(pairs: list[tuple[str, str]]) -> None:
    """Render existing (role, text) pairs into chat bubbles."""

    for role, text in pairs:
        _render_bubble(role, text)


def _render_bubble(role: str, text: str) -> None:
    """Render a single chat bubble."""

    if role == "user":
        ui.chat_message(
            text=text, name="You", sent=True,
        ).props("bg-color=primary text-color=white")
    else:
        ui.chat_message(
            text=text, name="Assistant", sent=False,
        ).props("bg-color=grey-3")


def _append_delta(label, buffer: dict, delta: str) -> None:
    """Append a streamed token delta to the assistant bubble."""

    buffer["text"] = buffer["text"] + delta
    label.text = buffer["text"]


async def _stream_assistant_reply(
    *,
    settings: AISettings,
    question_ctx: str,
    user_prompt: str,
    history: list[dict],
    storage: dict,
    context_id: str,
    on_delta,
) -> None:
    """Drive :func:`run_chat_turn` and persist the trace when done."""

    servers = parse_mcp_config(settings.mcp_config_json)
    mcp_clients = build_mcp_clients(servers) if servers else []

    async for event in run_chat_turn(
        settings,
        user_prompt,
        question_context=question_ctx,
        mcp_clients=mcp_clients,
        prior_messages=history,
    ):
        if isinstance(event, dict):
            delta = event.get("data")
            if isinstance(delta, str):
                on_delta(delta)
            trace = event.get("complete_messages")
            if isinstance(trace, list):
                save_history(storage, context_id, trace)


__all__ = ["render_chat_dialog"]
