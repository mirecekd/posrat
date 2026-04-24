"""Designer — bottom-left Properties panel.

Read-mostly / light-edit grid of per-question properties for the
currently selected Exam Explorer row. Mirrors the Visual CertExam
Properties panel: Answer Type dropdown, read-only "Number of Choices"
/ "Last Choice Letter", editable Complexity + Section rows (step 8.5)
and scaffold rows for Exhibits / Allow Shuffle Choices.

Heavy edits (choice text, explanation, image upload, hotspot payload)
still live in their existing per-field modal dialogs — the Properties
panel just provides quick-access rows that open those dialogs.
"""

from __future__ import annotations

import sqlite3

from nicegui import ui

from nicegui import app

from posrat.designer.browser import (
    ALLOWED_QUESTION_TYPES,
    OPEN_EXAM_STORAGE_KEY,
    _render_designer_body,
    change_question_type_in_open_exam,
    update_exam_default_question_count_in_open_exam,
    update_exam_passing_score_in_open_exam,
    update_exam_target_score_in_open_exam,
    update_exam_time_limit_minutes_in_open_exam,
    update_question_allow_shuffle_in_open_exam,
    update_question_complexity_in_open_exam,
    update_question_section_in_open_exam,
)

from posrat.designer.state import get_selected_question
from posrat.models import Question
from posrat.models.question import MAX_COMPLEXITY, MIN_COMPLEXITY



#: Pixel width of the "property name" (left) column in the key/value
#: grid. Keeps row labels aligned regardless of value length. Derived
#: from the Visual CertExam mockup's ~140 px gutter.
PROPERTIES_LABEL_WIDTH_PX = 160

#: Sentinel label used in the Complexity dropdown to represent "unset".
#: The underlying value on submit is ``None`` so SQL NULL round-trips
#: cleanly; the label is only for display. Kept as a constant so the
#: dropdown renderer and the on-change handler share the same key.
COMPLEXITY_UNSET_LABEL = "(none)"


def _compute_last_choice_letter(question: Question) -> str:
    """Return the letter of the last choice (``A``, ``B``, …) or ``-``.

    Convenience for the Properties panel's "Last Choice Letter" row so
    the user can tell at a glance how many options the question has.
    Works only for choice-based questions; hotspot rows return ``"-"``
    because they have no A/B/C letter system.
    """

    if question.type == "hotspot" or not question.choices:
        return "-"
    return chr(ord("A") + len(question.choices) - 1)


def _handle_type_change(new_type: str, current_type: str) -> None:
    """Persist a type change from the Properties dropdown.

    Short-circuits on same-type picks (avoids a redundant DB rewrite
    and the choices-reset notification). Standard notify matrix
    applies on the DAO call: success → positive + refresh, ``False``
    race → warning + refresh, ``None`` no-exam → warning,
    ``ValueError`` / ``sqlite3.DatabaseError`` → negative.
    """

    if new_type == current_type:
        return

    selected = get_selected_question()
    if selected is None:
        ui.notify("No question is selected.", type="warning")
        return

    try:
        updated = change_question_type_in_open_exam(selected.id, new_type)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot change type: {exc}", type="negative")
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not updated:
        ui.notify(
            f"Question {selected.id} no longer exists.", type="warning"
        )
        _render_designer_body.refresh()
        return

    ui.notify(f"Question type changed to {new_type}.")
    _render_designer_body.refresh()


def _handle_complexity_change(raw_value: object, current: int | None) -> None:
    """Persist a Complexity dropdown pick.

    ``raw_value`` is the value NiceGUI's ``ui.select`` passes back —
    either the :data:`COMPLEXITY_UNSET_LABEL` sentinel (mapped to
    ``None``) or an int in the 1..5 range. Short-circuits on same-value
    picks so moving focus through the dropdown does not spam the DB.

    Standard notify matrix: ``True`` = positive toast + refresh,
    ``False`` = warning + refresh (race — question vanished), ``None`` =
    warning ("no exam open"), ``ValueError`` = negative (out-of-range
    probe), ``sqlite3.DatabaseError`` = negative.
    """

    # Normalise the dropdown value into the model's vocabulary.
    new_value: int | None
    if raw_value is None or raw_value == COMPLEXITY_UNSET_LABEL:
        new_value = None
    else:
        try:
            new_value = int(raw_value)
        except (TypeError, ValueError):
            ui.notify(
                f"Invalid complexity value: {raw_value!r}",
                type="negative",
            )
            return

    if new_value == current:
        return

    selected = get_selected_question()
    if selected is None:
        ui.notify("No question is selected.", type="warning")
        return

    try:
        updated = update_question_complexity_in_open_exam(
            selected.id, new_value
        )
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(
            f"Cannot save complexity: {exc}", type="negative"
        )
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not updated:
        ui.notify(
            f"Question {selected.id} no longer exists.", type="warning"
        )
        _render_designer_body.refresh()
        return

    label = COMPLEXITY_UNSET_LABEL if new_value is None else str(new_value)
    ui.notify(f"Complexity of question {selected.id} saved ({label}).")
    _render_designer_body.refresh()


