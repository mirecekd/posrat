"""AI chat widgets — floating FAB, header button, chat dialog.

Two public renderers plus their shared dialog implementation:

* :func:`render_ai_fab` — floating "robot" bottom-right button.
  Takes a ``context_provider`` callable so the caller decides what
  question context is injected into the system prompt at click time
  (Designer passes the currently selected question, Runner passes
  the currently displayed question).

* :func:`render_ai_header_button` — small smart-toy icon button for
  the top header. No question context — used for generic AWS chat
  and for admin sanity-check after saving the config.

Both widgets silently no-op when :class:`AISettings.enabled` is
false so turning the chat off in the admin panel instantly hides
them everywhere.

The chat dialog itself (:func:`_open_chat_dialog`) is shared so
styling and behaviour stay identical regardless of entry point.
"""

from __future__ import annotations

from typing import Callable, Optional

from nicegui import app, ui

from posrat.ai.chat_state import (
    clear_history,
    extract_user_and_assistant_text,
    load_history,
    save_history,
)
from posrat.ai.config import AISettings, load_ai_settings
from posrat.ai.context import build_question_context
from posrat.ai.mcp_client import build_mcp_clients, parse_mcp_config
from posrat.designer.browser import resolve_data_dir
from posrat.models import Question
from posrat.system.system_db import open_system_db, resolve_system_db_path


#: Tooltip shown on both widgets so users know what the robot does.
_FAB_TOOLTIP = "AI study assistant (Bedrock)"


def _load_settings() -> AISettings:
    """Load the singleton AI settings row from the system DB."""

    db = open_system_db(resolve_system_db_path(resolve_data_dir()))
    try:
        return load_ai_settings(db)
    finally:
        db.close()


def render_ai_fab(
    context_provider: Callable[[], Optional[Question]],
    *,
    context_id: str,
) -> None:
    """Render the floating bottom-right AI chat FAB.

    No-op when :class:`AISettings.enabled` is ``False`` — the admin
    controls visibility through the /admin "AI chat" tab.

    Args:
        context_provider: Callable returning the :class:`Question`
            whose context should seed the system prompt, or ``None``
            for generic chat. Invoked at click time so the context
            always reflects the latest UI state (not the render-time
            snapshot).
        context_id: Stable identifier for the conversation — used by
            :mod:`posrat.ai.chat_state` to namespace history under
            ``app.storage.tab``. Typical value: ``"designer:<exam>:<qid>"``
            or ``"runner:<session>:<qid>"``.
    """

    settings = _load_settings()
    if not settings.enabled:
        return

    with ui.page_sticky(position="bottom-right", x_offset=20, y_offset=20):
        ui.button(
            icon="smart_toy",
            on_click=lambda _evt=None: _open_chat_dialog(
                settings=settings,
                question=context_provider(),
                context_id=context_id,
            ),
        ).props("fab color=primary").tooltip(_FAB_TOOLTIP)


def render_ai_header_button() -> None:
    """Render the small smart-toy button inside the shared header.

    Always rendered without a question context — entry point for
    generic AWS chat and admin sanity-check. No-op when the chat
    is disabled (mirrors the FAB behaviour).
    """

    settings = _load_settings()
    if not settings.enabled:
        return

    ui.button(
        icon="smart_toy",
        on_click=lambda _evt=None: _open_chat_dialog(
            settings=settings,
            question=None,
            context_id="header:generic",
        ),
    ).props("flat color=white round").tooltip(_FAB_TOOLTIP)


def _open_chat_dialog(
    *,
    settings: AISettings,
    question: Optional[Question],
    context_id: str,
) -> None:
    """Open the shared chat dialog pre-seeded with history for ``context_id``."""

    # Deferred import avoids a circular import between this module
    # and the dialog implementation (which needs ``run_chat_turn``).
    from posrat.ai.chat_dialog import render_chat_dialog

    render_chat_dialog(
        settings=settings,
        question=question,
        context_id=context_id,
    )


__all__ = [
    "build_mcp_clients",
    "build_question_context",
    "clear_history",
    "extract_user_and_assistant_text",
    "load_history",
    "parse_mcp_config",
    "render_ai_fab",
    "render_ai_header_button",
    "save_history",
]
