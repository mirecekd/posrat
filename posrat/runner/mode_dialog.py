"""Visual CertExam-style "Exam Mode" dialog for the Runner picker.

Opened from the picker card's Start… button. Collects candidate name,
desired question count, training/exam toggle and optional timer, then
launches a session via :func:`start_runner_session` and pushes the
fresh stash into ``app.storage.user``.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from nicegui import app, ui

from posrat.runner.orchestrator import start_runner_session
from posrat.runner.picker import RunnerExamSummary
from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    build_runner_session_stash,
)
from posrat.runner.view_helpers import current_runner_username, utc_now_iso


#: Fallback count when the exam itself does not specify
#: ``default_question_count``. 65 mirrors the AIF-C01 benchmark shown
#: in the screenshots the user shared during planning; at render time
#: we still cap it to the exam's actual pool size.
FALLBACK_DEFAULT_QUESTION_COUNT = 65


def open_mode_dialog(summary: RunnerExamSummary) -> None:
    """Open the VCE-style "Exam Mode" dialog for ``summary``.

    Pre-fills candidate name from :func:`resolve_username`, the
    requested question count from the exam's ``default_question_count``
    (clamped to the pool), the timer from ``time_limit_minutes`` and
    defaults the mode to *Training* with the timer on — mirrors the
    screenshot the user shared during planning.
    """

    default_count = min(
        summary.default_question_count or FALLBACK_DEFAULT_QUESTION_COUNT,
        summary.question_count,
    )
    default_timer = summary.time_limit_minutes or 0
    timer_enabled_default = summary.time_limit_minutes is not None

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label(f"Exam Mode — {summary.name}").classes("text-h6")
        candidate_input = ui.input(
            "Candidate name",
            value=current_runner_username(),
        ).classes("w-full")

        ui.label(
            f"Take N questions out of {summary.question_count} total."
        ).classes("text-caption text-grey q-mt-sm")
        count_input = ui.number(
            "Take N questions",
            value=default_count,
            min=1,
            max=summary.question_count,
            step=1,
            format="%d",
        ).classes("w-full")

        training_toggle = ui.checkbox(
            "Training mode (immediate feedback)", value=True
        )

        with ui.row().classes("items-center q-gutter-sm q-mt-sm"):
            timer_toggle = ui.checkbox("Timer on", value=timer_enabled_default)
            timer_input = ui.number(
                "Time limit (minutes)",
                value=default_timer,
                min=1,
                step=1,
                format="%d",
            ).classes("col-grow")

        def _on_start() -> None:
            candidate = (candidate_input.value or "").strip()
            if not candidate:
                ui.notify("Enter the candidate name.", type="negative")
                return

            try:
                requested_count = int(count_input.value or 0)
            except (TypeError, ValueError):
                ui.notify("Invalid question count.", type="negative")
                return
            if requested_count <= 0:
                ui.notify("Question count must be positive.", type="negative")
                return

            time_limit: Optional[int] = None
            if timer_toggle.value:
                try:
                    time_limit = int(timer_input.value or 0)
                except (TypeError, ValueError):
                    ui.notify("Invalid time.", type="negative")
                    return
                if time_limit <= 0:
                    ui.notify("Time must be positive.", type="negative")
                    return

            mode = "training" if training_toggle.value else "exam"

            try:
                started = start_runner_session(
                    summary.path,
                    exam_id=summary.exam_id,
                    mode=mode,
                    candidate_name=candidate,
                    question_count=requested_count,
                    time_limit_minutes=time_limit,
                    passing_score=summary.passing_score,
                    target_score=summary.target_score,
                    started_at=utc_now_iso(),
                )
            except (
                LookupError,
                ValueError,
                sqlite3.DatabaseError,
            ) as exc:
                ui.notify(f"Cannot start session: {exc}", type="negative")
                return

            stash = build_runner_session_stash(
                session_id=started.session.id,
                exam_path=str(summary.path.resolve()),
                exam_id=summary.exam_id,
                mode=mode,
                question_ids=started.question_ids,
                started_at=started.session.started_at,
                time_limit_minutes=time_limit,
                candidate_name=candidate,
            )
            app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
            dialog.close()
            ui.notify(
                f"Session started ({len(started.question_ids)} questions)."
            )

            # Lazy import keeps this module's import graph acyclic —
            # page.py owns the refreshable.
            from posrat.runner.page import _render_runner_body
            _render_runner_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Start", on_click=_on_start).props("color=primary")

    dialog.open()


__all__ = [
    "FALLBACK_DEFAULT_QUESTION_COUNT",
    "open_mode_dialog",
]
