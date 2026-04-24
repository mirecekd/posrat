"""Per-question-type input renderers for the Runner question view.

Each ``render_*_input`` function consumes the shared ``payload_holder``
mutable dict — the question view owns the holder, the input widgets
update its ``payload`` key on every change, and the submit handler
reads it once at submission time.

All three renderers honour ``feedback_pending``: when the flag is set
(training mode wrong-answer review) inputs are disabled and rows are
colour-coded via :func:`posrat.runner.view_helpers.choice_row_classes`.
"""

from __future__ import annotations

from typing import Optional

from nicegui import app, ui

from posrat.models import Question
from posrat.runner.sampler import shuffle_choices
from posrat.runner.session_state import RUNNER_SESSION_STORAGE_KEY
from posrat.runner.submit_flow import (
    force_finish_session,
    on_continue_after_feedback,
    on_submit_answer,
)
from posrat.runner.view_helpers import choice_row_classes, letter_for


def _render_end_exam_button(stash: dict) -> None:
    """Render the "End exam" button with a confirmation dialog.

    Lives on the same row as Submit / Continue, pushed to the far
    right so the candidate has an always-visible bail-out that ends
    the session immediately (any unanswered questions count as wrong).
    The confirmation dialog prevents accidental clicks — once the
    candidate confirms, :func:`force_finish_session` stamps
    ``finished_at`` and routes to the results screen.
    """

    def _open_confirm() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label("End exam now?").classes("text-h6")
            ui.label(
                "The session will be finalised and any unanswered"
                " questions will be marked as wrong."
            ).classes("text-body2 q-mt-sm")
            with ui.row().classes("justify-end q-mt-md q-gutter-sm"):
                ui.button(
                    "Cancel", on_click=dlg.close
                ).props("flat")

                def _confirm() -> None:
                    dlg.close()
                    force_finish_session(stash)

                ui.button("End exam", on_click=_confirm).props(
                    "color=negative"
                )
        dlg.open()

    ui.button("End exam", on_click=lambda _evt=None: _open_confirm()).props(
        "color=negative outline"
    )



def get_choice_order(stash: dict, question: Question) -> list[str]:
    """Return the pinned display order of ``question``'s choices.

    First call per question picks the order (either the authored
    sequence or a fresh shuffle if ``allow_shuffle=True``) and persists
    it under ``stash["choice_orders"][question.id]``. Subsequent
    renders — including prev/next revisits — reuse the same list so
    A/B/C labels and previously-picked answers stay consistent.
    """

    orders = stash.setdefault("choice_orders", {})
    cached = orders.get(question.id)
    if cached:
        return list(cached)

    ordered = shuffle_choices(
        question.choices,
        allow_shuffle=bool(getattr(question, "allow_shuffle", False)),
    )
    ids = [choice.id for choice in ordered]
    orders[question.id] = list(ids)
    app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
    return ids


