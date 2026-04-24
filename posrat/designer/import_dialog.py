"""Designer — "Bulk import" dialog (Phase 8.3).

Glue layer between the Designer UI and the pluggable bulk-import
pipeline (:mod:`posrat.importers`). The dialog is opened from the Exam
Explorer toolbar; it drives the user through three states:

1. **Upload**: pick an import source (ExamTopics RTF today) and upload
   the file. The bytes are written to a ``tempfile`` and fed straight
   into :meth:`ImportSource.parse`; the resulting :class:`ParseResult`
   is stashed in :data:`app.storage.user` under
   :data:`PENDING_IMPORT_STORAGE_KEY` so the preview step does not have
   to re-parse.
2. **Preview**: render a scrollable table of :class:`ParsedQuestion`
   rows with per-row checkboxes (``✓`` by default when
   ``warnings == []``), a "Select all" toggle, a live
   "Selected ``X``/``Y``" counter, plus an expandable list of
   :class:`ParseError` blocks that could not be parsed at all.
3. **Commit**: pass the ticked rows to
   :func:`persist_parsed_questions` and surface an
   :class:`ImportReport` summary via ``ui.notify`` ("Imported X,
   skipped Y"). A full Designer refresh picks up the new questions
   in the Explorer immediately afterwards.

Why its own module (not inside :mod:`posrat.designer.browser`):

* ``browser.py`` is already the 3100-line legacy DAO host; adding
  another ~250-line workflow would make the circular-import wrangling
  harder (the layout module already reaches back into browser).
* The preview table is self-contained — it does not need to share
  refreshable scope with the Properties / Editor panels.

Tests stay smoke-only (import + render coverage via the app smoke
harness): the interesting logic already lives under
:mod:`posrat.importers.examtopics` and
:mod:`posrat.importers.conversion`, both of which have direct unit
test suites.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from nicegui import app, events, ui

from posrat.designer.browser import (
    OPEN_EXAM_STORAGE_KEY,
    _render_designer_body,
    resolve_data_dir,
)
from posrat.importers import (
    ParseResult,
    ParsedQuestion,
    get_import_source,
    list_import_sources,
    persist_parsed_questions,
)


#: Process-local cache of the last :class:`ParseResult` per user id.
#: We **cannot** stash the object in :data:`app.storage.user` because
#: it is a file-persistent JSON dict and the embedded
#: :class:`ParsedImage.data` (raw ``bytes``) is not JSON-serialisable
#: (NiceGUI 3 crashes with ``Type is not JSON serializable: bytes``
#: inside its background ``file_persistent_dict`` writer). A plain
#: module-level dict keyed by the user's storage id keeps the object
#: alive for the duration of the session without triggering the
#: background JSON write.
_PARSE_RESULT_CACHE: dict[str, ParseResult] = {}
#: Legacy constant name retained so tests / tooling that imported it
#: before the in-memory refactor still resolve. The value is not a
#: real storage key anymore — it lives here as a stable identifier.
PENDING_IMPORT_STORAGE_KEY = "pending_import"

#: Key under which the set of checked ``source_index`` values is
#: persisted across refreshes of the preview table. Stored as a
#: ``list[int]`` (JSON-friendly) rather than a ``set`` so the value
#: round-trips cleanly through NiceGUI's signed storage cookie.
PENDING_IMPORT_SELECTION_KEY = "pending_import_selection"

#: Max bytes accepted by the upload widget. 20 MB comfortably fits the
#: reference ExamTopics dump (13 MB) with headroom for future larger
#: exam sets, while still rejecting pathological uploads that would
#: just choke the parser without contributing useful content.
MAX_IMPORT_FILE_BYTES = 20_000_000

#: Character cap on the question-text preview column in the preview
#: table. Matches the Designer's existing :data:`QUESTION_LIST_TEXT_PREVIEW`
#: philosophy — long texts get truncated with ``…`` so rows stay single-
#: line and scan easily.
PREVIEW_TEXT_MAX_CHARS = 90


def _truncate(text: str, limit: int = PREVIEW_TEXT_MAX_CHARS) -> str:
    """Return ``text`` clipped to ``limit`` with a trailing ellipsis.

    Kept as a local helper (instead of pulling from ``browser.py``) so
    this module stays importable without dragging the full browser
    namespace in through the :mod:`posrat.designer.browser` top-level
    (which it does already — but via named re-exports, not ``*``).
    """

    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _format_question_type(question_type: str) -> str:
    """Return a short, human-friendly label for a question type.

    Matches the terminology used elsewhere in the Designer
    ("Single" / "Multi" / "Hotspot"). Unknown types
    degrade to the raw identifier so future additions do not need a
    code change to be at least renderable.
    """

    return {
        "single_choice": "Single",
        "multi_choice": "Multi",
        "hotspot": "Hotspot",
    }.get(question_type, question_type)


# ---------------------------------------------------------------------------
# Storage helpers (persist/retrieve :class:`ParseResult` across refreshes)
# ---------------------------------------------------------------------------


def _cache_key() -> str:
    """Return the cache key for the current session.

    Prefers :data:`app.storage.browser['id']` (stable per-browser
    identifier) when available; falls back to ``"__default__"`` for
    unit tests that exercise helpers outside a NiceGUI request
    context. The fallback is safe because the module-level dict is
    process-scoped and the tests never leave stale values in it.
    """

    try:
        browser_storage = app.storage.browser
    except Exception:
        return "__default__"
    raw = browser_storage.get("id") if browser_storage else None
    return str(raw) if raw else "__default__"


def _store_parse_result(result: ParseResult) -> None:
    """Cache ``result`` in the process-local dict for later preview.

    The object holds :class:`ParsedImage.data` ``bytes`` which are not
    JSON-serialisable (NiceGUI 3's file-persistent ``app.storage.user``
    crashes the moment it tries to round-trip them). Keeping the
    ``ParseResult`` in a module-level dict avoids the JSON writer
    entirely and still scopes the data per-browser via
    :func:`_cache_key`. Only the tiny ``list[int]`` selection state
    goes through ``app.storage.user`` — it is JSON-clean.
    """

    _PARSE_RESULT_CACHE[_cache_key()] = result

    # Seed the selection with every question whose parser attached no
    # warnings — the user can tweak individual rows afterwards.
    default_selection = [
        q.source_index for q in result.questions if not q.warnings
    ]
    app.storage.user[PENDING_IMPORT_SELECTION_KEY] = default_selection


def _get_parse_result() -> ParseResult | None:
    """Return the cached :class:`ParseResult` or ``None`` when absent."""

    value = _PARSE_RESULT_CACHE.get(_cache_key())
    return value if isinstance(value, ParseResult) else None


def _get_selection_set() -> set[int]:
    """Return the set of currently ticked ``source_index`` values."""

    raw = app.storage.user.get(PENDING_IMPORT_SELECTION_KEY) or []
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        return set()


def _set_selection(selection: set[int]) -> None:
    """Persist ``selection`` back to :data:`app.storage.user`."""

    app.storage.user[PENDING_IMPORT_SELECTION_KEY] = sorted(selection)


def _clear_pending_import() -> None:
    """Drop both the cached :class:`ParseResult` and the selection list.

    Called when the dialog is closed (with or without a successful
    commit) so the next "Bulk import" click starts from a blank
    slate. ``dict.pop`` with a default and ``app.storage.user.pop``
    keep the helper idempotent when either side is already missing.
    """

    _PARSE_RESULT_CACHE.pop(_cache_key(), None)
    app.storage.user.pop(PENDING_IMPORT_SELECTION_KEY, None)


# ---------------------------------------------------------------------------
# Parsing (called from the upload handler)
# ---------------------------------------------------------------------------


def _parse_uploaded_bytes(
    source_id: str, filename: str, data: bytes
) -> ParseResult:
    """Write ``data`` to a tempfile and parse it via the registered source.

    A tempfile is used — rather than a :class:`io.BytesIO` — because
    every :class:`ImportSource.parse` implementation takes a
    :class:`pathlib.Path` and may call ``path.read_bytes()`` (the
    ExamTopics parser does). Reaching for :class:`tempfile.NamedTemporaryFile`
    with ``delete=False`` lets us pass a real filesystem path while still
    guaranteeing cleanup in the ``finally`` block.

    The original filename's suffix is preserved so sources that branch
    on extension (``file_extensions``) see a realistic path. If the
    user uploads a file with no suffix we fall back to an empty one —
    the parser may still succeed (ExamTopics matches content, not
    extension) or raise, which surfaces as a negative toast.
    """

    suffix = Path(filename).suffix or ""
    parser = get_import_source(source_id)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp_path = Path(f.name)
    try:
        return parser.parse(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Commit (called from the "Import selected" button)
# ---------------------------------------------------------------------------


def _commit_selected_to_open_exam(selected: list[ParsedQuestion]) -> Any:
    """Persist ``selected`` into the currently opened exam.

    Returns the :class:`~posrat.importers.conversion.ImportReport` on
    success or ``None`` when no exam is open (the caller surfaces
    that as a warning toast). All database / filesystem errors
    propagate up to the caller, which maps them to negative toasts.

    Separated from the button's ``on_click`` so the logic stays
    unit-testable without NiceGUI state.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return persist_parsed_questions(
        selected,
        db_path=Path(str(summary.get("path"))),
        data_dir=resolve_data_dir(),
        exam_id=str(summary.get("id")),
    )


