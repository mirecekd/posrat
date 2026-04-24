"""End-of-session results screen.

Rendered by :func:`posrat.runner.page._render_runner_body` once the
session's ``current_index`` cursor has advanced past the last question.
Reads the finalised :class:`Session` from disk (including every
persisted :class:`Answer`) and shows a score summary: raw-points
correct/total, computed percentage, optional PROŠEL/NEPROŠEL banner
based on the snapshotted ``passing_score``.

Also owns the "New exam" button that clears the stash and sends
the user back to the picker — the only sanctioned exit from the
results screen in MVP.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from posrat.runner.orchestrator import compute_session_score
from posrat.runner.session_state import RUNNER_SESSION_STORAGE_KEY
from posrat.storage import get_session, open_db


def render_results(stash: dict) -> None:
    """Render the score card after the session is finished."""

    exam_path = Path(str(stash.get("exam_path")))
    session_id = str(stash.get("session_id") or "")

    if not session_id or not exam_path.is_file():
        ui.label("Session data is not available.").classes("text-negative")
        _render_restart_button()
        return

    db = open_db(exam_path)
    try:
        session = get_session(db, session_id)
    finally:
        db.close()

    if session is None:
        ui.label("Session no longer exists.").classes("text-negative")
        _render_restart_button()
        return

    score = compute_session_score(session)
    ui.label("Result").classes("text-h5")
    with ui.card().classes("w-full").props("bordered"):
        ui.label(
            f"Candidate: {session.candidate_name or '(unknown)'}"
        ).classes("text-body2")
        ui.label(f"Exam: {session.exam_id}").classes("text-body2")
        ui.label(f"Mode: {session.mode}").classes("text-body2")

        ui.separator().classes("q-my-sm")

        ui.label(
            f"Correct: {score.correct_count} / {score.total_count}"
        ).classes("text-body1")
        if score.percent is not None:
            ui.label(f"Success rate: {score.percent:.1f} %").classes("text-body1")
        if score.raw_score is not None and session.target_score is not None:
            ui.label(
                f"Score: {score.raw_score} / {session.target_score} points"
            ).classes("text-body1")
        if score.passed is not None:
            state = "PASS" if score.passed else "FAIL"
            color = "text-positive" if score.passed else "text-negative"
            ui.label(state).classes(f"text-h6 {color}")

    _render_restart_button()


def _render_restart_button() -> None:
    """Render a "New exam" button that clears the stash."""

    def _reset() -> None:
        app.storage.user[RUNNER_SESSION_STORAGE_KEY] = None
        # Lazy import keeps the graph acyclic — page.py owns the refreshable.
        from posrat.runner.page import _render_runner_body
        _render_runner_body.refresh()

    with ui.row().classes("justify-end q-mt-md"):
        ui.button("New exam", on_click=_reset).props("color=primary")


__all__ = ["render_results"]
