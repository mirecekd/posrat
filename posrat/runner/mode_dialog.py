"""Visual CertExam-style "Exam Mode" dialog for the Runner picker.

Opened from the picker card's Start… button. Collects candidate name,
question-selection strategy (take N / take range / questions I got
wrong ≥ K times), training/exam toggle and optional timer, then
launches a session via :func:`start_runner_session` and pushes the
fresh stash into ``app.storage.user``.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from nicegui import app, ui

from posrat.runner.mode_selection import (
    OPT_ALL,
    OPT_INCORRECT,
    OPT_RANGE,
    resolve_selection_from_dialog,
)
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

    Three mutually exclusive question-selection modes:

    1. **Take N questions from entire exam file** — default sample.
    2. **Take question range X..Y** — 1-based inclusive slice.
    3. **Take questions that I have answered incorrectly N+ times** —
       filters the candidate's own finished sessions on this exam.

    Pre-fills the candidate name from :func:`current_runner_username`,
    timer from ``time_limit_minutes`` and defaults to *Training* mode
    with the timer on.
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

        selection_group = ui.radio(
            {
                OPT_ALL: "Take N questions from entire exam file",
                OPT_RANGE: "Take question range",
                OPT_INCORRECT:
                    "Take questions that I have answered incorrectly",
            },
            value=OPT_ALL,
        ).props("inline=false")

        count_input, range_start_input, range_end_input, wrong_input = (
            _render_selection_inputs(
                selection_group,
                default_count=default_count,
                pool_size=summary.question_count,
            )
        )

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

            selection = resolve_selection_from_dialog(
                mode=selection_group.value,
                count_value=count_input.value,
                range_start_value=range_start_input.value,
                range_end_value=range_end_input.value,
                wrong_value=wrong_input.value,
                pool_size=summary.question_count,
                notify=lambda msg: ui.notify(msg, type="negative"),
            )
            if selection is None:
                return

            time_limit = _resolve_timer(
                enabled=bool(timer_toggle.value),
                raw_value=timer_input.value,
            )
            if time_limit is False:
                # _resolve_timer notified already; abort the start flow.
                return

            mode = "training" if training_toggle.value else "exam"

            try:
                started = start_runner_session(
                    summary.path,
                    exam_id=summary.exam_id,
                    mode=mode,
                    candidate_name=candidate,
                    selection=selection,
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


def _render_selection_inputs(
    selection_group: ui.radio,
    *,
    default_count: int,
    pool_size: int,
):
    """Render the per-option number inputs bound to the radio group.

    Returns a 4-tuple ``(count, range_start, range_end, wrong)``; the
    caller reads their ``.value`` attributes when the Start button
    fires. Each input is wrapped in ``bind_enabled_from`` so only the
    row matching the selected radio is active at any time.
    """

    with ui.row().classes("items-center q-gutter-sm q-mt-sm q-ml-md"):
        ui.label("Take").classes("text-caption")
        count_input = ui.number(
            value=default_count,
            min=1,
            max=pool_size,
            step=1,
            format="%d",
        ).classes("w-24")
        ui.label(f"of {pool_size} questions").classes("text-caption")
        count_input.bind_enabled_from(
            selection_group, "value", lambda v: v == OPT_ALL
        )

    with ui.row().classes("items-center q-gutter-sm q-mt-sm q-ml-md"):
        ui.label("Range from").classes("text-caption")
        range_start_input = ui.number(
            value=1, min=1, max=pool_size, step=1, format="%d",
        ).classes("w-24")
        ui.label("to").classes("text-caption")
        range_end_input = ui.number(
            value=pool_size, min=1, max=pool_size, step=1, format="%d",
        ).classes("w-24")
        range_start_input.bind_enabled_from(
            selection_group, "value", lambda v: v == OPT_RANGE
        )
        range_end_input.bind_enabled_from(
            selection_group, "value", lambda v: v == OPT_RANGE
        )

    with ui.row().classes("items-center q-gutter-sm q-mt-sm q-ml-md"):
        wrong_input = ui.number(
            value=1, min=1, step=1, format="%d",
        ).classes("w-24")
        ui.label("or more times").classes("text-caption")
        wrong_input.bind_enabled_from(
            selection_group, "value", lambda v: v == OPT_INCORRECT
        )

    return count_input, range_start_input, range_end_input, wrong_input


def _resolve_timer(*, enabled: bool, raw_value) -> Optional[int]:
    """Parse the timer inputs into ``None`` / a positive int.

    Returns ``False`` (sentinel) when the value is invalid — caller
    must then abort the Start flow. ``False`` is distinct from
    ``None`` so a typo is not accidentally treated as "timer off".
    """

    if not enabled:
        return None
    try:
        value = int(raw_value or 0)
    except (TypeError, ValueError):
        ui.notify("Invalid time.", type="negative")
        return False  # type: ignore[return-value]
    if value <= 0:
        ui.notify("Time must be positive.", type="negative")
        return False  # type: ignore[return-value]
    return value


__all__ = [
    "FALLBACK_DEFAULT_QUESTION_COUNT",
    "open_mode_dialog",
]