# ---------------------------------------------------------------------------
# UI rendering
# ---------------------------------------------------------------------------


@ui.refreshable
def _render_preview_body(dialog: ui.dialog) -> None:
    """Render the preview table (questions + errors + action buttons).

    Structured as an :func:`ui.refreshable` so checkbox toggles (per-
    row + "Select all") can re-render the selection counter in place
    without redrawing the whole dialog. Called in two situations:

    * Right after a successful parse, to seed the initial view.
    * After every checkbox flip via ``on_change``.

    When no :class:`ParseResult` is stashed the body shows a muted
    "Upload a file above" hint; the upload widget above the body
    stays usable so the user can re-upload without closing the dialog.
    """

    result = _get_parse_result()
    if result is None:
        ui.label("No file has been uploaded yet.").classes(
            "text-caption text-grey"
        )
        return

    selection = _get_selection_set()
    total = len(result.questions)

    with ui.row().classes("items-center q-gutter-sm q-mb-sm"):
        all_selected = selection == {q.source_index for q in result.questions}

        def _toggle_all(evt: events.ValueChangeEventArguments) -> None:
            if evt.value:
                _set_selection({q.source_index for q in result.questions})
            else:
                _set_selection(set())
            _render_preview_body.refresh(dialog)

        ui.checkbox("Select all", value=all_selected, on_change=_toggle_all)
        ui.label(f"Selected {len(selection)}/{total}").classes(
            "text-caption text-grey"
        )

    if result.parse_errors:
        with ui.expansion(
            f"Parse errors ({len(result.parse_errors)})",
            icon="error_outline",
        ).classes("q-mb-sm"):
            for err in result.parse_errors:
                ui.label(f"{err.source_range}: {err.reason}").classes(
                    "text-caption"
                )

    if not result.questions:
        ui.label(
            "The parser found no questions. Check the file format."
        ).classes("text-caption text-negative")
        return

    # Scrollable list of per-question rows so even a 181-question RTF
    # stays navigable inside a single dialog. Each row is purely
    # presentational + checkbox; detailed editing happens later in the
    # Properties panel once the question is imported.
    with ui.scroll_area().classes("w-full").style("max-height: 420px;"):
        for parsed in result.questions:
            is_ticked = parsed.source_index in selection
            with ui.row().classes(
                "items-center q-gutter-sm no-wrap q-py-xs"
            ):

                def _on_toggle(
                    evt: events.ValueChangeEventArguments,
                    idx: int = parsed.source_index,
                ) -> None:
                    current = _get_selection_set()
                    if evt.value:
                        current.add(idx)
                    else:
                        current.discard(idx)
                    _set_selection(current)
                    _render_preview_body.refresh(dialog)

                ui.checkbox(value=is_ticked, on_change=_on_toggle)
                ui.label(f"Q{parsed.source_index}").classes(
                    "text-caption text-weight-bold"
                ).style("min-width: 48px;")
                ui.badge(_format_question_type(parsed.question_type)).props(
                    "color=primary outline"
                )
                ui.label(_truncate(parsed.text)).classes(
                    "text-body2 ellipsis"
                ).style("flex: 1;")
                ui.label(
                    f"{len(parsed.choices)} choices, "
                    f"{len(parsed.images)} images"
                ).classes("text-caption text-grey")
                if parsed.warnings:
                    # Amber warning icon + tooltip with the joined
                    # messages. Explicit tooltip (instead of
                    # bubbling them into the text) keeps the row
                    # compact while the details stay one hover away.
                    ui.icon("warning", color="warning").tooltip(
                        "; ".join(parsed.warnings)
                    )

    def _handle_commit() -> None:
        current = _get_selection_set()
        picked = [
            q for q in result.questions if q.source_index in current
        ]
        if not picked:
            ui.notify(
                "No question selected for import.", type="warning"
            )
            return

        try:
            report = _commit_selected_to_open_exam(picked)
        except (
            LookupError,
            ValueError,
            sqlite3.DatabaseError,
            OSError,
        ) as exc:
            ui.notify(f"Import failed: {exc}", type="negative")
            return

        if report is None:
            ui.notify("No exam is open.", type="warning")
            return

        skipped_count = len(report.skipped)
        if skipped_count:
            ui.notify(
                f"Imported {report.imported}, skipped "
                f"{skipped_count} (see console).",
                type="warning",
            )
            for parsed, reason in report.skipped:
                # Per-skip log line so the user can find the offending
                # question without a dedicated UI drill-down view.
                print(
                    f"[bulk-import] Q{parsed.source_index} skipped: {reason}"
                )
        else:
            ui.notify(
                f"Imported {report.imported} questions.", type="positive"
            )

        # Refresh the opened-exam summary's cached ``question_count`` so
        # the Exam Explorer header reflects the new count without the
        # user having to re-open the exam.
        summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
        if summary:
            summary["question_count"] = (
                int(summary.get("question_count", 0)) + report.imported
            )
            app.storage.user[OPEN_EXAM_STORAGE_KEY] = summary

        _clear_pending_import()
        dialog.close()
        _render_designer_body.refresh()

    with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
        ui.button(
            "Cancel",
            on_click=lambda _evt=None: (
                _clear_pending_import(),
                dialog.close(),
            ),
        ).props("flat")
        ui.button(
            f"Import selected ({len(selection)})",
            on_click=_handle_commit,
        ).props("color=primary")


