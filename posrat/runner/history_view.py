"""Runner history panel — right-hand side of the landing page.

Renders the list of past (and in-progress) session attempts produced
by :func:`posrat.runner.history.list_session_results`. Cards are
clickable: clicking a row stores the session identifiers under
:data:`RUNNER_DETAIL_STORAGE_KEY` and triggers a page refresh that
routes into :func:`posrat.runner.session_detail_view.render_session_detail`.

Layout decisions:

* One card per session, ordered newest-first (already sorted by the
  helper).
* Colour-coded passed/failed chip when the session snapshot carried
  a passing score. Sessions still in progress (``finished_at`` None)
  render with a neutral "in progress" label.
* ``cursor-pointer`` on the whole card signals "this is clickable"
  without a separate button — keeps the panel compact.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from posrat.runner.history import SessionResultSummary, list_session_results
from posrat.runner.session_detail_view import RUNNER_DETAIL_STORAGE_KEY


#: Display title for the right-hand history panel.
RUNNER_HISTORY_HEADING = "Results history"


def render_history_panel(data_dir: Path) -> None:
    """Render the session history column on the Runner landing page."""

    ui.label(RUNNER_HISTORY_HEADING).classes("text-h5")
    results = list_session_results(data_dir)

    if not results:
        ui.label(
            "No results yet. Start an exam on the left."
        ).classes("text-caption text-grey q-mt-md")
        return

    with ui.column().classes("q-gutter-sm q-mt-md w-full"):
        for result in results:
            _render_result_card(result)


def _open_session_detail(result: SessionResultSummary) -> None:
    """Stash the drill-down target and refresh into the detail view.

    Stores the minimal pair of identifiers the view needs
    (``exam_path`` + ``session_id``) under
    :data:`RUNNER_DETAIL_STORAGE_KEY`; the page-level refreshable
    picks it up on the next render and dispatches to
    :func:`render_session_detail`.
    """

    app.storage.user[RUNNER_DETAIL_STORAGE_KEY] = {
        "exam_path": str(result.exam_path),
        "session_id": result.session_id,
    }
    # Lazy import keeps the module graph acyclic — page.py imports
    # history_view indirectly (via picker_view), so a top-level import
    # would loop.
    from posrat.runner.page import _render_runner_body
    _render_runner_body.refresh()


def _render_result_card(result: SessionResultSummary) -> None:
    """Render one session summary row in the history panel.

    The card is clickable (``cursor-pointer`` + ``on("click")``)
    and opens the per-question drill-down on click. The explicit
    "Detail" button stays too so keyboard / screen-reader users
    have an obvious target; both paths call
    :func:`_open_session_detail`.
    """

    with ui.card().classes(
        "w-full cursor-pointer"
    ).props("bordered") as card:
        card.on("click", lambda _evt=None, r=result: _open_session_detail(r))
        # Top row: exam name + PASS/FAIL/in-progress chip.
        with ui.row().classes("items-center q-gutter-sm w-full no-wrap"):
            ui.label(result.exam_name).classes("text-subtitle1 col-grow")
            _render_state_chip(result)

        # Candidate + mode + started timestamp in a caption row.
        details: list[str] = []
        details.append(f"Candidate: {result.candidate_name or '(unknown)'}")
        details.append(f"Mode: {result.mode}")
        details.append(_format_started_at(result.started_at))
        ui.label(" · ".join(details)).classes("text-caption text-grey")

        # Score line: "N correct / M total · XX.X % · Y/Z points"
        score_parts = [
            f"{result.score.correct_count} correct / {result.score.total_count}"
        ]
        if result.score.percent is not None:
            score_parts.append(f"{result.score.percent:.1f} %")
        if result.score.raw_score is not None:
            # SessionScore does not carry the target_score; the
            # drill-down detail view shows ``X / Y points`` instead
            # because it can read target_score from the session row.
            score_parts.append(f"{result.score.raw_score} points")
        ui.label(" · ".join(score_parts)).classes("text-body2")


def _render_state_chip(result: SessionResultSummary) -> None:
    """Render a small PASS/FAIL/in-progress/completed chip."""

    if not result.is_finished:
        ui.badge("in progress").props("color=grey")
        return

    passed = result.score.passed
    if passed is True:
        ui.badge("PASS").props("color=positive")
    elif passed is False:
        ui.badge("FAIL").props("color=negative")
    else:
        # Finished but no pass/fail criterion (exam lacks passing_score
        # or target_score): just note the session closed.
        ui.badge("completed").props("color=secondary")


def _format_started_at(raw: str) -> str:
    """Return a compact ``YYYY-MM-DD HH:MM`` rendering of an ISO timestamp.

    Session timestamps are stored as ``YYYY-MM-DDTHH:MM:SSZ``. The UI
    doesn't need the seconds or the ``Z`` suffix — we slice to minute
    precision and replace the ``T`` with a space for readability.
    Returns the raw string verbatim on any parse hiccup so legacy
    formats are rendered as-is rather than failing.
    """

    if len(raw) < 16 or "T" not in raw:
        return raw
    return raw[:16].replace("T", " ")


__all__ = [
    "RUNNER_HISTORY_HEADING",
    "render_history_panel",
]