def render_single_choice_input(
    question: Question,
    stash: dict,
    holder: dict[str, object],
    feedback_pending: bool,
) -> None:
    """Render a single_choice question as A/B/C/D rows with exclusive radios.

    Each row has its own :func:`ui.radio` (so we can wrap it in a
    ``ui.row`` with per-row green/red highlighting for review), but the
    on-change handler manually clears *every other* row's radio —
    enforcing the "pick exactly one" invariant even though the radios
    are separate widgets. That gives us the best of both worlds:
    per-row background colours (impossible through a single bulk
    ``ui.radio`` widget) **and** mutual-exclusion semantics.

    Review mode (``feedback_pending``) colours every correct row
    green and wrong-picked rows red, right in the question list —
    the candidate sees the right answer in place, no need to look in
    a separate "Correct answer" panel.
    """

    order = get_choice_order(stash, question)
    by_id = {c.id: c for c in question.choices}
    current_payload = holder.get("payload")
    current_pick: Optional[str] = None
    if isinstance(current_payload, dict):
        cid_val = current_payload.get("choice_id")
        if isinstance(cid_val, str):
            current_pick = cid_val

    # Tracks all radio widgets so the exclusive-pick handler can
    # reset siblings when the user picks a new option.
    radios: dict[str, object] = {}

    for idx, cid in enumerate(order):
        choice = by_id.get(cid)
        if choice is None:  # pragma: no cover - defensive
            continue
        picked = current_pick == cid
        row_classes = "items-center q-gutter-sm no-wrap w-full " + choice_row_classes(
            is_correct=choice.is_correct,
            picked=picked,
            feedback_pending=feedback_pending,
        )
        with ui.row().classes(row_classes):
            radio = ui.radio(
                options={cid: ""},
                value=cid if picked else None,
            ).props("dense")
            if feedback_pending:
                radio.props("disable")
            radios[cid] = radio

            def _on_change(evt, chosen=cid) -> None:
                # Quasar fires ``value_change`` both when a radio is
                # picked (evt.value == cid) and when we clear it below
                # (evt.value is None). We only act on the pick path.
                if evt.value != chosen:
                    return
                holder["payload"] = {"choice_id": chosen}
                # Exclusive pick: clear every other radio so Quasar
                # UI stays in sync with the "pick exactly one"
                # invariant. set_value(None) triggers another
                # value_change callback but the guard above swallows
                # it (evt.value=None != chosen).
                for other_cid, other_radio in radios.items():
                    if other_cid == chosen:
                        continue
                    try:
                        other_radio.set_value(None)  # type: ignore[attr-defined]
                    except Exception:  # pragma: no cover - defensive
                        pass

            radio.on_value_change(_on_change)

            ui.label(f"{letter_for(idx)}.").classes(
                "text-weight-medium q-mr-xs"
            )
            ui.markdown(choice.text).classes("col-grow")




def render_multi_choice_input(
    question: Question,
    stash: dict,
    holder: dict[str, object],
    feedback_pending: bool,
) -> None:
    """Render a multi_choice question as A/B/C/D checkboxes."""

    order = get_choice_order(stash, question)
    by_id = {c.id: c for c in question.choices}

    current_payload = holder.get("payload")
    current_picks: set[str] = set()
    if isinstance(current_payload, dict):
        raw = current_payload.get("choice_ids")
        if isinstance(raw, list):
            current_picks = {str(x) for x in raw}

    selections: dict[str, bool] = {cid: cid in current_picks for cid in order}

    def _refresh_payload() -> None:
        holder["payload"] = {
            "choice_ids": [cid for cid in order if selections.get(cid)]
        }

    _refresh_payload()

    correct_count = sum(1 for c in question.choices if c.is_correct)

    # Live counter label: "Pick N answer(s). Selected: M".
    # Refreshable so each toggle updates M without a full page reload.
    @ui.refreshable
    def _render_counter() -> None:
        chosen = sum(1 for picked in selections.values() if picked)
        colour_cls = (
            "text-positive" if chosen == correct_count else "text-grey"
        )
        ui.label(
            f"Pick {correct_count} answer(s). Selected: {chosen}."
        ).classes(f"text-caption {colour_cls} q-mt-xs")

    _render_counter()

    for idx, cid in enumerate(order):

        choice = by_id.get(cid)
        if choice is None:  # pragma: no cover - defensive
            continue
        picked = selections.get(cid, False)
        row_classes = "items-center q-gutter-sm no-wrap w-full " + choice_row_classes(
            is_correct=choice.is_correct,
            picked=picked,
            feedback_pending=feedback_pending,
        )
        with ui.row().classes(row_classes):
            box = ui.checkbox("", value=picked).props("dense")
            if feedback_pending:
                box.props("disable")

            def _on_toggle(evt, chosen=cid) -> None:
                selections[chosen] = bool(evt.value)
                _refresh_payload()
                # Update the "Selected: M" counter so the user sees
                # live progress toward the required N picks.
                _render_counter.refresh()

            box.on_value_change(_on_toggle)


            ui.label(f"{letter_for(idx)}.").classes(
                "text-weight-medium q-mr-xs"
            )
            ui.markdown(choice.text).classes("col-grow")