def _handle_section_change(raw_value: object, current: str | None) -> None:
    """Persist a Section ``ui.input`` change.

    Fires on blur (we wire ``on_change`` from the input widget). The
    helper normalises the raw widget value the same way the model does
    (trim + empty → None) before comparing against ``current``, so
    whitespace-only edits don't cause spurious DB writes. Standard
    notify matrix applies.

    No-exam / unknown-id paths refresh the Designer body even on the
    warning branches so the Properties panel self-heals from any stale
    render state.
    """

    if isinstance(raw_value, str):
        trimmed = raw_value.strip()
        new_value: str | None = trimmed or None
    elif raw_value is None:
        new_value = None
    else:
        new_value = str(raw_value).strip() or None

    if new_value == current:
        return

    selected = get_selected_question()
    if selected is None:
        ui.notify("No question is selected.", type="warning")
        return

    try:
        updated = update_question_section_in_open_exam(
            selected.id, new_value
        )
    except sqlite3.DatabaseError as exc:
        ui.notify(
            f"Cannot save section: {exc}", type="negative"
        )
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not updated:
        ui.notify(
            f"Question {selected.id} no longer exists.", type="warning"
        )
        _render_designer_body.refresh()
        return

    label = new_value if new_value is not None else COMPLEXITY_UNSET_LABEL
    ui.notify(f"Section of question {selected.id} saved ({label}).")
    _render_designer_body.refresh()


def _handle_allow_shuffle_change(
    new_value: bool, current: bool
) -> None:
    """Persist an Allow Shuffle Choices checkbox toggle.

    Short-circuits on same-value toggles (the checkbox fires
    ``on_value_change`` even when the user taps a label and re-taps
    without changing the value). Standard notify matrix: ``True`` =
    positive toast + refresh, ``False`` = warning + refresh (race),
    ``None`` = warning ("no exam open"), ``sqlite3.DatabaseError`` =
    negative.
    """

    if bool(new_value) == bool(current):
        return

    selected = get_selected_question()
    if selected is None:
        ui.notify("No question is selected.", type="warning")
        return

    try:
        updated = update_question_allow_shuffle_in_open_exam(
            selected.id, bool(new_value)
        )
    except sqlite3.DatabaseError as exc:
        ui.notify(
            f"Cannot save shuffle: {exc}", type="negative"
        )
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not updated:
        ui.notify(
            f"Question {selected.id} no longer exists.", type="warning"
        )
        _render_designer_body.refresh()
        return

    state = "on" if new_value else "off"
    ui.notify(f"Shuffle for question {selected.id} turned {state}.")
    _render_designer_body.refresh()


def _render_property_row(label: str, value_widget: object) -> None:

    """Render one key/value row in the Properties grid.

    ``label`` is a plain string rendered as a muted caption on the left;
    ``value_widget`` is the NiceGUI element that draws the value on the
    right. Passing ``None`` falls back to a dimmed "-" placeholder.
    """

    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label(label).classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        if value_widget is None:
            ui.label("-").classes("text-caption text-grey")


def _render_readonly_value(value: str) -> None:
    """Render a plain read-only value (right column of the grid)."""

    ui.label(value).classes("text-body2")


def _coerce_optional_int(raw: object) -> int | None:
    """Normalise a ``ui.number`` value into ``int | None``.

    NiceGUI's ``ui.number`` passes back a ``float`` (or ``None``) even
    when ``step=1`` is set and the user typed a whole number. Runner
    metadata fields are all integer-typed on the model, so we coerce
    once at the boundary. Empty / ``None`` / blank-string inputs map to
    ``None`` so the "clear the value" UX stays symmetric with the DB
    NULL path.
    """

    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        raw = stripped
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _handle_exam_metadata_change(
    label: str,
    handler,
    raw_value: object,
    current: int | None,
) -> None:
    """Generic blur-save callback for the four exam metadata inputs.

    Normalises the widget value into ``int | None``, short-circuits on
    same-value blur (avoids DB writes when the user tabs through the
    field without changing anything), and then delegates to the
    ``update_exam_*_in_open_exam`` ``handler`` passed in by the
    caller.

    Standard notify matrix:

    * ``True`` → positive toast + refresh (summary card may want to
      update).
    * ``False`` → warning + refresh (stale summary — exam id vanished).
    * ``None`` → warning ("no exam open").
    * ``ValueError`` (Pydantic probe) → negative toast; the input keeps
      its current typed value so the user can correct it.
    * ``sqlite3.DatabaseError`` → negative toast.
    """

    new_value = _coerce_optional_int(raw_value)

    if new_value == current:
        return

    try:
        updated = handler(new_value)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot save {label}: {exc}", type="negative")
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not updated:
        ui.notify(
            "The open exam no longer exists — refresh the page.",
            type="warning",
        )
        _render_designer_body.refresh()
        return

    display = "(none)" if new_value is None else str(new_value)
    ui.notify(f"{label} saved ({display}).")
    _render_designer_body.refresh()


