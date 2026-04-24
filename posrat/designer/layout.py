"""Designer — 3-panel layout assembler (Visual CertExam-style).

Renders the full Designer page as a horizontal splitter:

* **Left column** — Exam Explorer (top) + Properties (bottom), themselves
  stacked via a vertical splitter.
* **Right column** — Main editor (Question + Choices + Explanation).

A slim top bar sits above the splitter with the exam name plus the
"Export JSON" button. No NiceGUI ``ui.header`` is used here — the top
menu bar from :func:`posrat.app._render_header` already owns the
outermost chrome.
"""

from __future__ import annotations

from nicegui import app, ui

from posrat.designer.browser import (
    OPEN_EXAM_STORAGE_KEY,
    _handle_export_exam_click,
    _prune_stale_open_exam,
    _render_designer_body,
)

from posrat.designer.editor import render_editor_panel
from posrat.designer.explorer import render_explorer_panel
from posrat.designer.properties import render_properties_panel


#: Initial split (in percent) between the left (Explorer + Properties)
#: and the right (Editor) column. Matches the Visual CertExam mockup
#: reasonably closely — the editor gets the lion's share because
#: question / choices / explanation textareas need horizontal room.
HORIZONTAL_SPLIT_PERCENT = 25

#: Initial split (in percent) between the Explorer (top) and Properties
#: (bottom) inside the left column. Explorer gets the larger share
#: because it holds the scrollable question list.
VERTICAL_SPLIT_PERCENT = 60

#: Full-viewport height applied to the outer splitter so both columns
#: extend to the bottom of the window. ``calc(100vh - 96px)`` leaves
#: room for the top menu bar (NiceGUI header ~64 px) and the status
#: caption at the bottom of the page.
LAYOUT_HEIGHT_CSS = "calc(100vh - 120px)"


def _render_header_bar() -> None:
    """Render the tiny bar above the splitter — exam meta + export.

    Shown even when no exam is open (so the "New exam / Open" toolbar
    in the Explorer panel stays reachable) but the export button is
    disabled because there is nothing to export.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)

    with ui.row().classes("items-center q-gutter-sm w-full q-mb-xs"):
        if summary:
            ui.label(f"{summary.get('name')}").classes(
                "text-subtitle1 text-weight-medium"
            )
            ui.label(
                f"({summary.get('question_count')} questions)"
            ).classes("text-caption text-grey")
        else:
            ui.label("Designer").classes(
                "text-subtitle1 text-weight-medium"
            )

        # Push the export button to the far right.
        ui.space()

        export_button = ui.button(
            "Exportovat JSON",
            icon="download",
            on_click=_handle_export_exam_click,
        ).props("size=sm flat color=secondary")
        if not summary:
            export_button.props("disable")


@ui.refreshable
def render_designer_layout() -> None:
    """Render the full 3-panel Designer layout (public entry point).

    Wrapped in :func:`ui.refreshable` so the existing per-field dialog
    handlers can call ``render_designer_layout.refresh()`` once the
    transition is fully over. Until then the legacy
    :func:`_render_designer_body.refresh()` is the refresh target, and
    this wrapper simply re-reads user storage on every call.

    Runs :func:`_prune_stale_open_exam` first so a mid-session Admin →
    Exams delete doesn't leave us rendering header metadata for a
    just-disappeared file (and, more importantly, doesn't let any
    ``open_db`` call below resurrect the deleted ``.sqlite`` via
    ``sqlite3.connect``'s default "create on missing" behaviour).
    """

    _prune_stale_open_exam()
    _render_header_bar()


    with ui.splitter(
        horizontal=False, value=HORIZONTAL_SPLIT_PERCENT
    ).classes("w-full").style(f"height: {LAYOUT_HEIGHT_CSS};") as outer:
        with outer.before:
            # Left column — Explorer (top) + Properties (bottom).
            with ui.splitter(
                horizontal=True, value=VERTICAL_SPLIT_PERCENT
            ).classes("w-full h-full") as inner:
                with inner.before:
                    with ui.column().classes("w-full q-pa-sm"):
                        render_explorer_panel()
                with inner.after:
                    with ui.column().classes("w-full q-pa-sm"):
                        render_properties_panel()
        with outer.after:
            with ui.column().classes("w-full q-pa-sm"):
                render_editor_panel()


__all__ = [
    "HORIZONTAL_SPLIT_PERCENT",
    "LAYOUT_HEIGHT_CSS",
    "VERTICAL_SPLIT_PERCENT",
    "render_designer_layout",
]
