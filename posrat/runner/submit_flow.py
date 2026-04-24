"""Submit / navigation flow handlers for the Runner question view.

Every side-effect that mutates the session stash (advance cursor,
persist answer, jump prev/next, close session) lives here so the
individual render modules (``picker_view``, ``question_view``,
``choice_inputs``) can stay purely presentational.

The only NiceGUI interaction is notify toasts + the refresh of the
root :func:`posrat.runner.page._render_runner_body` refreshable, which
is imported lazily to keep the import graph acyclic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from nicegui import app, ui

from posrat.models import Question
from posrat.runner.grading import decode_given_json
from posrat.runner.orchestrator import submit_runner_answer
from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    advance_session_stash,
)
from posrat.storage import finish_session, open_db


def _refresh_runner_body() -> None:
    """Lazily call ``page._render_runner_body.refresh()``.

    Imported at call time rather than at module load so
    :mod:`posrat.runner.submit_flow` stays importable from
    ``page.py`` (which defines the refreshable) without a cycle.
    """

    from posrat.runner.page import _render_runner_body

    _render_runner_body.refresh()


def validate_submission_shape(
    question: Question, payload: object
) -> Optional[str]:
    """Return a friendly error message when ``payload`` is not submittable.

    Returns ``None`` when the answer is well-formed (enough picks,
    every step answered, …). The actual correctness grading happens
    server-side in :func:`~posrat.runner.grading.grade_answer`; this
    helper guards against premature submits — e.g. the VCE-style rule
    "Pick 3 answers" on a 3-correct multi_choice question.
    """

    if question.type == "single_choice":
        if not isinstance(payload, dict) or not payload.get("choice_id"):
            return "Pick one answer."
        return None

    if question.type == "multi_choice":
        expected = sum(1 for c in question.choices if c.is_correct)
        picks: list = []
        if isinstance(payload, dict):
            raw = payload.get("choice_ids")
            if isinstance(raw, list):
                picks = [str(x) for x in raw if x]
        if len(picks) != expected:
            return (
                f"Pick exactly {expected} answer(s) "
                f"(selected {len(picks)})."
            )
        return None

    if question.type == "hotspot":
        if question.hotspot is None:
            return "Invalid hotspot."
        picks_dict: dict = {}
        if isinstance(payload, dict):
            raw = payload.get("step_option_ids")
            if isinstance(raw, dict):
                picks_dict = raw
        missing = [
            step.prompt or step.id
            for step in question.hotspot.steps
            if not picks_dict.get(step.id)
        ]
        if missing:
            return "Fill in every step: " + ", ".join(missing[:3])
        return None

    return f"Unsupported question type: {question.type}"  # pragma: no cover


def on_submit_answer(
    question: Question,
    stash: dict,
    payload_holder: dict[str, object],
) -> None:
    """Persist the answer, then branch on mode + correctness.

    * ``exam`` mode and correct training answers → silent advance.
    * ``training`` mode + wrong answer → set ``feedback_pending_for``
      so the question view re-renders with inline review highlighting
      and the "Continue" button.
    """

    payload = payload_holder.get("payload")
    validation_error = validate_submission_shape(question, payload)
    if validation_error is not None:
        ui.notify(validation_error, type="warning")
        return

    try:
        is_correct, given_json = submit_runner_answer(
            Path(str(stash["exam_path"])),
            session_id=str(stash["session_id"]),
            question_id=question.id,
            payload=payload,
        )
    except (LookupError, ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot save answer: {exc}", type="negative")
        return

    # Remember this submission so prev/next revisits can re-seed inputs
    # and so the feedback view can highlight what the user picked.
    stash.setdefault("given_answers", {})[question.id] = (
        decode_given_json(given_json)
    )

    if stash.get("mode") == "training" and not is_correct:
        stash["feedback_pending_for"] = question.id
        app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
        _refresh_runner_body()
        return

    stash["feedback_pending_for"] = None
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
    advance_or_finalise(stash)


def on_continue_after_feedback(stash: dict) -> None:
    """Training-mode "Continue" handler: clear feedback flag + advance."""

    stash["feedback_pending_for"] = None
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
    advance_or_finalise(stash)


def advance_or_finalise(stash: dict) -> None:
    """Move cursor to the next question or end the session."""

    finished = advance_session_stash(stash)
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash

    if finished:
        finalise_session(stash)
    _refresh_runner_body()


def navigate_question(stash: dict, delta: int) -> None:
    """Move cursor by ``delta`` (±1) without grading.

    Supports the VCE-style ``[<]`` / ``[>]`` header navigation: the
    candidate can revisit earlier questions or skip ahead without
    submitting an answer. Clamped to ``[0, len(question_ids) - 1]``
    so the Runner never lands on the results screen from here — that
    path is reserved for :func:`advance_or_finalise`.

    Clears ``feedback_pending_for`` on every navigation so the target
    question is always rendered in its standard (editable) state; the
    candidate's previous submission still shows up as pre-filled
    widgets thanks to ``given_answers``.
    """

    current = int(stash.get("current_index", 0))
    total = len(stash.get("question_ids") or [])
    new_index = max(0, min(total - 1, current + delta))
    if new_index == current:
        return
    stash["current_index"] = new_index
    stash["feedback_pending_for"] = None
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
    _refresh_runner_body()


def finalise_session(stash: dict) -> None:
    """Close the session DB-side so ``finished_at`` is stamped."""

    exam_path = Path(str(stash.get("exam_path")))
    session_id = str(stash.get("session_id") or "")
    if not session_id or not exam_path.is_file():
        return

    db = open_db(exam_path)
    try:
        try:
            finish_session(db, session_id)
        except LookupError:
            # Session vanished (e.g. someone deleted the DB) — treat
            # as already-finalised.
            pass
    finally:
        db.close()


def force_finish_session(stash: dict) -> None:
    """Immediately end the session and route to the results screen.

    Used by the countdown-timer's "Time's up" modal: once the
    candidate acknowledges the timeout we advance the cursor past
    the last question, stamp ``finished_at`` and refresh so
    :func:`posrat.runner.page._render_runner_body` dispatches to
    :func:`posrat.runner.results_view.render_results`.

    This differs from :func:`advance_or_finalise` which only moves
    one step at a time — timeout is a hard cut, any still-unanswered
    questions stay unanswered (and therefore count as wrong via the
    snapshot-based denominator in :func:`compute_session_score`).
    """

    question_ids = list(stash.get("question_ids") or [])
    stash["current_index"] = len(question_ids)
    stash["feedback_pending_for"] = None
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
    finalise_session(stash)
    _refresh_runner_body()


__all__ = [
    "advance_or_finalise",
    "finalise_session",
    "force_finish_session",
    "navigate_question",
    "on_continue_after_feedback",
    "on_submit_answer",
    "validate_submission_shape",
]
