
"""Designer — top-left Exam Explorer panel.

Renders the tree-like list of questions for the currently opened exam.
Each row is clickable and updates the user-scoped "selected question
id" state (see :mod:`posrat.designer.state`), which the Properties
and Editor panels then pick up to render the right fields.

The panel also owns the small toolbar on top with Open / New / Add /
Move up / Move down actions — exactly the ones from the Visual
CertExam mockup. Exporting the exam as JSON lives in the top header
bar (different scope), not here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nicegui import app, ui

from posrat.designer.browser import (
    MOVE_DOWN,
    MOVE_UP,
    OPEN_EXAM_STORAGE_KEY,
    SEARCH_QUERY_STORAGE_KEY,
    _handle_add_question_click,
    _handle_delete_question_click,
    _handle_move_question_click,
    _render_designer_body,
    _show_new_exam_dialog,
    filter_questions,
    format_question_label,
    list_exam_files,
    load_questions_for_open_exam,
    move_question_in_open_exam,
    open_exam_from_file,
    resolve_data_dir,
)
from posrat.designer.import_dialog import _show_bulk_import_dialog
from posrat.designer.state import (
    ensure_selection_valid,
    get_selected_question_id,
    select_question,
)
from posrat.models import Question


#: Maximum pixel height of the scrollable question list so the panel
#: never expands beyond the viewport. The outer :func:`ui.scroll_area`
#: takes care of overflow — picking a small cap keeps the Properties
#: panel (which sits right below) permanently visible.
QUESTION_LIST_MAX_HEIGHT_PX = 480

#: Pixel width of the question-label column inside an Explorer row.
#: Kept narrow because "Qn" labels never exceed 4 characters — the
#: remaining horizontal space is reserved for the truncated question
#: text preview. Separate constant from the legacy
#: ``QUESTION_LIST_TEXT_WIDTH_PX`` because the Explorer is narrower
#: than the legacy full-width row.
EXPLORER_LABEL_WIDTH_PX = 56


def _handle_select_row(question_id: str) -> None:
    """Store ``question_id`` as the selected row and refresh the body.

    A full Designer refresh is used (rather than a narrower targeted
    refresh) because the selection change must propagate to the
    Properties and Editor panels simultaneously — all three live inside
    :func:`_render_designer_body` for the 3-panel layout.
    """

    select_question(question_id)
    _render_designer_body.refresh()


def _handle_move_selected(direction: str) -> None:
    """Move the currently selected question up / down and keep the highlight.

    Wrapper around :func:`_handle_move_question_click` that looks up
    the full :class:`Question` matching the stored selection id so the
    legacy move handler's ``question`` argument stays happy. Selection
    itself does not change (the row still points at the same id, which
    now sits at a different index).
    """

    selected_id = get_selected_question_id()
    if selected_id is None:
        ui.notify("Select a question first.", type="warning")
        return

    for question in load_questions_for_open_exam():
        if question.id == selected_id:
            _handle_move_question_click(question, direction)
            return

    ui.notify("The selected question is gone, refreshing.", type="warning")
    _render_designer_body.refresh()


def _handle_delete_selected() -> None:
    """Open the delete-confirm dialog for the currently selected row."""

    selected_id = get_selected_question_id()
    if selected_id is None:
        ui.notify("Select a question first.", type="warning")
        return

    for question in load_questions_for_open_exam():
        if question.id == selected_id:
            _handle_delete_question_click(question)
            return

    ui.notify("The selected question is gone, refreshing.", type="warning")
    _render_designer_body.refresh()


def _handle_open_file_click(path: Path) -> None:
    """Wrapper around :func:`open_exam_from_file` that syncs selection.

    The legacy :func:`posrat.designer.browser._handle_open_click`
    refreshes the body but does not touch the selection key, so a
    stored selection from a *previous* exam would dangle until the
    next render. Clearing it here keeps the Properties / Editor
    panels' "select the first question" auto-pick semantics working
    cleanly on first open.
    """

    try:
        exam = open_exam_from_file(path)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot open {path.name}: {exc}", type="negative")
        return

    app.storage.user[OPEN_EXAM_STORAGE_KEY] = {
        "path": str(path.resolve()),
        "id": exam.id,
        "name": exam.name,
        "description": exam.description,
        "question_count": len(exam.questions),
    }
    # Clear stale selection (might refer to a question from the
    # previous exam); ``ensure_selection_valid`` below will seed the
    # first row of the newly opened exam on the next render.
    select_question(None)
    ui.notify(f"Exam '{exam.name}' opened ({len(exam.questions)} questions).")
    _render_designer_body.refresh()


def _render_exam_file_picker() -> None:
    """Inline popover with the list of ``*.sqlite`` files under data_dir.

    Shown when the user clicks the "Open" button in the Explorer
    toolbar. Each row opens the corresponding exam via
    :func:`_handle_open_file_click`. No search / pagination — MVP
    covers a handful of exam files at most; a search input can be
    added later if the list grows.
    """

    data_dir = resolve_data_dir()
    exam_files = list_exam_files(data_dir)

    with ui.menu() as menu:
        if not exam_files:
            with ui.menu_item().props("disable"):
                ui.label("No .sqlite files in the data directory.")
        else:
            for exam_file in exam_files:

                def _on_pick(
                    _evt=None, p=exam_file, m=menu
                ) -> None:
                    m.close()
                    _handle_open_file_click(p)

                with ui.menu_item(on_click=_on_pick):
                    ui.label(exam_file.name)

    return menu


def _render_toolbar() -> None:
    """Render the tiny toolbar on top of the Explorer.

    Buttons mirror the Visual CertExam mockup — Open, New, Move up,
    Move down, Add. Deletion lives in the Properties panel's context
    menu (5.5 flow), and export JSON is in the top header bar (outside
    the Explorer's scope).
    """

    with ui.row().classes("items-center q-gutter-xs q-mb-sm no-wrap"):
        open_menu = _render_exam_file_picker()
        ui.button(icon="folder_open").props("flat dense size=sm").on_click(
            open_menu.open
        ).tooltip("Open exam")
        ui.button(
            icon="add_box",
            on_click=_show_new_exam_dialog,
        ).props("flat dense size=sm").tooltip("New exam")

        # Separator between file-level and question-level actions.
        ui.separator().props("vertical").classes("q-mx-xs")

        ui.button(
            icon="arrow_upward",
            on_click=lambda _evt=None: _handle_move_selected(MOVE_UP),
        ).props("flat dense size=sm color=primary").tooltip("Move up")
        ui.button(
            icon="arrow_downward",
            on_click=lambda _evt=None: _handle_move_selected(MOVE_DOWN),
        ).props("flat dense size=sm color=primary").tooltip("Move down")
        ui.button(
            icon="add",
            on_click=_handle_add_question_click,
        ).props("flat dense size=sm color=primary").tooltip("Add question")
        ui.button(
            icon="delete",
            on_click=_handle_delete_selected,
        ).props("flat dense size=sm color=negative").tooltip("Delete question")

        # Bulk-import lives at the file-level end of the toolbar: it
        # adds many questions at once, so it belongs next to the
        # per-question actions but is visually grouped with them via
        # the shared q-gutter-xs spacing (no extra separator needed).
        # Two buttons with format-specific Material icons make the
        # source type obvious at a glance — ``article`` for the
        # ExamTopics RTF flow and ``picture_as_pdf`` for the CertExam
        # Designer PDF flow — and each opens the shared bulk-import
        # dialog pre-locked to the matching parser (so the file picker
        # auto-filters by extension and no dropdown is needed).
        ui.button(
            icon="article",
            on_click=lambda _evt=None: _show_bulk_import_dialog(
                preselect_source_id="examtopics"
            ),
        ).props("flat dense size=sm color=primary").tooltip(
            "Bulk import (RTF — ExamTopics)"
        )
        ui.button(
            icon="picture_as_pdf",
            on_click=lambda _evt=None: _show_bulk_import_dialog(
                preselect_source_id="certexam_pdf"
            ),
        ).props("flat dense size=sm color=primary").tooltip(
            "Bulk import (PDF — CertExam Designer)"
        )


def _render_question_row(
    question: Question,
    index: int,
    selected_id: str | None,
) -> None:
    """Render a single ``Qn`` row with selection highlight.

    Rows are clickable — clicking anywhere (badge, label, text) stores
    the id as the selected row and refreshes the Designer body so the
    Properties and Editor panels pick up the new selection. Selected
    rows get a ``bg-blue-1`` background (matches the Quasar blue
    accent) and a bold label; the rest stay neutral.
    """

    is_selected = question.id == selected_id
    row_classes = (
        "items-center q-gutter-sm no-wrap cursor-pointer q-px-sm q-py-xs"
        + (" bg-blue-1" if is_selected else "")
    )

    row = ui.row().classes(row_classes).on(
        "click",
        lambda _evt=None, qid=question.id: _handle_select_row(qid),
    )
    # Tagging the selected row with a stable DOM id lets us scroll it
    # into view on the next render — without it, selecting a question
    # deep in a long exam (e.g. Q181 in the ExamTopics import) loses
    # the row off-screen and the user can't tell which question the
    # Main Editor is showing. The scroll JS runs once per render in
    # ``render_explorer_panel`` after the list is assembled.
    if is_selected:
        row.props('id="explorer-selected-row"')

    with row:
        # Quasar-style book icon (green) matches the Visual CertExam
        # question glyph reasonably closely — we don't have the exact
        # mockup icon, so this is the nearest built-in equivalent.
        ui.icon("menu_book", color="positive").classes("text-sm")
        label_classes = "text-body2"
        if is_selected:
            label_classes += " text-weight-bold"
        ui.label(format_question_label(index)).classes(label_classes).style(
            f"min-width: {EXPLORER_LABEL_WIDTH_PX}px;"
        ).tooltip(question.id)


def render_explorer_panel() -> None:
    """Render the Exam Explorer panel (top-left of the 3-panel layout).

    Renders toolbar, search input and scrollable question list. The
    scroll area is capped at :data:`QUESTION_LIST_MAX_HEIGHT_PX` so
    the Properties panel sitting below always stays visible.

    When no exam is open the panel shows a muted placeholder; when an
    exam is open but empty, it invites the user to click "Add
    question". Both states match the legacy :func:`_render_question_list`
    contract.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)

    ui.label("Exam Explorer").classes("text-subtitle2 text-weight-bold")

    _render_toolbar()

    if not summary:
        ui.label("Open an exam using the 'Open' button.").classes(
            "text-caption text-grey q-mt-sm"
        )
        return

    ui.label(str(summary.get("name"))).classes(
        "text-caption text-grey ellipsis"
    ).tooltip(str(summary.get("path")))

    questions = load_questions_for_open_exam()
    selected_id = ensure_selection_valid(questions)

    # Search input persists across refreshes via the existing
    # SEARCH_QUERY_STORAGE_KEY so the Explorer, Properties and Editor
    # all see the same filter.
    app.storage.user.setdefault(SEARCH_QUERY_STORAGE_KEY, "")
    ui.input(
        placeholder="Search (id or text)…",
        on_change=lambda _evt: _render_designer_body.refresh(),
    ).bind_value(app.storage.user, SEARCH_QUERY_STORAGE_KEY).props(
        "clearable dense outlined"
    ).classes("w-full q-mt-xs")

    query = str(app.storage.user.get(SEARCH_QUERY_STORAGE_KEY, ""))

    if not questions:
        ui.label("No questions yet. Add one with '+'.").classes(
            "text-caption text-grey q-mt-sm"
        )
        return

    visible = filter_questions(questions, query)
    if not visible:
        ui.label("No question matches the filter.").classes(
            "text-caption text-grey q-mt-sm"
        )
        return

    with ui.scroll_area().classes("w-full").style(
        f"max-height: {QUESTION_LIST_MAX_HEIGHT_PX}px;"
    ):
        for question in visible:
            # Index into the *full* (unfiltered) list so the Qn label
            # matches the on-disk order — filtering hides rows but
            # keeps their numbering stable.
            index = questions.index(question)
            _render_question_row(question, index, selected_id)

    # Scroll the selected row into view after each render so switching
    # questions (or coming back via a dialog) always keeps the current
    # target visible — the block is ``nearest`` so we don't jerk the
    # scroll position when the row is already on screen.
    if selected_id is not None:
        ui.run_javascript(
            "const el = document.getElementById('explorer-selected-row');"
            "if (el) el.scrollIntoView({block: 'nearest', behavior: 'smooth'});"
        )


__all__ = [
    "EXPLORER_LABEL_WIDTH_PX",
    "QUESTION_LIST_MAX_HEIGHT_PX",
    "render_explorer_panel",
]