def _render_exam_metadata_row(
    label: str,
    current: int | None,
    handler,
    *,
    min_value: int | None = None,
    placeholder: str = "(none)",
    suffix: str | None = None,
) -> None:
    """Render one blur-save ``ui.number`` row inside the Exam Settings expansion.

    ``handler`` is the ``update_exam_*_in_open_exam`` callable to invoke
    on blur / Enter. ``min_value`` is an optional client-side lower
    bound passed to ``ui.number(min=…)`` so the Quasar widget refuses
    obvious out-of-range values before even firing the change — the
    real fail-fast Pydantic probe still runs inside the handler for
    safety. ``suffix`` is a free-text unit label shown next to the input
    ("min", "pts") so the Runner meaning is visible at a glance.
    """

    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label(label).classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        number_input = ui.number(
            value=current,
            placeholder=placeholder,
            min=min_value,
            step=1,
            format="%d",
        ).props("dense borderless").classes("col-grow")

        def _on_blur(
            evt=None,
            inp=number_input,
            lbl=label,
            cb=handler,
            cur=current,
        ) -> None:
            _handle_exam_metadata_change(lbl, cb, inp.value, cur)

        number_input.on("blur", _on_blur)
        number_input.on("keydown.enter", _on_blur)

        if suffix:
            ui.label(suffix).classes("text-caption text-grey")


