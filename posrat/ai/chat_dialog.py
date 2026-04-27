"""Chat dialog — modal with message bubbles + prompt textarea.

Opened by :func:`posrat.ai.widget.render_ai_fab` and
:func:`posrat.ai.widget.render_ai_header_button`. Keeps streaming
UI, history persistence and error reporting in one place so both
entry points share identical look & feel.

The dialog is intentionally a :class:`ui.dialog` (not a ``ui.drawer``)
so it works uniformly on every route — the Designer and Runner
already use their own drawers for navigation, and stacking drawers
would fight for the same screen edge.

**Streaming implementation note.** We render each message as a
:class:`ui.card` + :class:`ui.markdown` pair instead of
:class:`ui.chat_message`. The latter's ``text`` attribute does not
re-render on assignment in NiceGUI 3 (it's baked into the slot at
construction time), which meant the assistant's streamed tokens
never surfaced to the user. ``ui.markdown.content`` round-trips
cleanly on mutation and also renders backticks / bold / code
blocks for free — both wins for an AI reply.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from nicegui import app, ui

from posrat.ai.agent import run_chat_turn
from posrat.ai.chat_state import (
    clear_history,
    extract_user_and_assistant_text,
    load_history,
    save_history,
)
from posrat.ai.config import DEFAULT_ENRICH_PROMPT, AISettings
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
        "no-wrap column"
    ).style("width: 720px; max-width: 95vw; max-height: 85vh;"):
        # --- Header row -----------------------------------------------
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

            # Designer-only shortcut: prepend the latest assistant
            # reply into the selected question's Explanation/Reference
            # field and close the dialog so the user immediately sees
            # the updated editor preview. Gated on ``context_id``
            # prefix + an attached question so Runner / header chats
            # don't surface a button that would have nothing to write
            # to. The actual DB hit lives in :func:`_insert_last_reply_into_explanation`.
            if question is not None and context_id.startswith("designer:"):
                # One-click flow: fire the canned enrichment prompt,
                # stream the reply, then prepend it into Explanation
                # as soon as the stream finishes. Saves three clicks
                # (type prompt, send, "To Explanation") down to one.
                ui.button(
                    "Auto-enrich",
                    icon="auto_fix_high",
                    on_click=lambda _evt=None: _on_auto_enrich(),
                ).props("flat dense color=primary").tooltip(
                    "Send the canned enrichment prompt and automatically "
                    "save the reply into Explanation/Reference"
                )
                ui.button(
                    "To Explanation",
                    icon="playlist_add",
                    on_click=lambda _evt=None: _insert_last_reply_into_explanation(
                        history=history,
                        question=question,
                        dialog=dialog,
                    ),
                ).props("flat dense").tooltip(
                    "Prepend the last AI reply into the question's "
                    "Explanation/Reference field"
                )

            ui.button(

                icon="clear_all",
                on_click=_on_clear,
            ).props("flat dense").tooltip("Clear chat history")
            ui.button(
                icon="close",
                on_click=dialog.close,
            ).props("flat dense")

        # --- Scrollable message area ----------------------------------
        messages_container = ui.scroll_area().classes(
            "col-grow q-pa-sm"
        ).style("height: 55vh; min-height: 300px;")

        with messages_container:
            if history:
                _render_bubbles(
                    extract_user_and_assistant_text(history)
                )
            else:
                _render_intro(question_ctx)

        # --- Prompt row -----------------------------------------------
        prompt = ui.textarea(
            placeholder=(
                "Ask about this question, AWS services, best practices… "
                "(Ctrl/Cmd + Enter to send)"
            ),
        ).classes("w-full q-px-sm").props(
            "outlined type=textarea rows=3 autogrow=false"
        )

        async def _run_chat_turn(user_text: str) -> bool:
            """Render user + assistant bubbles and stream the reply.

            Returns ``True`` on successful completion, ``False`` when
            the stream raised. Shared by :func:`_on_send` (manual
            prompt) and :func:`_on_auto_enrich` (canned prompt) so
            both entry points use identical UI rendering + history
            persistence without duplicating 40 lines of stream setup.
            """

            # User bubble — render immediately so the UI feels
            # responsive before the first streamed token.
            with messages_container:
                _render_bubble("user", user_text)

            # Assistant bubble placeholder — we mutate the
            # ``ui.markdown.content`` attribute on each token delta.
            with messages_container:
                assistant_md = _render_bubble("assistant", "_Thinking…_")

            assistant_buffer = {"text": ""}
            try:
                await _stream_assistant_reply(
                    settings=settings,
                    question_ctx=question_ctx,
                    user_prompt=user_text,
                    history=history,
                    storage=storage,
                    context_id=context_id,
                    on_delta=lambda delta: _append_delta(
                        assistant_md, assistant_buffer, delta
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — surface everything
                ui.notify(f"AI error: {exc}", type="negative")
                assistant_md.content = f"⚠ **Error:** {exc}"
                return False

            # If the stream produced zero deltas (e.g. tool-only
            # turn), show at least the persisted assistant text so
            # the bubble doesn't stay stuck on "Thinking…".
            if not assistant_buffer["text"]:
                trail = extract_user_and_assistant_text(history)
                if trail and trail[-1][0] == "assistant":
                    assistant_md.content = trail[-1][1]
                else:
                    assistant_md.content = "_(no reply)_"
            return True

        async def _on_send() -> None:
            text = (prompt.value or "").strip()
            if not text:
                return
            prompt.value = ""
            await _run_chat_turn(text)

        async def _on_auto_enrich() -> None:
            """Fire the canned enrichment prompt + auto-commit the reply.

            Feeds :data:`DEFAULT_ENRICH_PROMPT` through the same
            streaming pipeline as a manual send, then — on success —
            prepends the fresh assistant reply into the question's
            Explanation/Reference field via
            :func:`_insert_last_reply_into_explanation` (which closes
            the dialog and refreshes the Designer body).
            """

            if question is None:
                # Defensive — the button is only rendered for
                # Designer + attached question, but the outer closure
                # captures ``question`` as ``Optional`` so narrow
                # locally for the type checker and for safety.
                return
            succeeded = await _run_chat_turn(DEFAULT_ENRICH_PROMPT)
            if not succeeded:
                return
            _insert_last_reply_into_explanation(
                history=history,
                question=question,
                dialog=dialog,
            )


        with ui.row().classes("items-center q-pa-sm w-full q-gutter-sm"):
            ui.space()
            ui.button(
                "Send",
                icon="send",
                on_click=_on_send,
            ).props("color=primary")

        # Ctrl/Cmd + Enter sends. Plain Enter is left as a newline
        # so multi-paragraph questions work naturally.
        prompt.on("keydown.ctrl.enter", _on_send)
        prompt.on("keydown.meta.enter", _on_send)

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


def _render_bubble(role: str, text: str):
    """Render a single chat bubble and return its markdown element.

    Uses :class:`ui.card` wrapper plus :class:`ui.markdown` so we can
    mutate ``content`` later for streaming. Returns the markdown
    element so the caller can update it as tokens arrive.
    """

    bg = "bg-primary" if role == "user" else "bg-grey-3"
    text_class = "text-white" if role == "user" else ""
    align = "self-end" if role == "user" else "self-start"

    with ui.card().classes(
        f"{bg} {text_class} {align} q-pa-sm q-mb-sm"
    ).style("max-width: 85%;"):
        ui.label("You" if role == "user" else "Assistant").classes(
            "text-caption text-weight-medium"
        )
        # ``extras`` enables GitHub-flavoured markdown bits the model
        # likes to emit (tables, fenced code blocks, strikethrough,
        # task lists). Without them ``ui.markdown`` falls back to the
        # plain markdown2 parser which renders tables as raw pipes.
        md = ui.markdown(
            content=text,
            extras=[
                "tables",
                "fenced-code-blocks",
                "strike",
                "task_list",
                "cuddled-lists",
                "break-on-newline",
            ],
        ).classes(
            "q-mt-xs"
            + (" text-white" if role == "user" else "")
        )
    return md



def _append_delta(md, buffer: dict, delta: str) -> None:
    """Append a streamed token delta to the assistant bubble."""

    buffer["text"] = buffer["text"] + delta
    md.content = buffer["text"]


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
        if not isinstance(event, dict):
            continue
        delta = event.get("data")
        if isinstance(delta, str):
            on_delta(delta)
            continue
        trace = event.get("complete_messages")
        if isinstance(trace, list):
            save_history(storage, context_id, trace)
            # Also feed the caller's live reference so the
            # "no deltas seen" fallback can grab the final text.
            history.clear()
            history.extend(trace)


def _insert_last_reply_into_explanation(
    *,
    history: list[dict],
    question: Question,
    dialog,
) -> None:
    """Prepend the latest assistant reply into the question's Explanation.

    Scans ``history`` for the most recent assistant message, concatenates
    it with the current on-disk explanation (separated by a blank line),
    and persists via
    :func:`posrat.designer.browser.update_question_explanation_in_open_exam`.
    On success the Designer body is refreshed so the textarea + live
    markdown preview pick up the new value, and the chat dialog closes.

    Standard Designer notify matrix: ``True`` = positive toast, ``False``
    = warning (stale question id), ``None`` = warning ("no exam open"),
    ``ValueError`` / ``sqlite3.DatabaseError`` = negative toast.
    """

    # Deferred imports — the Designer browser module pulls in a lot of
    # UI wiring we don't want to drag into the AI package graph just
    # for this one helper. Keeping them local mirrors the pattern used
    # by :mod:`posrat.ai.widget`.
    from posrat.designer.browser import (
        _render_designer_body,
        load_questions_for_open_exam,
        update_question_explanation_in_open_exam,
    )

    trail = extract_user_and_assistant_text(history)
    assistant_text: Optional[str] = None
    for role, text in reversed(trail):
        if role == "assistant" and text.strip():
            assistant_text = text
            break

    if assistant_text is None:
        ui.notify("No AI reply to insert yet.", type="warning")
        return

    # Re-read the fresh explanation from disk so we don't clobber
    # edits made in the editor between opening the dialog and clicking
    # "To Explanation". ``question`` passed in by the caller is a
    # snapshot captured when the dialog opened.
    fresh_questions = load_questions_for_open_exam()
    existing: Optional[str] = question.explanation
    if fresh_questions:
        for q in fresh_questions:
            if q.id == question.id:
                existing = q.explanation
                break

    if existing and existing.strip():
        new_explanation = f"{assistant_text}\n\n{existing}"
    else:
        new_explanation = assistant_text

    try:
        updated = update_question_explanation_in_open_exam(
            question.id, new_explanation
        )
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot save explanation: {exc}", type="negative")
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return
    if not updated:
        ui.notify(
            f"Question {question.id} no longer exists.", type="warning"
        )
        _render_designer_body.refresh()
        return

    ui.notify("AI reply inserted into Explanation.", type="positive")
    _render_designer_body.refresh()
    dialog.close()


__all__ = ["render_chat_dialog"]
