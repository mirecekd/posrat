"""Runner page — top-level refreshable + ``/runner`` route entry point.

The page body is a single :func:`ui.refreshable` function that
inspects the per-user session stash and delegates to one of three
sub-modules:

* :mod:`posrat.runner.picker_view` — no active session (landing page).
* :mod:`posrat.runner.question_view` — in-flight session rendering.
* :mod:`posrat.runner.results_view` — finished session score card.

All three delegates are pure render functions; state mutations flow
through :mod:`posrat.runner.submit_flow` which re-calls the
refreshable on this module to re-render after each side-effect.

Historical note: before the 2026-04-23 refactor this whole pipeline
lived in a single ~1000-LOC ``page.py``. It was split to keep every
module under the 8 KB soft cap the project follows for Python files,
and to make future additions (countdown timer, Exam-mode review,
JSON export) drop-in rather than surgery on a god module.
"""

from __future__ import annotations

from nicegui import app, ui

from posrat.runner.picker_view import render_picker
from posrat.runner.question_view import render_question_view
from posrat.runner.results_view import render_results
from posrat.runner.session_detail_view import (
    RUNNER_DETAIL_STORAGE_KEY,
    render_session_detail,
)
from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    is_session_stash_complete,
)


def _is_detail_stash_valid(detail_stash: object) -> bool:
    """Return ``True`` when ``detail_stash`` has both keys the view needs."""

    return (
        isinstance(detail_stash, dict)
        and bool(detail_stash.get("exam_path"))
        and bool(detail_stash.get("session_id"))
    )


@ui.refreshable
def _render_runner_body() -> None:
    """Top-level Runner body — picks picker/question/results based on state.

    Dispatch order (first match wins):

    1. **Detail stash set** → render the drill-down review of a past
       session. Takes priority over the run stash so a candidate can
       browse history while another session is still mid-flight.
    2. **Run stash incomplete** → landing page (picker + history).
    3. **Run stash finished** (cursor past last question) → results.
    4. Otherwise → current question view.
    """

    detail_stash = app.storage.user.get(RUNNER_DETAIL_STORAGE_KEY)
    if _is_detail_stash_valid(detail_stash):
        render_session_detail(detail_stash)
        return

    stash = app.storage.user.get(RUNNER_SESSION_STORAGE_KEY)

    if not is_session_stash_complete(stash):
        render_picker()
        return

    question_ids: list[str] = list(stash.get("question_ids") or [])
    index = int(stash.get("current_index", 0))
    if index >= len(question_ids):
        render_results(stash)
        return

    render_question_view(stash)


def render_runner() -> None:
    """Public entry point wired into the ``/runner`` route."""

    _render_runner_body()


__all__ = [
    "render_runner",
]