def _render_exam_settings_section() -> None:
    """Render the "Exam settings" collapsible group of runner metadata.

    Reads the opened-exam summary from :data:`app.storage.user` so we
    can show the exam name in the expansion caption. Metadata values
    come from ``app.storage.user[OPEN_EXAM_STORAGE_KEY]["metadata"]``
    which is populated by ``_handle_open_click`` in
    :mod:`posrat.designer.browser`; when that key is missing (older
    summaries from before the 7A refactor, or a summary that has been
    invalidated) we fall through and render "Select a question…" without
    the exam settings header so nothing crashes.

    The whole section is a :func:`ui.expansion` (default collapsed) so
    the per-question properties below stay visible at first glance —
    the Runner config fields are authored once per exam, not per
    question, so hiding them behind a disclosure is appropriate.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return

    # The summary was populated by browser._handle_open_click / the
    # JSON round-trip from get_exam(). Metadata fields default to None
    # when the exam hasn't been opted into them yet. ``None`` values
    # also come back from .get() for keys missing from the summary
    # (older stash format), which is fine — ui.number(value=None)
    # renders as an empty field with the placeholder caption.
    metadata = summary.get("metadata") or {}
    default_count = metadata.get("default_question_count")
    time_limit = metadata.get("time_limit_minutes")
    passing_score = metadata.get("passing_score")
    target_score = metadata.get("target_score")

    with ui.expansion(
        "Exam settings", icon="settings"
    ).classes("w-full"):
        ui.label(
            "Default question count, time limit and passing/target "
            "score. Used at Runner start."
        ).classes("text-caption text-grey q-mb-sm")

        _render_exam_metadata_row(
            "Default # of questions",
            default_count,
            update_exam_default_question_count_in_open_exam,
            min_value=1,
            suffix="questions",
        )
        _render_exam_metadata_row(
            "Time limit",
            time_limit,
            update_exam_time_limit_minutes_in_open_exam,
            min_value=1,
            suffix="min",
        )
        _render_exam_metadata_row(
            "Passing score",
            passing_score,
            update_exam_passing_score_in_open_exam,
            min_value=0,
            suffix="pts",
        )
        _render_exam_metadata_row(
            "Target score",
            target_score,
            update_exam_target_score_in_open_exam,
            min_value=1,
            suffix="pts",
        )


def render_properties_panel() -> None:
    """Render the Properties panel for the selected question.

    Empty-state placeholder when no question is selected (happens when
    no exam is open or the exam has zero questions). Otherwise renders
    Answer Type dropdown + editable Complexity / Section rows + read-
    only scaffold rows. The Visual CertExam mockup shows additional
    rows (Exhibits, Allow Shuffle Choices) that we render as read-only
    scaffolds for now — their full edit flow lands with Phase 9.

    Since Phase 7A.5 the panel is split in two vertically: at the top
    a collapsible "Exam settings" group exposes the four Runner-facing
    exam metadata fields (default question count, timer, passing /
    target score); below that, the per-question grid keeps its
    existing shape.
    """

    ui.label("Properties").classes("text-subtitle2 text-weight-bold")

    # Exam-level settings are always rendered (when an exam is open)
    # so the user can edit them even before selecting a question.
    _render_exam_settings_section()

    question = get_selected_question()
    if question is None:
        ui.label("Select a question in the Exam Explorer.").classes(
            "text-caption text-grey q-mt-sm"
        )
        return


    # --- Answer Type (editable dropdown) ------------------------------
    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Answer Type").classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        type_select = ui.select(
            options=list(ALLOWED_QUESTION_TYPES),
            value=question.type,
        ).props("dense options-dense borderless").classes("col-grow")
        type_select.on_value_change(
            lambda evt, current=question.type: _handle_type_change(
                str(evt.value or ""), current
            )
        )

    # --- Read-only scaffold rows --------------------------------------
    number_of_choices = (
        str(len(question.choices)) if question.type != "hotspot" else "-"
    )
    last_letter = _compute_last_choice_letter(question)

    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Number of Choices").classes(
            "text-caption text-grey"
        ).style(f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;")
        _render_readonly_value(number_of_choices)

    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Last Choice Letter").classes(
            "text-caption text-grey"
        ).style(f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;")
        _render_readonly_value(last_letter)

    # --- Complexity (editable dropdown) -------------------------------
    # ``ui.select`` with a ``dict`` mapping value → label lets us show
    # "(none)" for the ``None`` state while still sending a real int
    # (or the sentinel) to the on-change handler. Using a dict keeps
    # the 1..5 display order stable regardless of Python's iteration
    # quirks.
    complexity_options: dict[object, str] = {
        COMPLEXITY_UNSET_LABEL: COMPLEXITY_UNSET_LABEL,
    }
    for value in range(MIN_COMPLEXITY, MAX_COMPLEXITY + 1):
        complexity_options[value] = str(value)

    current_complexity_key: object = (
        question.complexity
        if question.complexity is not None
        else COMPLEXITY_UNSET_LABEL
    )

    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Complexity").classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        complexity_select = ui.select(
            options=complexity_options,
            value=current_complexity_key,
        ).props("dense options-dense borderless").classes("col-grow")
        complexity_select.on_value_change(
            lambda evt, current=question.complexity: _handle_complexity_change(
                evt.value, current
            )
        )

    # --- Exhibits (read-only for now) ---------------------------------
    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Exhibits").classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        has_image = bool(question.image_path)
        _render_readonly_value("(attached)" if has_image else "(empty)")

    # --- Section (editable free-text input) ---------------------------
    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Section").classes("text-caption text-grey").style(
            f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;"
        )
        section_input = ui.input(
            value=question.section or "",
            placeholder="(none)",
        ).props("dense borderless").classes("col-grow")
        # ``on_change`` fires on every keystroke; we debounce to blur /
        # Enter by listening for ``blur`` and ``keydown.enter`` via the
        # raw Quasar events. Falling back to ``on_value_change`` would
        # otherwise hit the DB on every character.
        section_input.on(
            "blur",
            lambda evt=None, inp=section_input, current=question.section: _handle_section_change(
                inp.value, current
            ),
        )
        section_input.on(
            "keydown.enter",
            lambda evt=None, inp=section_input, current=question.section: _handle_section_change(
                inp.value, current
            ),
        )

    # --- Allow Shuffle Choices (editable checkbox) --------------------
    # Hidden for hotspot questions because the hotspot step order is
    # authored — the ``allow_shuffle`` column stays at its default
    # ``False`` for those rows (see Question model docstring).
    with ui.row().classes("items-center q-gutter-sm no-wrap w-full q-py-xs"):
        ui.label("Allow Shuffle Choices").classes(
            "text-caption text-grey"
        ).style(f"min-width: {PROPERTIES_LABEL_WIDTH_PX}px;")
        if question.type == "hotspot":
            _render_readonly_value("-")
        else:
            shuffle_checkbox = ui.checkbox(
                value=bool(question.allow_shuffle),
            ).props("dense")
            shuffle_checkbox.on_value_change(
                lambda evt, current=bool(question.allow_shuffle): _handle_allow_shuffle_change(
                    bool(evt.value), current
                )
            )


    ui.separator().classes("q-my-sm")
    ui.label(str(question.id)).classes(
        "text-caption text-grey ellipsis"
    ).tooltip("Stable question identifier (used for JSON export and session answers).")


__all__ = [
    "COMPLEXITY_UNSET_LABEL",
    "PROPERTIES_LABEL_WIDTH_PX",
    "render_properties_panel",
]
