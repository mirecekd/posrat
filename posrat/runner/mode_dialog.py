"""Visual CertExam-style "Exam Mode" dialog for the Runner picker.

Opened from the picker card's Start… button. Collects candidate name,
question-selection strategy (Take N from the entire exam OR Take
questions answered incorrectly N+ times), an optional range modifier
that narrows the Take N pool to a 1-based inclusive slice, plus
training/exam toggle and optional timer. Then launches a session via
:func:`start_runner_session` and pushes the fresh stash into
``app.storage.user``.

The range modifier is enabled only when the active radio is Take N —
the checkbox auto-disables when the user switches to incorrect mode,
since "Take wrong answers from a slice" was deemed overkill during
planning (2026-05-20). Sample order is always randomised; POSRAT
never serves questions in their author-specified 1..N sequence.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from nicegui import app, ui

from posrat.runner.mode_selection import (
    OPT_ALL,
    OPT_INCORRECT,
    resolve_selection_from_dialog,
)
from posrat.runner.orchestrator import start_runner_session
from posrat.runner.picker import RunnerExamSummary
from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    build_runner_session_stash,
)
from posrat.runner.view_helpers import current_runner_username, utc_now_iso
from posrat.system.current_user import current_user_or_none


#: Fallback count when the exam itself does not specify
#: ``default_question_count``. 65 mirrors the AIF-C01 benchmark shown
#: in the screenshots the user shared during planning; at render time
#: we still cap it to the exam's actual pool size.
FALLBACK_DEFAULT_QUESTION_COUNT = 65


def open_mode_dialog(summary: RunnerExamSummary) -> None:
    """Open the VCE-style "Exam Mode" dialog for ``summary``.

    Two mutually exclusive question-selection modes:

    1. **Take N questions from entire exam file** — random sample of
       ``count`` questions. Optional range modifier checkbox narrows
       the pool to a 1-based inclusive slice ``[start, end]`` before
       sampling.
    2. **Take questions that I have answered incorrectly N+ times** —
       filters the candidate's own finished sessions on this exam.

    Pre-fills the candidate name from :func:`current_runner_username`,
    timer from ``time_limit_minutes``, and defaults to *Training* mode
    with the timer on. Take-N count default is the exam's
    ``default_question_count`` (or :data:`FALLBACK_DEFAULT_QUESTION_COUNT`
    when missing) — never clamped to the range modifier, since the
    user explicitly chose "N is always taken from the exam default".
    """

    default_count = (
        summary.default_question_count or FALLBACK_DEFAULT_QUESTION_COUNT
    )
    default_timer = summary.time_limit_minutes or 0
    timer_enabled_default = summary.time_limit_minutes is not None

    with ui.dialog() as dialog, ui.card().style("min-width: 640px"):
        ui.label(f"Exam Mode — {summary.name}").classes("text-h6")
        candidate_input = ui.input(
            "Candidate name",
            value=current_runner_username(),
        ).classes("w-full")

        # Two separate radios — each holds one option — so we can
        # interleave the per-option sub-rows between them. They are
        # mutex-synchronised via on_value_change handlers below so
        # the visible state matches a single logical radio group.
        take_all_radio = ui.radio(
            {OPT_ALL: "Take N questions from entire exam file"},
            value=OPT_ALL,
        ).props("inline=false")

        (
            count_input,
            range_checkbox,
            range_start_input,
            range_end_input,
        ) = _render_take_all_inputs(
            take_all_radio,
            default_count=default_count,
            pool_size=summary.question_count,
        )

        take_incorrect_radio = ui.radio(
            {OPT_INCORRECT: "Take questions that I have answered incorrectly"},
            value=None,
        ).props("inline=false")

        wrong_input = _render_take_incorrect_inputs(take_incorrect_radio)

        # Mutual exclusion — flipping one radio clears the other so
        # only one option is checked at any moment.
        def _sync_take_all(event) -> None:
            if event.value == OPT_ALL:
                take_incorrect_radio.set_value(None)

        def _sync_take_incorrect(event) -> None:
            if event.value == OPT_INCORRECT:
                take_all_radio.set_value(None)

        take_all_radio.on_value_change(_sync_take_all)
        take_incorrect_radio.on_value_change(_sync_take_incorrect)

        def _active_mode() -> Optional[str]:
            """Return whichever radio is currently selected."""

            if take_all_radio.value == OPT_ALL:
                return OPT_ALL
            if take_incorrect_radio.value == OPT_INCORRECT:
                return OPT_INCORRECT
            return None


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

            mode_value = _active_mode()
            if mode_value is None:
                ui.notify("Pick one question-selection mode.", type="negative")
                return

            selection = resolve_selection_from_dialog(
                mode=mode_value,
                count_value=count_input.value,
                wrong_value=wrong_input.value,
                pool_size=summary.question_count,
                notify=lambda msg: ui.notify(msg, type="negative"),
                range_enabled=(
                    bool(range_checkbox.value) and mode_value == OPT_ALL
                ),
                range_start_value=range_start_input.value,
                range_end_value=range_end_input.value,
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

            # Pin the signed-in account to the session row so the
            # Runner history panel can filter per-user (Phase 14).
            # ``candidate_name`` stays a free-text label; ``username``
            # is the stable POSRAT identifier we use for ACL.
            _user = current_user_or_none()
            session_username = _user.username if _user else None

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
                    username=session_username,
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


def _render_take_all_inputs(
    take_all_radio: ui.radio,
    *,
    default_count: int,
    pool_size: int,
):
    """Render the Take-N + range-modifier sub-rows under the Take-N radio.

    Returns a 4-tuple ``(count, range_checkbox, range_start, range_end)``;
    the caller reads their ``.value`` attributes when the Start button
    fires. Every input is bound through ``bind_enabled_from`` to the
    Take-N radio so that switching to the Take-incorrect option fully
    greys this whole sub-tree out — visual signal that the inputs are
    inert until Take-N is active again.
    """

    is_active = lambda v: v == OPT_ALL  # noqa: E731

    with ui.row().classes("items-center q-gutter-sm q-mt-sm q-ml-lg"):
        ui.label("Take").classes("text-caption")
        count_input = ui.number(
            value=default_count,
            min=1,
            step=1,
            format="%d",
        ).classes("w-24")
        ui.label(f"of {pool_size} questions").classes("text-caption")
        count_input.bind_enabled_from(take_all_radio, "value", is_active)

    with ui.row().classes("items-center q-gutter-sm q-ml-lg q-mt-xs"):
        range_checkbox = ui.checkbox("Limit to question range")
        range_checkbox.bind_enabled_from(take_all_radio, "value", is_active)
        ui.label("from").classes("text-caption")
        range_start_input = ui.number(
            value=1, min=1, max=pool_size, step=1, format="%d",
        ).classes("w-24")
        ui.label("to").classes("text-caption")
        range_end_input = ui.number(
            value=pool_size, min=1, max=pool_size, step=1, format="%d",
        ).classes("w-24")
        # Both inputs are gated by the checkbox; the radio-group gate
        # is delegated to ``range_checkbox`` itself, which is already
        # disabled outside Take-N mode and therefore stuck at False.
        range_start_input.bind_enabled_from(
            range_checkbox, "value", lambda v: bool(v),
        )
        range_end_input.bind_enabled_from(
            range_checkbox, "value", lambda v: bool(v),
        )

    return count_input, range_checkbox, range_start_input, range_end_input


def _render_take_incorrect_inputs(take_incorrect_radio: ui.radio):
    """Render the "[N] or more times" sub-row under the Take-incorrect radio.

    Returns the ``ui.number`` widget; ``.value`` is read on Start.
    Enabled only while the Take-incorrect radio is the active option.
    """

    is_active = lambda v: v == OPT_INCORRECT  # noqa: E731

    with ui.row().classes("items-center q-gutter-sm q-mt-sm q-ml-lg"):
        wrong_input = ui.number(
            value=1, min=1, step=1, format="%d",
        ).classes("w-24")
        ui.label("or more times").classes("text-caption")
        wrong_input.bind_enabled_from(take_incorrect_radio, "value", is_active)

    return wrong_input



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
