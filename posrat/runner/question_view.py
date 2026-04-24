"""Main question view — header, text, inputs, submit/review footer.

Wires together the header row (mode badge + prev/next nav + candidate
info), the question text (``ui.markdown`` for embedded images), the
per-type input renderer from :mod:`posrat.runner.choice_inputs`, and
the submit / feedback footer. Keeps the rendering code in one place so
the overall layout decisions (progress bar, separators, spacing) stay
visible at a glance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import ui

from posrat.models import Question
from posrat.runner.choice_inputs import (
    render_feedback_footer,
    render_hotspot_input,
    render_multi_choice_input,
    render_next_area,
    render_single_choice_input,
)
from posrat.runner.submit_flow import (
    finalise_session,
    navigate_question,
)
from posrat.runner.orchestrator import compute_session_score
from posrat.runner.timer_widget import render_countdown
from posrat.storage import get_session, list_questions, open_db



def _load_session_question(
    exam_path: Path, exam_id: str, question_id: str
) -> Optional[Question]:
    """Re-read a single :class:`Question` from the session's exam DB.

    We intentionally re-read on every render so edits made in the
    Designer (e.g. fixed typo in explanation) show up on the Runner's
    next refresh without the candidate needing to restart the session.
    """

    db = open_db(exam_path)
    try:
        for question in list_questions(db, exam_id):
            if question.id == question_id:
                return question
    finally:
        db.close()
    return None


def _render_live_score(stash: dict) -> None:
    """Render the live "Current Score: X / 1000 (pass ≥ 700)" chip.

    The raw score is computed against the session snapshot by
    :func:`compute_session_score`, which already handles the default
    ``target_score=1000`` / ``passing_score=700`` fallbacks applied in
    :func:`start_runner_session`. We read the live session (including
    every recorded :class:`Answer`) on each render so the number
    updates immediately after a training-mode submit refresh.

    Rendered as two small text-caption labels in the header row. The
    chip is silently skipped when the session row is missing or has
    no ``target_score`` (legacy sessions started before the
    700/1000 default landed) — the view must never blow up just
    because the header can't show a score.
    """

    exam_path_str = stash.get("exam_path")
    session_id = stash.get("session_id")
    if not exam_path_str or not session_id:
        return

    exam_path = Path(str(exam_path_str))
    if not exam_path.is_file():
        return

    try:
        db = open_db(exam_path)
    except Exception:  # pragma: no cover - defensive
        return
    try:
        session = get_session(db, str(session_id))
    finally:
        db.close()

    if session is None or session.target_score is None:
        return

    score = compute_session_score(session)
    raw = score.raw_score if score.raw_score is not None else 0

    ui.label(f"Score: {raw} / {session.target_score}").classes(
        "text-caption text-grey"
    )
    if session.passing_score is not None:
        ui.label(f"(pass ≥ {session.passing_score})").classes(
            "text-caption text-grey"
        )


def _render_header(
    stash: dict,
    index: int,
    total: int,
) -> None:
    """Render the header row: mode badge + prev/next nav + candidate info."""

    with ui.row().classes("items-center q-gutter-md w-full"):
        ui.badge(str(stash.get("mode")).upper()).props(
            "color=primary" if stash.get("mode") == "training" else "color=secondary"
        )

        prev_button = ui.button(
            icon="chevron_left",
            on_click=lambda _evt=None: navigate_question(stash, -1),
        ).props("flat dense")
        if index == 0:
            prev_button.props("disable")

        ui.label(f"Question {index + 1} / {total}").classes("text-subtitle2")

        next_button = ui.button(
            icon="chevron_right",
            on_click=lambda _evt=None: navigate_question(stash, +1),
        ).props("flat dense")
        if index >= total - 1:
            next_button.props("disable")

        ui.space()
        # Live VCE-style "Current Score: 15 / 1000 (pass ≥ 700)" chip.
        # Re-reads the session on every render so training-mode
        # submits immediately reflect in the header.
        _render_live_score(stash)
        ui.label(str(stash.get("candidate_name") or "")).classes(
            "text-caption text-grey"
        )
        # Live MM:SS countdown (no-op on timer-less sessions). Owns
        # the "time up" modal → force_finish_session → results view.
        render_countdown(stash)



def render_question_view(stash: dict) -> None:
    """Render the current question and its input widgets."""

    exam_path = Path(str(stash["exam_path"]))
    exam_id = str(stash["exam_id"])
    question_ids: list[str] = list(stash.get("question_ids") or [])
    index = int(stash.get("current_index", 0))
    question_id = question_ids[index]

    question = _load_session_question(exam_path, exam_id, question_id)
    if question is None:
        # Guard against a mid-session Designer delete. We finalise the
        # session so ``finished_at`` is stamped, then let the refresh
        # bubble up to the results view.
        ui.notify(
            f"Question {question_id} is missing from the exam — session ended.",
            type="negative",
        )
        finalise_session(stash)
        from posrat.runner.page import _render_runner_body

        _render_runner_body.refresh()
        return

    _render_header(stash, index, len(question_ids))

    progress_value = index / max(len(question_ids), 1)
    with ui.linear_progress(value=progress_value).props("instant-feedback"):
        ui.label(f"{progress_value * 100:.1f} %").classes(
            "absolute-center text-sm text-white"
        )

    ui.separator().classes("q-my-sm")

    # Question text as markdown (embeds ![](/media/assets/...) images).
    ui.markdown(question.text).classes("text-body1")

    # Review mode: locked inputs + green/red highlights. Triggered by
    # a wrong training-mode submit; cleared by prev/next nav or by the
    # feedback "Continue" button.
    feedback_pending = stash.get("feedback_pending_for") == question.id

    # Working copy of the user's in-progress answer for this question.
    # Pre-seeded from ``given_answers`` so prev/next revisits replay
    # the last submission; empty dict otherwise.
    given_answers = stash.setdefault("given_answers", {})
    previous_payload = given_answers.get(question.id)
    payload_holder: dict[str, object] = {
        "payload": (
            dict(previous_payload)
            if isinstance(previous_payload, dict)
            else None
        )
    }

    if question.type == "single_choice":
        render_single_choice_input(
            question, stash, payload_holder, feedback_pending
        )
    elif question.type == "multi_choice":
        render_multi_choice_input(
            question, stash, payload_holder, feedback_pending
        )
    elif question.type == "hotspot":
        render_hotspot_input(
            question, stash, payload_holder, feedback_pending
        )
    else:  # pragma: no cover - defensive
        ui.label(f"Unknown type: {question.type}").classes("text-negative")

    ui.separator().classes("q-my-sm")

    if feedback_pending:
        render_feedback_footer(question, stash)
    else:
        render_next_area(question, stash, payload_holder)


__all__ = ["render_question_view"]
