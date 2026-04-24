"""Runner landing page — 2-panel layout: exams (left) + history (right).

Rendered by :func:`posrat.runner.page._render_runner_body` when no
session stash is present in ``app.storage.user``. The left panel
walks the data directory via :func:`list_runnable_exams` and shows
one Start… card per exam; the right panel delegates to
:mod:`posrat.runner.history_view` for the session-history browse.

Layout uses :func:`ui.splitter` with a draggable divider at 60 %
so candidates get a bit more real-estate for the exam list (their
primary focus) while still seeing recent results at a glance.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from posrat.designer.browser import resolve_data_dir
from posrat.models import User
from posrat.runner.history_view import render_history_panel
from posrat.runner.mode_dialog import open_mode_dialog
from posrat.runner.picker import (
    RunnerExamSummary,
    list_runnable_exams,
)
from posrat.runner.view_helpers import current_runner_username
from posrat.system.acl_repo import (
    get_access_request,
    has_exam_access,
    request_exam_access,
)
from posrat.system.system_db import (
    open_system_db,
    resolve_system_db_path,
)


#: Display title for the left-hand exam list panel.
RUNNER_PICKER_HEADING = "Available exams"


@ui.refreshable
def render_picker() -> None:
    """Render the 2-panel landing page: exam list + results history.

    Phase 10.9/10.15 turn this into a :func:`ui.refreshable` so the
    "Request access" button on a disabled card can rerender the
    whole view (picking up the new ``pending`` badge) without forcing
    a full page reload.
    """

    username = current_runner_username()
    # Username is already shown in the shared header ("Signed in:
    # <name>"); rendering it again here was duplicate noise (visual
    # feedback from 10.9 follow-up).

    data_dir = resolve_data_dir()

    with ui.splitter(value=60).classes("w-full").props(
        "horizontal=false"
    ) as splitter:
        with splitter.before:
            with ui.column().classes("q-pa-md w-full"):
                _render_exam_list(data_dir, username)
        with splitter.after:
            with ui.column().classes("q-pa-md w-full"):
                render_history_panel(data_dir)


def _render_exam_list(data_dir: Path, username: str) -> None:
    """Render the left-hand column with the list of runnable exams.

    Phase 10.9: every card consults the system DB to decide whether
    the current ``username`` already has an ``ExamAccessGrant``. Cards
    for accessible exams render the Start… button as before; cards
    for not-yet-approved exams show a disabled summary and a "Request
    access" button that creates / refreshes an
    :class:`ExamAccessRequest` via
    :func:`posrat.system.acl_repo.request_exam_access`.
    """

    ui.label(RUNNER_PICKER_HEADING).classes("text-h5")
    summaries = list_runnable_exams(data_dir)

    if not summaries:
        ui.label(
            "No exam is available. Create one in the Designer "
            "or import it."
        ).classes("q-mt-md")
        return

    db = open_system_db(resolve_system_db_path(data_dir))
    try:
        access = {
            s.exam_id: has_exam_access(
                db, username=username, exam_id=s.exam_id
            )
            for s in summaries
        }
        pending = {
            s.exam_id: _is_pending(db, username, s.exam_id)
            for s in summaries
        }
    finally:
        db.close()

    with ui.column().classes("q-gutter-md q-mt-md w-full"):
        for summary in summaries:
            _render_exam_card(
                summary,
                username=username,
                has_access=access.get(summary.exam_id, False),
                is_pending=pending.get(summary.exam_id, False),
            )


def _is_pending(db, username: str, exam_id: str) -> bool:
    """Return True when the user has a pending request for ``exam_id``."""

    request = get_access_request(db, username=username, exam_id=exam_id)
    return request is not None and request.status == "pending"


def _render_exam_card(
    summary: RunnerExamSummary,
    *,
    username: str,
    has_access: bool,
    is_pending: bool,
) -> None:
    """One card per exam; shape depends on ACL status."""

    with ui.card().classes("w-full").props("bordered"):
        ui.label(summary.name).classes("text-h6")
        if summary.description:
            ui.label(summary.description).classes("text-body2")
        with ui.row().classes("items-center q-gutter-md text-caption text-grey"):
            ui.label(f"{summary.question_count} questions")
            if summary.default_question_count:
                ui.label(f"Default: {summary.default_question_count}")
            if summary.time_limit_minutes:
                ui.label(f"Timer: {summary.time_limit_minutes} min")
            if summary.passing_score and summary.target_score:
                ui.label(
                    f"Passing: {summary.passing_score}/{summary.target_score}"
                )

        with ui.row().classes("justify-end q-mt-sm q-gutter-sm items-center"):
            if has_access:
                ui.button(
                    "Start…",
                    on_click=lambda _evt=None, s=summary: open_mode_dialog(s),
                ).props("color=primary")
            elif is_pending:
                ui.badge("Request pending").props("color=warning")
            else:
                ui.badge("No access").props("color=grey")
                ui.button(
                    "Request access",
                    on_click=(
                        lambda _evt=None, s=summary: _handle_request_access(
                            username, s
                        )
                    ),
                ).props("color=secondary size=sm")


def _handle_request_access(
    username: str, summary: RunnerExamSummary
) -> None:
    """File an :class:`ExamAccessRequest` for ``summary`` / ``username``."""

    db = open_system_db(resolve_system_db_path(resolve_data_dir()))
    try:
        try:
            request_exam_access(
                db, username=username, exam_id=summary.exam_id
            )
        except ValueError as exc:
            ui.notify(
                f"Cannot submit request: {exc}", type="negative"
            )
            return
    finally:
        db.close()

    ui.notify(
        f"Access request for {summary.name!r} submitted."
    )
    render_picker.refresh()


__all__ = [
    "RUNNER_PICKER_HEADING",
    "render_picker",
]