def render_hotspot_input(
    question: Question,
    stash: dict,
    holder: dict[str, object],
    feedback_pending: bool,
) -> None:
    """Render per-step dropdowns for a ``hotspot`` question.

    Review mode (``feedback_pending=True``) colours each step row
    green / red based on whether the candidate's pick matches the
    step's ``correct_option_id``; wrong picks also get a
    ``Correct: <option text>`` caption so the user sees the right
    answer without leaving the question.
    """

    if question.hotspot is None:  # pragma: no cover - defensive
        ui.label("Invalid hotspot (missing payload).").classes("text-negative")
        return

    option_map = {opt.id: opt.text for opt in question.hotspot.options}
    current_payload = holder.get("payload")
    current_picks_raw: dict[str, str] = {}
    if isinstance(current_payload, dict):
        raw = current_payload.get("step_option_ids")
        if isinstance(raw, dict):
            current_picks_raw = {str(k): str(v) for k, v in raw.items()}

    picks: dict[str, Optional[str]] = {
        step.id: (current_picks_raw.get(step.id) or None)
        for step in question.hotspot.steps
    }

    def _refresh_payload() -> None:
        holder["payload"] = {
            "step_option_ids": {
                sid: (pick or "") for sid, pick in picks.items()
            }
        }

    _refresh_payload()

    for step in question.hotspot.steps:
        correct = step.correct_option_id
        user_pick = picks.get(step.id)
        row_classes = choice_row_classes(
            is_correct=(user_pick == correct),
            picked=bool(user_pick),
            feedback_pending=feedback_pending,
        )
        with ui.column().classes(f"w-full q-mt-sm {row_classes}"):
            ui.label(step.prompt).classes("text-body2")
            sel = ui.select(
                options=option_map,
                value=picks.get(step.id),
                label="Pick the correct option",
            ).classes("w-full")
            if feedback_pending:
                sel.props("disable")

            def _on_change(evt, sid=step.id) -> None:
                picks[sid] = evt.value
                _refresh_payload()

            sel.on_value_change(_on_change)

            if feedback_pending and user_pick != correct:
                ui.label(
                    f"Correct: {option_map.get(correct, correct)}"
                ).classes("text-caption text-positive")


def render_next_area(
    question: Question,
    stash: dict,
    payload_holder: dict[str, object],
) -> None:
    """Render the bottom "Submit" + far-right "End exam" button row."""

    with ui.row().classes("items-center w-full q-mt-md justify-between"):
        ui.button(
            "Submit",
            on_click=lambda _evt=None: on_submit_answer(
                question, stash, payload_holder
            ),
        ).props("color=primary")
        _render_end_exam_button(stash)


def render_feedback_footer(question: Question, stash: dict) -> None:
    """Render the wrong-answer review strip.

    The correct answers are already highlighted inline (green rows) in
    the question list itself, and each choice now shows its own row
    colour — so the footer stays minimal: just the **Explanation /
    reference** markdown (when the question has one) plus the
    Continue + End exam buttons on the same row (End exam far right).
    """

    if question.explanation:
        ui.label("Explanation / reference").classes(
            "text-subtitle2 q-mt-sm"
        )
        ui.markdown(question.explanation).classes("text-body2")

    with ui.row().classes("items-center w-full q-mt-md justify-between"):
        ui.button(
            "Continue",
            on_click=lambda _evt=None: on_continue_after_feedback(stash),
        ).props("color=primary")
        _render_end_exam_button(stash)





__all__ = [
    "get_choice_order",
    "render_feedback_footer",
    "render_hotspot_input",
    "render_multi_choice_input",
    "render_next_area",
    "render_single_choice_input",
]
