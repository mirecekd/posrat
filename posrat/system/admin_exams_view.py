"""Admin → **Exams** tab.

Extracted from the original monolithic ``admin_view.py`` as part of
the Phase 10 stretch refactor. Responsibility is the exams tab of
``/admin``:

- list every ``.sqlite`` exam file in the data directory,
- show size and path,
- offer a "Delete" button that removes the file + assets + ACL rows
  in one atomic helper (:func:`delete_exam_completely`).

``delete_exam_completely`` is intentionally public (not underscored)
so admin scripts / tests can reuse it without needing a NiceGUI
context. The UI wrapper adds a confirm dialog and a notify + refresh
cycle on top.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from nicegui import ui

from posrat.designer.browser import list_exam_files, resolve_data_dir
from posrat.system.acl_repo import purge_acl_for_exam
from posrat.system.system_db import open_system_db, resolve_system_db_path


@ui.refreshable
def render_exams_tab() -> None:
    ui.label("Exams").classes("text-h6 q-mt-md")
    data_dir = resolve_data_dir()
    files = list_exam_files(data_dir)

    if not files:
        ui.label("Data directory is empty.").classes("q-mt-md")
        return

    with ui.column().classes("q-gutter-sm w-full"):
        for path in files:
            _render_exam_row(path, data_dir)


def _render_exam_row(path: Path, data_dir: Path) -> None:
    size_bytes = path.stat().st_size
    size_kb = size_bytes / 1024.0
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center q-gutter-md w-full"):
            with ui.column().classes("col-grow"):
                ui.label(path.name).classes("text-subtitle1")
                ui.label(f"{size_kb:.1f} kB · {path}").classes(
                    "text-caption text-grey-7"
                )
            ui.button(
                "Delete",
                on_click=lambda _evt=None, p=path: _confirm_delete_exam(
                    p, data_dir
                ),
            ).props("flat color=negative size=sm")


def _confirm_delete_exam(path: Path, data_dir: Path) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label(f"Delete exam {path.name}?").classes("text-h6")
        ui.label(
            "Removes the SQLite file, the assets directory and all ACL"
            " records. This action cannot be undone."
        ).classes("text-caption text-grey")

        def _do_delete() -> None:
            try:
                delete_exam_completely(path, data_dir)
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    f"Cannot delete: {exc}", type="negative"
                )
                return
            ui.notify(f"Exam {path.name} deleted.")
            dialog.close()
            render_exams_tab.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=_do_delete).props(
                "color=negative"
            )

    dialog.open()


def delete_exam_completely(path: Path, data_dir: Path) -> None:
    """Remove the ``.sqlite`` file, the ``assets/<exam_id>/`` dir, and ACLs.

    Pure helper (no NiceGUI calls) so admin scripts / tests can reuse
    it. Exam id is derived from the filename stem (matches the
    Designer's naming convention).
    """

    exam_id = path.stem

    # ACL purge first — if the file delete fails we don't want to
    # orphan disk state.
    db = open_system_db(resolve_system_db_path(data_dir))
    try:
        purge_acl_for_exam(db, exam_id)
    finally:
        db.close()

    # Assets directory for this exam — missing is fine.
    assets_dir = Path(data_dir) / "assets" / exam_id
    if assets_dir.is_dir():
        shutil.rmtree(assets_dir)

    # The database file itself.
    if path.is_file():
        path.unlink()


__all__ = ["delete_exam_completely", "render_exams_tab"]
