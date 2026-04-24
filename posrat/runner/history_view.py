"""Runner history panel — right-hand side of the landing page.

Renders the list of past (and in-progress) session attempts produced
by :func:`posrat.runner.history.list_session_results`. Cards are
clickable: clicking a row stores the session identifiers under
:data:`RUNNER_DETAIL_STORAGE_KEY` and triggers a page refresh that
routes into :func:`posrat.runner.session_detail_view.render_session_detail`.

Each card also shows a small trash-icon button that opens a
confirmation dialog and deletes the session (and its answers) via
:func:`posrat.storage.delete_session`. The picker refreshes
afterwards so the row disappears from the history panel.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

from posrat.runner.history import SessionResultSummary, list_session_results
from posrat.runner.session_detail_view import RUNNER_DETAIL_STORAGE_KEY
from posrat.storage import delete_session, open_db


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

    The card body (everything except the trash icon) is clickable
    and opens the per-question drill-down. The trash-icon button in
    the top-right corner calls :func:`_confirm_delete_session`, which
    opens a ``ui.dialog`` for explicit confirmation — no
    single-click deletions so accidental taps cannot destroy data.
    """

    with ui.card().classes("w-full").props("bordered"):
        # Top row: exam name + PASS/FAIL/in-progress chip + delete button.
        with ui.row().classes(
            "items-center q-gutter-sm w-full no-wrap cursor-pointer"
        ) as top_row:
            top_row.on(
                "click",
                lambda _evt=None, r=result: _open_session_detail(r),
            )
            ui.label(result.exam_name).classes("text-subtitle1 col-grow")
            _render_state_chip(result)

        _render_delete_button(result)

        # Candidate + mode + started timestamp in a caption row. Wrap
        # in a clickable div so we keep the "click anywhere" drill-down
        # affordance without swallowing clicks on the delete button.
        with ui.column().classes("cursor-pointer w-full") as body:
            body.on(
                "click",
                lambda _evt=None, r=result: _open_session_detail(r),
            )
            details: list[str] = []
            details.append(
                f"Candidate: {result.candidate_name or '(unknown)'}"
            )
            details.append(f"Mode: {result.mode}")
            details.append(_format_started_at(result.started_at))
            ui.label(" · ".join(details)).classes("text-caption text-grey")

            # Score line: "N correct / M total · XX.X % · Y points"
            score_parts = [
                f"{result.score.correct_count} correct "
                f"/ {result.score.total_count}"
            ]
            if result.score.percent is not None:
                score_parts.append(f"{result.score.percent:.1f} %")
            if result.score.raw_score is not None:
                # SessionScore does not carry the target_score; the
                # drill-down detail view shows ``X / Y points`` instead
                # because it can read target_score from the session row.
                score_parts.append(f"{result.score.raw_score} points")
            ui.label(" · ".join(score_parts)).classes("text-body2")


def _render_delete_button(result: SessionResultSummary) -> None:
    """Render the trash-icon button in the card's top-right corner.

    Floated right via absolute-positioned row so it does not steal
    horizontal space from the exam name label — NiceGUI's flexbox
    already gave that row its own layout.
    """

    with ui.row().classes("justify-end w-full"):
        ui.button(
            icon="delete",
            on_click=lambda _evt=None, r=result: _confirm_delete_session(r),
        ).props("flat dense color=negative size=sm").tooltip(
            "Delete this session"
        )


def _confirm_delete_session(result: SessionResultSummary) -> None:
    """Open a confirmation dialog and, on accept, delete the session.

    Uses a plain ``ui.dialog`` with two buttons (Cancel / Delete)
    rather than Quasar's quick-confirm API so the verbiage clearly
    mentions the irreversible cascade into the answers table.
    """

    with ui.dialog() as dialog, ui.card():
        ui.label(
            f"Delete session from {_format_started_at(result.started_at)}?"
        ).classes("text-subtitle1")
        ui.label(
            "This removes the session row and all its recorded "
            "answers. The action cannot be undone."
        ).classes("text-caption text-grey")
        with ui.row().classes("justify-end q-gutter-sm q-mt-sm"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(
                "Delete",
                on_click=lambda _evt=None: _do_delete(result, dialog),
            ).props("color=negative")
    dialog.open()


def _do_delete(result: SessionResultSummary, dialog: ui.dialog) -> None:
    """Execute the delete and refresh the picker so the row disappears."""

    db = open_db(result.exam_path)
    try:
        removed = delete_session(db, result.session_id)
    finally:
        db.close()
    dialog.close()
    if removed:
        ui.notify("Session deleted.")
    else:
        # Very rare — the row vanished between render and confirm.
        ui.notify(
            "Session was already gone (refreshing history).",
            type="warning",
        )

    # Refresh the entire Runner body so picker_view redraws the
    # history panel without the deleted session.
    from posrat.runner.page import _render_runner_body
    _render_runner_body.refresh()


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