def _show_bulk_import_dialog(preselect_source_id: str | None = None) -> None:
    """Open the bulk-import dialog and wire the upload → preview flow.

    Parameters
    ----------
    preselect_source_id:
        Optional ``source_id`` of the importer to pre-select and lock
        the dialog to. When supplied the source-type dropdown is
        hidden and the file picker's ``accept=`` attribute is narrowed
        to the selected parser's extensions — this is what the
        per-format toolbar buttons (RTF vs PDF) use so each one opens
        a single-purpose dialog. When ``None`` the dropdown is shown
        with every registered parser (legacy behaviour).

    The dialog stays open across the upload → preview → commit cycle;
    closing it (Cancel or successful commit) clears the stashed
    :class:`ParseResult` so the next invocation starts from a clean
    slate.

    If no exam is currently open we refuse to open the dialog at all —
    there is nowhere for the import to land. The user sees a warning
    toast and the Designer stays as-is.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        ui.notify(
            "Open an exam first before importing into it.",
            type="warning",
        )
        return

    _clear_pending_import()
    sources = list_import_sources()
    if not sources:
        ui.notify("No import source is registered.", type="negative")
        return

    # Resolve the effective source — either the caller-supplied preselect
    # (validated against the registry) or the first registered parser.
    # An unknown ``preselect_source_id`` falls back to the default and a
    # warning toast so a stale reference from the toolbar can't wedge
    # the dialog open empty.
    effective_sources = sources
    if preselect_source_id is not None:
        matching = [s for s in sources if s.source_id == preselect_source_id]
        if matching:
            effective_sources = matching
        else:
            ui.notify(
                f"Unknown import source {preselect_source_id!r};"
                " falling back to default.",
                type="warning",
            )
    default_source_id = effective_sources[0].source_id

    with ui.dialog() as dialog, ui.card().style("min-width: 720px;"):
        ui.label(
            f"Bulk import of questions — {effective_sources[0].display_name}"
            if preselect_source_id is not None
            else "Bulk import of questions"
        ).classes("text-h6")
        ui.label(
            f"Questions will be added to exam: {summary.get('name')}"
        ).classes("text-caption text-grey q-mb-sm")

        # Source dropdown — visible only when the caller did not
        # pre-select a single parser. In preselect mode the dropdown
        # is replaced by a fixed hidden value so the upload handler
        # still has a resolved ``source_id``.
        source_options = {s.source_id: s.display_name for s in sources}
        if preselect_source_id is None:
            source_select = ui.select(
                options=source_options,
                value=default_source_id,
                label="Source type",
            ).classes("w-full")
        else:
            # Tiny shim object so the upload handler below can keep
            # its ``source_select.value`` access pattern unchanged —
            # no separate code path needed.
            class _FixedSource:
                value = default_source_id

            source_select = _FixedSource()

        async def _handle_upload(event: events.UploadEventArguments) -> None:
            # NiceGUI 3's ``UploadEventArguments`` exposes the payload
            # as an awaitable ``event.file.read()`` coroutine (backed
            # by Starlette's ``UploadFile``). There is no synchronous
            # ``.content`` attribute — the NiceGUI 2.x API that shipped
            # such a property has been removed. Mirrors the async
            # upload handlers already in use in :mod:`posrat.designer.editor`
            # and :mod:`posrat.designer.browser`, so the dialog stays
            # consistent with the rest of the Designer and the user sees
            # identical error paths.
            source_id = str(source_select.value or sources[0].source_id)
            try:
                data = await event.file.read()
            except Exception as exc:  # pragma: no cover - defensive
                ui.notify(f"Cannot read file: {exc}", type="negative")
                return

            if len(data) > MAX_IMPORT_FILE_BYTES:
                ui.notify(
                    f"File is too large ({len(data)} B, limit "
                    f"{MAX_IMPORT_FILE_BYTES} B).",
                    type="negative",
                )
                return

            try:
                result = _parse_uploaded_bytes(
                    source_id, event.file.name, data
                )
            except (OSError, ValueError) as exc:
                ui.notify(f"Parse failed: {exc}", type="negative")
                return

            _store_parse_result(result)
            ui.notify(
                f"Uploaded: {len(result.questions)} questions, "
                f"{len(result.parse_errors)} errors."
            )
            _render_preview_body.refresh(dialog)

        # Narrow the file picker's ``accept=`` to *only* the active
        # parser(s). In preselect mode this means the OS file dialog
        # filters by the chosen format (e.g. ``.pdf`` only) which gives
        # the user immediate feedback that the button is format-specific.
        accept_exts = ",".join(
            ext for s in effective_sources for ext in s.file_extensions
        )
        ui.upload(
            label="Upload file (drag and drop or pick)",
            on_upload=_handle_upload,
            auto_upload=True,
            max_file_size=MAX_IMPORT_FILE_BYTES,
        ).props(f"accept={accept_exts}").classes("w-full q-mb-md")

        ui.separator().classes("q-my-sm")

        _render_preview_body(dialog)

    dialog.on("hide", lambda _evt=None: _clear_pending_import())
    dialog.open()


__all__ = [
    "MAX_IMPORT_FILE_BYTES",
    "PENDING_IMPORT_SELECTION_KEY",
    "PENDING_IMPORT_STORAGE_KEY",
    "PREVIEW_TEXT_MAX_CHARS",
    "_commit_selected_to_open_exam",
    "_parse_uploaded_bytes",
    "_show_bulk_import_dialog",
]
