"""Designer — Question Browser panel.

This is the left-hand panel of the Designer UI. It owns the exam file
selection and the list of questions inside the currently opened exam.

Phase 4 grows this module in small increments:

* **4.1** Open an existing ``.sqlite`` exam file from the server-side data
  directory (implemented here).
* **4.2** Create a new empty exam database.
* **4.3** Render the list of questions for the opened exam.
* **4.4–4.7** Add, delete, reorder and search questions.

The public entry point is :func:`render_designer`, which is wired into the
``/designer`` route by :mod:`posrat.app`. Keeping it a plain render function
(no module-level UI calls) means tests can import the module without booting
a NiceGUI server.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from nicegui import app, events, ui

from posrat.io import export_exam_to_json_file
from posrat.models import Choice, Exam, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep
from posrat.storage import (
    add_question,
    create_exam,
    delete_question,
    get_exam,
    list_questions,
    open_db,
    reorder_questions,
    update_question,
)



#: Environment variable that overrides the default data directory. Useful for
#: running multiple POSRAT instances side-by-side, for pointing the app at a
#: shared directory, or for tests.
DATA_DIR_ENV = "POSRAT_DATA_DIR"

#: Default data directory, relative to the current working directory (i.e.
#: where the user invoked ``python -m posrat``). Keeping it relative makes
#: the simple "clone repo, run, go" flow work out of the box while still
#: letting power users pin an absolute path via ``POSRAT_DATA_DIR``.
DEFAULT_DATA_DIR = Path("data")

#: File extension used for POSRAT exam databases. A leading dot is included
#: so it composes cleanly with :meth:`pathlib.Path.suffix`.
EXAM_FILE_SUFFIX = ".sqlite"

#: Key used inside ``app.storage.user`` to persist the currently opened
#: exam's metadata. We intentionally store a lightweight JSON-serialisable
#: summary (path + exam header) rather than the full :class:`Exam` payload:
#: questions can be re-read from the database on demand, and this keeps the
#: signed storage cookie small.
OPEN_EXAM_STORAGE_KEY = "open_exam"


def resolve_data_dir() -> Path:
    """Return the directory that holds POSRAT exam ``.sqlite`` files.

    Resolution order:

    1. ``POSRAT_DATA_DIR`` environment variable, if set and non-empty. The
       value is taken verbatim and may be absolute or relative to the
       current working directory.
    2. ``DEFAULT_DATA_DIR`` (``./data/``) otherwise.

    The directory (and any missing parents) is created on first call so the
    rest of the Designer UI does not have to worry about a fresh checkout.
    """

    raw = os.environ.get(DATA_DIR_ENV)
    data_dir = Path(raw) if raw else DEFAULT_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def list_exam_files(data_dir: Path) -> list[Path]:
    """Return the sorted list of ``*.sqlite`` exam files in ``data_dir``.

    Only regular files with the ``.sqlite`` suffix are returned; directories
    and other file types are ignored. The result is sorted by filename so the
    UI has a stable, user-friendly order without having to re-sort it.
    If ``data_dir`` does not exist, an empty list is returned — this matches
    the "no exams yet" state instead of raising.

    Phase 10: the cross-cutting ``system.sqlite`` (users / ACL store
    from :mod:`posrat.system`) also lives in the data directory but is
    **not** an exam file; we filter it out here so the Designer /
    Runner pickers don't try to open it via the per-exam migration
    runner (which would overwrite its schema). The filename is
    imported lazily to avoid an import cycle between
    :mod:`posrat.designer.browser` and :mod:`posrat.system`.
    """

    if not data_dir.is_dir():
        return []

    from posrat.system.system_db import SYSTEM_DB_FILENAME

    return sorted(
        (
            p
            for p in data_dir.iterdir()
            if p.is_file()
            and p.suffix == EXAM_FILE_SUFFIX
            and p.name != SYSTEM_DB_FILENAME
        ),
        key=lambda p: p.name,
    )


def open_exam_from_file(path: Path) -> Exam:
    """Open a POSRAT ``.sqlite`` file and return the :class:`Exam` it holds.

    Each POSRAT exam database contains exactly one exam row (the design is
    one-exam-per-file, mirroring the JSON bundle), so we look it up with a
    simple ``LIMIT 1`` query and then delegate to :func:`get_exam` to rebuild
    the full model with its questions, choices and hotspot payload.

    Raises :class:`ValueError` when the database does not contain any exam
    row (empty / corrupted file). Schema migrations are applied transparently
    by :func:`open_db`, so a brand-new empty ``.sqlite`` file is a valid
    argument — it just returns the "no exam" error path.
    """

    db = open_db(path)
    try:
        row = db.execute("SELECT id FROM exams LIMIT 1").fetchone()
        if row is None:
            raise ValueError(f"No exam found in database {path}")
        exam = get_exam(db, row["id"])
        if exam is None:  # pragma: no cover - row existed a line ago
            raise ValueError(f"Exam row {row['id']} disappeared in {path}")
        return exam
    finally:
        db.close()


def create_exam_file(
    data_dir: Path,
    exam_id: str,
    name: str,
    description: str | None = None,
    *,
    default_question_count: int | None = None,
    passing_score: int | None = None,
    time_limit_minutes: int | None = None,
) -> Path:
    """Create a new empty POSRAT exam database and return its path.

    Builds a file path as ``data_dir / f"{exam_id}{EXAM_FILE_SUFFIX}"`` and
    refuses to overwrite existing files (``FileExistsError``). The exam
    itself is created via :func:`create_exam` with an empty ``questions``
    list, mirroring the "empty skeleton ready for editing" flow of the
    Designer. Pydantic validates ``exam_id`` / ``name`` at model
    construction time, so invalid inputs (empty strings, duplicates) fail
    fast before touching the filesystem.

    ``default_question_count``, ``passing_score`` and
    ``time_limit_minutes`` are optional Runner-facing metadata captured
    at creation time (Phase 7A). The Runner reads them later via
    :class:`RunnerExamSummary` to pre-fill the mode dialog (number of
    questions + timer toggle) and to drive the countdown timer +
    pass/fail banner.

    The caller is responsible for ensuring ``data_dir`` exists; in the
    Designer flow this is guaranteed by :func:`resolve_data_dir`.
    """

    path = data_dir / f"{exam_id}{EXAM_FILE_SUFFIX}"
    if path.exists():
        raise FileExistsError(f"Exam file already exists: {path}")

    exam = Exam(
        id=exam_id,
        name=name,
        description=description,
        questions=[],
        default_question_count=default_question_count,
        passing_score=passing_score,
        time_limit_minutes=time_limit_minutes,
    )
    db = open_db(path)

    try:
        create_exam(db, exam)
    finally:
        db.close()
    return path



#: Maximum number of characters of ``Question.text`` rendered inline in the
#: question list. Longer texts are truncated with an ellipsis. Kept small
#: so a dozen questions still fit on-screen without horizontal scrolling;
#: full text lives in the Properties panel (Phase 5).
QUESTION_LIST_TEXT_PREVIEW = 80


def format_question_label(index: int) -> str:
    """Return the user-facing ``Q{n}`` label for a question at ``index``.

    The Designer displays questions as ``Q1``, ``Q2``, …, ``Qn`` in the
    Exam Explorer (matching the Visual CertExam-style 3-panel layout),
    while ``Question.id`` stays a stable UUID-ish string used for JSON
    export, session answers and inter-tab references. Keeping the two
    separate means renumbering on reorder is free — the label is a pure
    function of position, ids never have to move.

    ``index`` is zero-based (``questions[0]`` → ``"Q1"``). A negative
    index raises :class:`ValueError` so accidental off-by-one callers
    fail loudly instead of producing ``"Q0"`` / ``"Q-1"`` strings.
    """

    if index < 0:
        raise ValueError(f"question index must be non-negative, got {index}")
    return f"Q{index + 1}"


#: Maximum CSS width applied to the Designer body so the question list
#: rows do not spread over an ultra-wide viewport. Without this the per-
#: row buttons drift far to the right when a single question text is
#: long, producing an uneven layout (see the screenshot in step 5.2's
#: UX follow-up). The value is in pixels to keep the CSS self-contained
#: — Tailwind utility classes have a baked-in breakpoint that does not
#: always match our target max-width of ~1200px.
DESIGNER_BODY_MAX_WIDTH_PX = 1200

#: Pixel width of the ``Question.text`` preview column inside a question
#: list row. The text label is clipped and ellipsised at this width so
#: every row has its action buttons aligned at the same x position,
#: regardless of the row's text length. Paired with
#: :data:`QUESTION_LIST_TEXT_PREVIEW` (character-level cap) — the
#: character cap keeps the truncation predictable at the string level,
#: the pixel cap keeps the layout predictable at the render level.
QUESTION_LIST_TEXT_WIDTH_PX = 520



def load_questions_from_file(path: Path, exam_id: str) -> list[Question]:
    """Return questions of ``exam_id`` from the ``.sqlite`` at ``path``.

    Thin wrapper around :func:`posrat.storage.list_questions` that owns the
    DB connection lifecycle. Extracted as a pure helper so tests can exercise
    the DAO path without a NiceGUI request context.
    """

    db = open_db(path)
    try:
        return list_questions(db, exam_id)
    finally:
        db.close()


def _prune_stale_open_exam() -> dict | None:
    """Return the opened-exam summary after evicting stale paths.

    The Designer stashes the currently-opened exam path in
    :data:`app.storage.user` so edits and navigation stay sticky across
    refreshes. When an admin deletes the underlying ``.sqlite`` file
    (via Admin → Exams) the stash keeps its path — and the next
    :func:`open_db` call would silently recreate the deleted file as
    an empty skeleton (``sqlite3.connect`` creates missing files by
    default). We defend against that here by checking ``path.is_file``
    and, on a miss, dropping the storage entry before any DB I/O so
    callers render the "no exam open" state instead of a resurrected
    ghost.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    if not path.is_file():
        # Stale reference — clear it so the Designer renders "no exam
        # open" and the user can re-pick from the Explorer. No notify
        # here because this runs on every render; a noisy toast would
        # spam. The empty Designer state is already an obvious signal.
        app.storage.user.pop(OPEN_EXAM_STORAGE_KEY, None)
        return None

    return summary


def load_questions_for_open_exam() -> list[Question]:
    """Return questions of the currently opened exam, freshly from disk.

    Reads the "opened exam" summary from :data:`app.storage.user` (populated
    by :func:`_handle_open_click`) and delegates to
    :func:`load_questions_from_file`. Returns an empty list when no exam is
    open — callers can use this as the "nothing to render" signal without
    distinguishing "missing summary" from "empty exam".

    Silently evicts stale summaries that point at a deleted ``.sqlite``
    file via :func:`_prune_stale_open_exam`, so a mid-session
    Admin-delete cannot resurrect the file through ``sqlite3.connect``.
    """

    summary = _prune_stale_open_exam()
    if not summary:
        return []

    return load_questions_from_file(
        Path(str(summary.get("path"))), str(summary.get("id"))
    )



#: Prefix used for auto-generated question ids (``q-<hex>``). Kept short so
#: the id does not dominate the question-list row; the hex suffix provides
#: enough entropy to avoid collisions within a single exam.
BLANK_QUESTION_ID_PREFIX = "q-"

#: Default placeholder text seeded into newly-added blank questions. Users
#: are expected to replace it via the Properties panel (Phase 5) — the
#: string just needs to satisfy ``Question.text`` ``min_length=1``.
BLANK_QUESTION_TEXT = "New question"

#: Default number of choices seeded into a fresh ``single_choice`` question.
#: Matches the Visual CertExam convention (A/B/C/D) and gives the user a
#: ready-made skeleton to fill in rather than requiring them to click
#: "Add choice" twice after every new row. The first choice is marked
#: correct so the ``single_choice`` Pydantic invariant holds out of the
#: box. When the user morphs the question to another type the count is
#: adjusted by :func:`_morph_question_to_type`.
DEFAULT_SINGLE_CHOICE_COUNT = 4

#: Default number of choices seeded into a fresh ``multi_choice`` question.
#: Multi-choice AWS-style questions typically offer 5–6 options, so 6 hits
#: the common case without forcing the user to prune. Only the first
#: choice is marked correct (multi_choice needs ≥1, not exactly 1, so a
#: lone correct keeps the invariant and leaves room for the user to toggle
#: more). Applied by :func:`_generate_blank_question` and
#: :func:`_morph_question_to_type` when growing from a smaller choice set.
DEFAULT_MULTI_CHOICE_COUNT = 6


def _seed_default_choices(question_id: str, count: int) -> list[Choice]:
    """Return ``count`` default :class:`Choice` rows anchored to ``question_id``.

    Generates labels ``A``, ``B``, …, ``chr(ord('A') + count - 1)`` so the
    user sees a familiar letter sequence. Only the first choice is marked
    correct — that satisfies both single_choice (exactly 1 correct) and
    multi_choice (≥1 correct) invariants, so the returned list can be
    dropped into either question type without further tweaking. Raises
    :class:`ValueError` when ``count < 2`` because Pydantic's choice-based
    invariants require at least 2 options.
    """

    if count < 2:
        raise ValueError(
            f"choice count must be at least 2, got {count}"
        )
    return [
        Choice(
            id=f"{question_id}-{chr(ord('a') + idx)}",
            text=chr(ord("A") + idx),
            is_correct=(idx == 0),
        )
        for idx in range(count)
    ]


def _generate_blank_question(existing_ids: set[str]) -> Question:
    """Return a minimal ``single_choice`` :class:`Question` with a fresh id.

    The id is drawn from ``uuid.uuid4().hex[:8]`` and re-rolled if it clashes
    with an id already present in ``existing_ids``.
    :data:`DEFAULT_SINGLE_CHOICE_COUNT` (=4) choices A–D are seeded with the
    first marked correct — matches the Visual CertExam "A/B/C/D + radio"
    default and keeps the Pydantic single_choice invariant (exactly 1
    correct) satisfied out of the box.
    """

    while True:
        question_id = f"{BLANK_QUESTION_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        if question_id not in existing_ids:
            break

    return Question(
        id=question_id,
        type="single_choice",
        text=BLANK_QUESTION_TEXT,
        choices=_seed_default_choices(question_id, DEFAULT_SINGLE_CHOICE_COUNT),
    )



def add_blank_question_to_file(path: Path, exam_id: str) -> str:
    """Append a blank ``single_choice`` question to ``exam_id`` and return its id.

    Opens the database at ``path``, enumerates existing question ids to pick
    a unique auto-generated id, constructs a minimal valid
    :class:`Question` and appends it via :func:`posrat.storage.add_question`.
    The DB connection is always closed, even on error. Extracted as a pure
    helper so tests can exercise the DAO flow without a NiceGUI request
    context; the UI-facing wrapper is
    :func:`add_blank_question_to_open_exam`.
    """

    db = open_db(path)
    try:
        existing_ids = {q.id for q in list_questions(db, exam_id)}
        question = _generate_blank_question(existing_ids)
        add_question(db, exam_id, question)
        return question.id
    finally:
        db.close()


def add_blank_question_to_open_exam() -> str | None:
    """Append a blank question to the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`add_blank_question_to_file`. Returns the new
    question id on success, or ``None`` when no exam is open — mirroring
    the :func:`load_questions_for_open_exam` contract so the UI can notify
    the user without distinguishing "missing summary" from other no-ops.
    Also bumps the cached ``question_count`` in the storage summary so the
    header metadata stays in sync without re-opening the exam.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    new_id = add_blank_question_to_file(path, exam_id)

    summary["question_count"] = int(summary.get("question_count", 0)) + 1
    app.storage.user[OPEN_EXAM_STORAGE_KEY] = summary
    return new_id


def update_question_text_in_file(
    path: Path, question_id: str, new_text: str
) -> bool:
    """Overwrite ``question_id``'s ``text`` in the exam DB at ``path``.

    Mirrors the :class:`posrat.models.Question` ``text`` contract: the
    model declares ``text: str = Field(..., min_length=1)``, so empty
    values are refused fast with :class:`ValueError` before the DB is
    touched. A question whose id does not exist is treated as an
    idempotent no-op — the helper returns ``False`` rather than raising,
    matching :func:`delete_question_from_file`. ``True`` means the row
    was updated.

    Only the ``text`` column is rewritten; ``type``, ``choices``, the
    hotspot payload, ``explanation``, ``image_path`` and ``order_index``
    are preserved byte-for-byte. The single ``UPDATE`` runs inside a
    ``with db:`` transaction so any unexpected failure rolls the change
    back cleanly. Extracted as a pure helper so tests can exercise the
    DAO path without a NiceGUI request context; the UI-facing wrapper
    is :func:`update_question_text_in_open_exam`.
    """

    if not new_text:
        raise ValueError("question text must not be empty")

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        with db:
            db.execute(
                "UPDATE questions SET text = ? WHERE id = ?",
                (new_text, question_id),
            )
        return True
    finally:
        db.close()


def update_question_text_in_open_exam(
    question_id: str, new_text: str
) -> bool | None:
    """Overwrite ``question_id``'s text in the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_question_text_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open, matching the other
      ``*_in_open_exam`` / ``*_from_open_exam`` wrappers so the UI can
      distinguish "nothing open" from DAO-level outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return update_question_text_in_file(path, question_id, new_text)


def update_question_explanation_in_file(
    path: Path, question_id: str, new_explanation: str | None
) -> bool:
    """Overwrite ``question_id``'s ``explanation`` in the exam DB at ``path``.

    Unlike :func:`update_question_text_in_file`, ``explanation`` is
    declared ``Optional[str]`` on :class:`posrat.models.Question` with no
    ``min_length`` constraint, so an empty explanation is a legitimate
    outcome — the caller maps "user cleared the field" to ``None`` (SQL
    ``NULL``) before delegating here. The helper therefore accepts
    ``str | None`` verbatim without extra validation.

    Row lookup mirrors :func:`update_question_text_in_file`: unknown id
    returns ``False`` (idempotent no-op), matching the
    :func:`delete_question_from_file` convention. ``True`` means the row
    was updated. Only the ``explanation`` column is rewritten; ``type``,
    ``text``, ``choices``, the hotspot payload, ``image_path`` and
    ``order_index`` are preserved byte-for-byte.
    """

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        with db:
            db.execute(
                "UPDATE questions SET explanation = ? WHERE id = ?",
                (new_explanation, question_id),
            )
        return True
    finally:
        db.close()


def update_question_explanation_in_open_exam(
    question_id: str, new_explanation: str | None
) -> bool | None:
    """Overwrite ``question_id``'s explanation in the opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_question_explanation_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open, matching the other
      ``*_in_open_exam`` wrappers so the UI can distinguish "nothing
      open" from DAO-level outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return update_question_explanation_in_file(path, question_id, new_explanation)


def update_question_complexity_in_file(
    path: Path, question_id: str, new_complexity: int | None
) -> bool:
    """Overwrite ``question_id``'s ``complexity`` in the exam DB at ``path``.

    ``new_complexity`` must be ``None`` (clears the rating) or an int in
    the ``MIN_COMPLEXITY..MAX_COMPLEXITY`` range enforced by the
    :class:`posrat.models.Question` model. Out-of-range ints raise
    :class:`ValueError` from Pydantic *before* the SQL statement runs,
    so a malformed call cannot half-apply.

    Row lookup mirrors :func:`update_question_explanation_in_file`: an
    unknown id returns ``False`` (idempotent no-op); ``True`` means the
    row was updated. Every other column (text, explanation, choices,
    hotspot payload, image_path, order_index, section) is preserved
    byte-for-byte.
    """

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        # Model-level range check — we build a throwaway Question only
        # so Pydantic raises ValueError before the UPDATE lands. The
        # dummy single_choice shape satisfies the choices invariant
        # without touching the live row's real choices.
        if new_complexity is not None:
            Question(
                id="__complexity_probe__",
                type="single_choice",
                text="probe",
                complexity=new_complexity,
                choices=[
                    Choice(
                        id="__probe_a__", text="A", is_correct=True
                    ),
                    Choice(
                        id="__probe_b__", text="B", is_correct=False
                    ),
                ],
            )
        with db:
            db.execute(
                "UPDATE questions SET complexity = ? WHERE id = ?",
                (new_complexity, question_id),
            )
        return True
    finally:
        db.close()


def update_question_complexity_in_open_exam(
    question_id: str, new_complexity: int | None
) -> bool | None:
    """Overwrite ``question_id``'s complexity in the opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_question_complexity_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return update_question_complexity_in_file(
        path, question_id, new_complexity
    )


def update_question_section_in_file(
    path: Path, question_id: str, new_section: str | None
) -> bool:
    """Overwrite ``question_id``'s ``section`` in the exam DB at ``path``.

    ``new_section`` may be ``None`` (clears the tag), an empty string
    (normalised to ``None`` by the Pydantic validator so both paths
    converge on SQL ``NULL``) or any free-text tag. Whitespace around
    a non-empty value is trimmed — same contract as the model, applied
    here so callers bypassing the model (e.g. REST / batch tools) get
    the same normalisation.

    Row lookup mirrors :func:`update_question_explanation_in_file`: an
    unknown id returns ``False`` (idempotent no-op); ``True`` means the
    row was updated.
    """

    if isinstance(new_section, str):
        trimmed = new_section.strip()
        new_section = trimmed or None

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        with db:
            db.execute(
                "UPDATE questions SET section = ? WHERE id = ?",
                (new_section, question_id),
            )
        return True
    finally:
        db.close()


def update_question_section_in_open_exam(
    question_id: str, new_section: str | None
) -> bool | None:
    """Overwrite ``question_id``'s section in the opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_question_section_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return update_question_section_in_file(path, question_id, new_section)


# --------------------------------------------------------------------------- #
# Phase 7A.5 — Exam-level Runner metadata update helpers                      #
# --------------------------------------------------------------------------- #
#
# Each editable metadata field gets its own targeted ``UPDATE exams SET
# <col> = ?`` helper so a Properties panel blur-save for one field never
# risks clobbering the others. The pattern mirrors the per-field
# question-level helpers above (``update_question_text_in_file`` and
# friends): construct a throwaway Pydantic ``Exam`` to run model-level
# validation *before* the SQL statement runs, so malformed inputs fail
# fast without leaving the DB half-updated.


def _load_exam_row_for_metadata_update(
    db: sqlite3.Connection, exam_id: str
) -> sqlite3.Row | None:
    """Return the single ``exams`` row used to seed the validation probe.

    Reads every metadata column so the throwaway ``Exam`` constructed
    inside each update helper can replay the full post-update state
    through Pydantic validators (including the ``passing_score ≤
    target_score`` cross-field check). ``None`` when the exam id is
    unknown.
    """

    return db.execute(
        "SELECT id, name, description, default_question_count,"
        " time_limit_minutes, passing_score, target_score"
        " FROM exams WHERE id = ?",
        (exam_id,),
    ).fetchone()


def _probe_exam_update(
    row: sqlite3.Row,
    *,
    default_question_count: int | None = ...,  # type: ignore[assignment]
    time_limit_minutes: int | None = ...,  # type: ignore[assignment]
    passing_score: int | None = ...,  # type: ignore[assignment]
    target_score: int | None = ...,  # type: ignore[assignment]
) -> None:
    """Run a Pydantic-level dry-run of the post-update exam state.

    ``Ellipsis`` sentinels mean "keep the stored value" for that field;
    any other value (including ``None``) replaces the stored value in
    the probe. We construct an :class:`Exam` with an empty question
    list — the metadata validators don't care about questions, and
    skipping the heavy payload keeps the helper cheap even for huge
    exams.

    Raises whatever :class:`~pydantic.ValidationError` (subclass of
    :class:`ValueError`) the model raises; callers let it bubble up so
    the caller sees the exact field + reason.
    """

    def _pick(new_value: object, stored: object) -> object:
        return stored if new_value is ... else new_value

    Exam(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        default_question_count=_pick(
            default_question_count, row["default_question_count"]
        ),
        time_limit_minutes=_pick(
            time_limit_minutes, row["time_limit_minutes"]
        ),
        passing_score=_pick(passing_score, row["passing_score"]),
        target_score=_pick(target_score, row["target_score"]),
        questions=[],
    )


def update_exam_default_question_count_in_file(
    path: Path, exam_id: str, new_count: int | None
) -> bool:
    """Overwrite ``exam_id``'s ``default_question_count`` in the DB at ``path``.

    ``new_count`` must be ``None`` (clears the default) or an int
    ``>= 1``. Out-of-range values are rejected by Pydantic **before**
    the UPDATE runs, so a malformed call cannot half-apply.

    Returns ``True`` when the row was updated, ``False`` when the exam
    id is unknown (idempotent no-op). Other metadata columns (time
    limit, scoring) stay byte-for-byte.
    """

    db = open_db(path)
    try:
        row = _load_exam_row_for_metadata_update(db, exam_id)
        if row is None:
            return False
        _probe_exam_update(row, default_question_count=new_count)
        with db:
            db.execute(
                "UPDATE exams SET default_question_count = ?"
                " WHERE id = ?",
                (new_count, exam_id),
            )
        return True
    finally:
        db.close()


def update_exam_default_question_count_in_open_exam(
    new_count: int | None,
) -> bool | None:
    """Overwrite the opened exam's default question count, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_exam_default_question_count_in_file`.
    Returns ``None`` when no exam is open, ``False`` when the stored
    summary points at a stale id, ``True`` on success.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None
    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    return update_exam_default_question_count_in_file(
        path, exam_id, new_count
    )


def update_exam_time_limit_minutes_in_file(
    path: Path, exam_id: str, new_minutes: int | None
) -> bool:
    """Overwrite ``exam_id``'s ``time_limit_minutes`` in the DB at ``path``.

    ``new_minutes`` must be ``None`` (no time limit) or an int ``>= 1``.
    Same fail-fast validation + idempotent no-op semantics as
    :func:`update_exam_default_question_count_in_file`.
    """

    db = open_db(path)
    try:
        row = _load_exam_row_for_metadata_update(db, exam_id)
        if row is None:
            return False
        _probe_exam_update(row, time_limit_minutes=new_minutes)
        with db:
            db.execute(
                "UPDATE exams SET time_limit_minutes = ? WHERE id = ?",
                (new_minutes, exam_id),
            )
        return True
    finally:
        db.close()


def update_exam_time_limit_minutes_in_open_exam(
    new_minutes: int | None,
) -> bool | None:
    """Overwrite the opened exam's time limit, if any."""

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None
    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    return update_exam_time_limit_minutes_in_file(
        path, exam_id, new_minutes
    )


def update_exam_passing_score_in_file(
    path: Path, exam_id: str, new_passing_score: int | None
) -> bool:
    """Overwrite ``exam_id``'s ``passing_score`` in the DB at ``path``.

    ``new_passing_score`` must be ``None`` or an int ``>= 0``. The
    probe also re-runs the ``passing_score <= target_score`` cross-field
    validator, so setting a passing score above an already-stored
    target score is rejected **before** the UPDATE runs.
    """

    db = open_db(path)
    try:
        row = _load_exam_row_for_metadata_update(db, exam_id)
        if row is None:
            return False
        _probe_exam_update(row, passing_score=new_passing_score)
        with db:
            db.execute(
                "UPDATE exams SET passing_score = ? WHERE id = ?",
                (new_passing_score, exam_id),
            )
        return True
    finally:
        db.close()


def update_exam_passing_score_in_open_exam(
    new_passing_score: int | None,
) -> bool | None:
    """Overwrite the opened exam's passing score, if any."""

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None
    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    return update_exam_passing_score_in_file(
        path, exam_id, new_passing_score
    )


def update_exam_target_score_in_file(
    path: Path, exam_id: str, new_target_score: int | None
) -> bool:
    """Overwrite ``exam_id``'s ``target_score`` in the DB at ``path``.

    ``new_target_score`` must be ``None`` or an int ``>= 1``. The
    probe re-runs the cross-field check, so lowering ``target_score``
    below an already-stored ``passing_score`` is rejected **before**
    the UPDATE runs.
    """

    db = open_db(path)
    try:
        row = _load_exam_row_for_metadata_update(db, exam_id)
        if row is None:
            return False
        _probe_exam_update(row, target_score=new_target_score)
        with db:
            db.execute(
                "UPDATE exams SET target_score = ? WHERE id = ?",
                (new_target_score, exam_id),
            )
        return True
    finally:
        db.close()


def update_exam_target_score_in_open_exam(
    new_target_score: int | None,
) -> bool | None:
    """Overwrite the opened exam's target score, if any."""

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None
    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    return update_exam_target_score_in_file(
        path, exam_id, new_target_score
    )


def update_question_allow_shuffle_in_file(

    path: Path, question_id: str, allow_shuffle: bool
) -> bool:
    """Overwrite ``question_id``'s ``allow_shuffle`` flag in the DB at ``path``.

    The column is ``NOT NULL`` with a SQLite default of 0, so we coerce
    the Python ``bool`` to ``0`` / ``1`` explicitly. An unknown id is
    an idempotent no-op (``False``); a successful write returns
    ``True``. Other columns (text, explanation, choices, hotspot
    payload, image_path, complexity, section, order_index) stay
    untouched byte-for-byte.
    """

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        with db:
            db.execute(
                "UPDATE questions SET allow_shuffle = ? WHERE id = ?",
                (1 if allow_shuffle else 0, question_id),
            )
        return True
    finally:
        db.close()


def update_question_allow_shuffle_in_open_exam(
    question_id: str, allow_shuffle: bool
) -> bool | None:
    """Overwrite ``question_id``'s allow_shuffle flag in the opened exam.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`update_question_allow_shuffle_in_file`. Returns
    the same ``True`` / ``False`` / ``None`` matrix as the sibling
    ``*_in_open_exam`` wrappers.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return update_question_allow_shuffle_in_file(
        path, question_id, allow_shuffle
    )


#: Ordered tuple of question types the Designer lets the user pick from.


#: Kept as a module-level constant so UI dropdowns, helper validators and
#: tests all share a single vocabulary. The order matches the
#: :data:`posrat.models.question.QuestionType` ``Literal`` declaration and
#: is also the display order in the "Change type" dropdown.
ALLOWED_QUESTION_TYPES: tuple[str, ...] = (
    "single_choice",
    "multi_choice",
    "hotspot",
)


def _morph_question_to_type(question: Question, new_type: str) -> Question:
    """Return a copy of ``question`` coerced to ``new_type``.

    Morphing rules, all of them crafted to produce a **validly-shaped**
    :class:`Question` that :class:`posrat.models.Question`'s Pydantic
    validator accepts without further tweaking:

    * ``single_choice`` ↔ ``multi_choice``: choices are preserved verbatim
      when going multi → single we keep the first ``is_correct=True`` and
      clear the rest (single requires *exactly one* correct). When going
      single → multi the list stays as-is (single ≥1 correct is already
      multi-compatible). If the source has fewer than two choices (can
      happen when coming from a ``hotspot``) two default options are
      seeded.
    * Any type → ``hotspot``: ``choices`` are discarded, ``hotspot``
      payload is seeded with two options + one step referencing the
      first option. The Designer's 5.5 hotspot editor will let the user
      flesh it out later.
    * ``hotspot`` → choice-based: ``hotspot`` is cleared and two default
      choices are seeded (first correct).

    Id, text, ``explanation`` and ``image_path`` are always preserved;
    only ``type`` / ``choices`` / ``hotspot`` shift to satisfy the new
    type invariant. Returning a fresh :class:`Question` (rather than
    mutating in place) keeps the helper safe for tests that want to
    compare "before" and "after" snapshots.
    """

    if new_type not in ALLOWED_QUESTION_TYPES:
        raise ValueError(
            f"unknown question type: {new_type!r} "
            f"(allowed: {ALLOWED_QUESTION_TYPES!r})"
        )

    if new_type == question.type:
        # No-op morph: still return a fresh copy so the caller can treat
        # the result as immutable and identity-compare against the input
        # where helpful.
        return question.model_copy(deep=True)

    base_id = question.id

    if new_type == "hotspot":

        # Seed minimal valid hotspot payload: two options + one step.
        options = [
            HotspotOption(id=f"{base_id}-opt-a", text="A"),
            HotspotOption(id=f"{base_id}-opt-b", text="B"),
        ]
        steps = [
            HotspotStep(
                id=f"{base_id}-step-1",
                prompt="Step 1",
                correct_option_id=options[0].id,
            ),
        ]
        return Question(
            id=question.id,
            type="hotspot",
            text=question.text,
            explanation=question.explanation,
            image_path=question.image_path,
            choices=[],
            hotspot=Hotspot(options=options, steps=steps),
        )

    # ``new_type`` is single_choice or multi_choice from here on.
    target_default_count = (
        DEFAULT_SINGLE_CHOICE_COUNT
        if new_type == "single_choice"
        else DEFAULT_MULTI_CHOICE_COUNT
    )

    if question.type == "hotspot":
        # Hotspot → choice: drop the hotspot payload, seed the target
        # type's default choice set (4 for single, 6 for multi) because
        # the source has ``choices=[]``.
        new_choices = _seed_default_choices(base_id, target_default_count)
    else:
        # choice ↔ choice: reuse existing list but grow it to the target
        # type's default when coming up short (single→multi typically
        # wants 6 rows, not the 4 it started with). Also patch is_correct
        # so single_choice's exactly-one-correct invariant holds.
        source = list(question.choices)
        if len(source) < target_default_count:
            # Preserve existing rows verbatim, then top up with fresh
            # default rows so ids stay unique. Fresh rows are all
            # ``is_correct=False`` so they never break the exactly-one
            # or ≥1 correct invariants when merged with existing data.
            existing_ids = {c.id for c in source}
            extras: list[Choice] = []
            for idx in range(len(source), target_default_count):
                candidate_id = f"{base_id}-{chr(ord('a') + idx)}"
                # Protect against clash with a manually-named pre-existing
                # choice — extremely rare in practice but cheap to guard.
                suffix = 0
                while candidate_id in existing_ids:
                    suffix += 1
                    candidate_id = f"{base_id}-extra{suffix}"
                existing_ids.add(candidate_id)
                extras.append(
                    Choice(
                        id=candidate_id,
                        text=chr(ord("A") + idx),
                        is_correct=False,
                    )
                )
            source = source + extras

        new_choices = [c.model_copy(deep=True) for c in source]
        if new_type == "single_choice":
            correct_seen = False
            for idx, choice in enumerate(new_choices):
                if choice.is_correct and not correct_seen:
                    correct_seen = True
                    continue
                if choice.is_correct and correct_seen:
                    new_choices[idx] = choice.model_copy(update={"is_correct": False})
            if not correct_seen:
                # multi → single on a list with no correct (shouldn't
                # normally happen because multi requires ≥1 correct,
                # but defensive): mark the first choice correct.
                new_choices[0] = new_choices[0].model_copy(
                    update={"is_correct": True}
                )


    return Question(
        id=question.id,
        type=new_type,  # type: ignore[arg-type]
        text=question.text,
        explanation=question.explanation,
        image_path=question.image_path,
        choices=new_choices,
        hotspot=None,
    )


def change_question_type_in_file(
    path: Path, question_id: str, new_type: str
) -> bool:
    """Change ``question_id``'s ``type`` in the exam DB at ``path``.

    Validates ``new_type`` against :data:`ALLOWED_QUESTION_TYPES` before
    opening the DB (fail-fast: malformed call cannot even touch disk).
    Loads the existing question via :func:`list_questions`, runs it
    through :func:`_morph_question_to_type` to get a validly-shaped
    replacement, then persists it via :func:`posrat.storage.update_question`.
    The whole rewrite runs inside a single ``with db:`` transaction
    (``update_question`` wraps the delete-all + re-insert in one), so a
    validation failure in ``_morph_question_to_type`` leaves the DB
    untouched.

    Returns ``True`` when the row was updated, ``False`` when
    ``question_id`` does not exist (idempotent no-op, matching
    :func:`update_question_text_in_file`). A same-type call is treated
    as a no-op *round-trip* that still returns ``True`` — the caller can
    detect "nothing changed" by comparing types before calling.

    Raises :class:`ValueError` for an unknown ``new_type`` or for a
    question we cannot find in any exam of this DB (we look up the
    owning ``exam_id`` via the questions table to avoid a parameter).
    """

    if new_type not in ALLOWED_QUESTION_TYPES:
        raise ValueError(
            f"unknown question type: {new_type!r} "
            f"(allowed: {ALLOWED_QUESTION_TYPES!r})"
        )

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT exam_id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        exam_id = row["exam_id"]

        current = [q for q in list_questions(db, exam_id) if q.id == question_id]
        if not current:  # pragma: no cover - defensive, row existed a line ago
            return False
        morphed = _morph_question_to_type(current[0], new_type)
        update_question(db, morphed)
        return True
    finally:
        db.close()


def change_question_type_in_open_exam(
    question_id: str, new_type: str
) -> bool | None:
    """Change ``question_id``'s type in the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`change_question_type_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open, matching the other
      ``*_in_open_exam`` wrappers so the UI can distinguish "nothing
      open" from DAO-level outcomes. ``question_count`` stays in sync
      because a type change does not add or remove rows.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return change_question_type_in_file(path, question_id, new_type)


def replace_question_choices_in_file(
    path: Path, question_id: str, new_choices: list[Choice]
) -> bool:
    """Replace the whole ``choices`` list of ``question_id``.

    Pure helper that loads the existing question, substitutes its
    ``choices`` for ``new_choices`` and re-persists through
    :func:`posrat.storage.update_question`. All :class:`Question`
    type-specific invariants (single: exactly 1 correct; multi: ≥1
    correct; both: ≥2 choices) are enforced by Pydantic when the
    replacement Question is constructed — an invalid ``new_choices``
    list therefore raises :class:`ValueError` *before* the DB is
    touched, so a malformed call cannot half-apply.

    Returns ``True`` when the row was updated, ``False`` when
    ``question_id`` does not exist (idempotent no-op). Only works for
    choice-based questions (``single_choice`` / ``multi_choice``);
    calling this on a hotspot row raises :class:`ValueError` via the
    Pydantic ``choices`` constraint.
    """

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT exam_id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        exam_id = row["exam_id"]

        current = next(
            (q for q in list_questions(db, exam_id) if q.id == question_id),
            None,
        )
        if current is None:  # pragma: no cover - defensive
            return False

        replacement = Question(
            id=current.id,
            type=current.type,
            text=current.text,
            explanation=current.explanation,
            image_path=current.image_path,
            choices=new_choices,
            hotspot=current.hotspot,
        )
        update_question(db, replacement)
        return True
    finally:
        db.close()


def replace_question_choices_in_open_exam(
    question_id: str, new_choices: list[Choice]
) -> bool | None:
    """Replace ``question_id``'s choices in the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`replace_question_choices_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open, matching the other
      ``*_in_open_exam`` wrappers so the UI can distinguish "nothing
      open" from DAO-level outcomes. ``question_count`` stays in sync
      because choice edits do not add or remove rows.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return replace_question_choices_in_file(path, question_id, new_choices)


def replace_question_hotspot_in_file(
    path: Path, question_id: str, new_hotspot: Hotspot
) -> bool:
    """Replace the whole ``hotspot`` payload of ``question_id``.

    Pure helper that loads the existing question, substitutes its
    ``hotspot`` for ``new_hotspot`` and re-persists through
    :func:`posrat.storage.update_question`. All :class:`Hotspot`
    invariants (≥1 option, ≥1 step, unique option ids, unique step ids,
    every ``step.correct_option_id`` references a real option) are
    enforced by Pydantic when ``new_hotspot`` is constructed — so
    callers that build a valid :class:`Hotspot` can rely on the
    delegated save succeeding. Question-level invariants
    (``type == "hotspot"`` implies ``choices == []``) are enforced by
    :class:`posrat.models.Question` when the replacement Question is
    constructed here.

    Returns ``True`` when the row was updated, ``False`` when
    ``question_id`` does not exist (idempotent no-op). Only works for
    ``hotspot`` questions; calling this on a choice-based row raises
    :class:`ValueError` via the ``Question`` cross-validator.
    """

    db = open_db(path)
    try:
        row = db.execute(
            "SELECT exam_id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        exam_id = row["exam_id"]

        current = next(
            (q for q in list_questions(db, exam_id) if q.id == question_id),
            None,
        )
        if current is None:  # pragma: no cover - defensive
            return False

        replacement = Question(
            id=current.id,
            type=current.type,
            text=current.text,
            explanation=current.explanation,
            image_path=current.image_path,
            choices=current.choices,
            hotspot=new_hotspot,
        )
        update_question(db, replacement)
        return True
    finally:
        db.close()


#: Subdirectory under the data directory that holds image attachments. One
#: subfolder per exam id keeps attachments for different ``.sqlite`` files
#: cleanly separated so copying / renaming one exam does not disturb
#: another. The whole thing lives server-side next to ``data/*.sqlite``
#: so the JSON export bundle (Phase 8.5) can later zip images + DB
#: together.
ASSETS_DIRNAME = "assets"

#: Maximum size of an uploaded image, in bytes. Kept deliberately modest
#: (5 MB) because the Runner has to re-serve these from disk on every
#: question view; users who need larger diagrams can pre-compress before
#: upload. Enforced both by :func:`ui.upload(max_file_size=...)` and by
#: the pure helper so bypassing the UI cannot stuff a giant blob into
#: the assets directory.
MAX_IMAGE_SIZE_BYTES = 5_000_000

#: Whitelist of image suffixes we accept. Anything else is refused by
#: :func:`_sanitize_image_suffix`; the filename is regenerated from a
#: fresh UUID on disk so the original basename (which may contain path
#: separators or shell metacharacters) never ends up on the filesystem.
_ALLOWED_IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
)


def resolve_assets_dir(data_dir: Path) -> Path:
    """Return the assets subdirectory of ``data_dir``, creating it if missing.

    All image attachments live at ``data_dir / ASSETS_DIRNAME / <exam_id> /
    <filename>``. The top-level ``assets`` dir is created eagerly so the
    Designer never has to branch on "does the folder exist yet"; per-exam
    subdirectories are created on first attach inside
    :func:`attach_image_to_question_in_file`.
    """

    assets = data_dir / ASSETS_DIRNAME
    assets.mkdir(parents=True, exist_ok=True)
    return assets


def resolve_question_image_path(
    data_dir: Path, image_path: str | None
) -> Path | None:
    """Return the absolute filesystem path of ``image_path``, or ``None``.

    :class:`posrat.models.Question` stores ``image_path`` as a string
    relative to the assets root (``<exam_id>/<filename>``). The Runner
    and the Designer preview both need an absolute path to hand to
    :func:`ui.image`, so this helper joins it against
    :func:`resolve_assets_dir`. A question without an attachment
    (``image_path=None``) returns ``None`` verbatim so callers can
    branch once.
    """

    if not image_path:
        return None
    return resolve_assets_dir(data_dir) / image_path


def _sanitize_image_suffix(filename: str) -> str:
    """Return a safe, lower-case file suffix (with the leading dot).

    Raises :class:`ValueError` when the extension is missing or not in
    :data:`_ALLOWED_IMAGE_SUFFIXES`. We re-generate the base name from a
    UUID inside :func:`attach_image_to_question_in_file`, but the
    suffix is preserved so ``ui.image`` can infer the correct MIME type
    and so the user still recognises PNG / JPG on disk.
    """

    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(
            f"unsupported image suffix: {suffix!r} "
            f"(allowed: {sorted(_ALLOWED_IMAGE_SUFFIXES)!r})"
        )
    return suffix


def attach_image_to_question_in_file(
    db_path: Path,
    data_dir: Path,
    question_id: str,
    image_bytes: bytes,
    original_filename: str,
) -> bool:
    """Persist ``image_bytes`` and attach it to ``question_id``.

    Writes the bytes to
    ``resolve_assets_dir(data_dir) / <exam_id> / <uuid><safe_suffix>`` and
    updates ``questions.image_path`` to the exam-relative string
    ``<exam_id>/<uuid><safe_suffix>`` (matching the JSON bundle
    convention so export / import round-trips cleanly). Returns
    ``True`` on success, ``False`` when ``question_id`` is unknown —
    the unknown-id path short-circuits **before** anything is written
    to disk, so a malformed call cannot leave orphaned files behind.

    Raises :class:`ValueError` for images above
    :data:`MAX_IMAGE_SIZE_BYTES` or with an unsupported extension —
    both happen before any disk write. Caller (the UI upload handler)
    is expected to surface these as user-facing warnings.
    """

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError(
            f"image exceeds max size of {MAX_IMAGE_SIZE_BYTES} bytes "
            f"(got {len(image_bytes)})"
        )
    safe_suffix = _sanitize_image_suffix(original_filename)

    db = open_db(db_path)
    try:
        row = db.execute(
            "SELECT exam_id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        exam_id = row["exam_id"]

        # Build the on-disk target. Regenerating the base name from a UUID
        # lets us side-step all filesystem / shell escaping concerns the
        # uploaded filename might otherwise bring (``../``, null bytes,
        # Unicode lookalikes, …).
        new_basename = f"{uuid.uuid4().hex}{safe_suffix}"
        target_dir = resolve_assets_dir(data_dir) / exam_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / new_basename
        target.write_bytes(image_bytes)

        relative = f"{exam_id}/{new_basename}"
        with db:
            db.execute(
                "UPDATE questions SET image_path = ? WHERE id = ?",
                (relative, question_id),
            )
        return True
    finally:
        db.close()


def attach_image_to_question_in_open_exam(
    question_id: str, image_bytes: bytes, original_filename: str
) -> bool | None:
    """Attach an image to ``question_id`` in the currently opened exam.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`attach_image_to_question_in_file` with the
    resolved data dir. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return attach_image_to_question_in_file(
        Path(str(summary.get("path"))),
        resolve_data_dir(),
        question_id,
        image_bytes,
        original_filename,
    )


def clear_question_image_in_file(
    db_path: Path, question_id: str
) -> bool:
    """Clear ``question_id``'s ``image_path`` (sets it to SQL ``NULL``).

    The underlying file on disk is intentionally **not** deleted —
    stale asset garbage-collection is a Phase 9 polish task. Returns
    ``True`` when the row was updated, ``False`` when ``question_id``
    does not exist (idempotent no-op, matching the other
    ``*_in_file`` helpers).
    """

    db = open_db(db_path)
    try:
        row = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return False
        with db:
            db.execute(
                "UPDATE questions SET image_path = NULL WHERE id = ?",
                (question_id,),
            )
        return True
    finally:
        db.close()


def clear_question_image_in_open_exam(question_id: str) -> bool | None:
    """Clear the attached image of ``question_id`` in the opened exam.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`clear_question_image_in_file`. Returns the same
    ``True`` / ``False`` / ``None`` matrix as the other
    ``*_in_open_exam`` wrappers.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return clear_question_image_in_file(
        Path(str(summary.get("path"))), question_id
    )


def upload_asset_to_exam(
    data_dir: Path,
    exam_id: str,
    image_bytes: bytes,
    original_filename: str,
) -> str:
    """Persist ``image_bytes`` into the exam-wide asset pool and return its relative path.

    Unlike :func:`attach_image_to_question_in_file` this helper does
    **not** write anything to the DB — the bytes simply land in
    ``<data_dir>/assets/<exam_id>/<uuid><suffix>`` and the caller gets
    the exam-relative string back (``<exam_id>/<uuid><suffix>``) to
    weave into question/choice markdown manually. That way the same
    image can be referenced by multiple questions / choices, which is
    what ``Question.image_path`` (one image per question) cannot do.

    Reuses the same safety invariants as the per-question attach helper:

    * Bytes above :data:`MAX_IMAGE_SIZE_BYTES` raise :class:`ValueError`
      before any disk write.
    * Suffix is whitelisted via :func:`_sanitize_image_suffix`; the
      base name is regenerated from a UUID so path-traversal shenanigans
      from the user-supplied filename cannot escape the exam subdir.

    No DB round-trip means no "unknown exam id" path — the caller is
    responsible for passing a valid ``exam_id``; an invalid one just
    creates a stray subfolder under ``assets/``.
    """

    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError(
            f"image exceeds max size of {MAX_IMAGE_SIZE_BYTES} bytes "
            f"(got {len(image_bytes)})"
        )
    safe_suffix = _sanitize_image_suffix(original_filename)

    new_basename = f"{uuid.uuid4().hex}{safe_suffix}"
    target_dir = resolve_assets_dir(data_dir) / exam_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / new_basename
    target.write_bytes(image_bytes)

    return f"{exam_id}/{new_basename}"


def upload_asset_to_open_exam(
    image_bytes: bytes, original_filename: str
) -> str | None:
    """Upload an asset into the currently opened exam's pool.

    Reads the "opened exam" summary from :data:`app.storage.user`,
    resolves the data directory via :func:`resolve_data_dir` and
    delegates to :func:`upload_asset_to_exam`. Returns the
    exam-relative path on success, or ``None`` when no exam is open
    — matching the other ``*_open_exam`` wrappers so the UI can
    distinguish "nothing open" from real outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return upload_asset_to_exam(
        resolve_data_dir(),
        str(summary.get("id")),
        image_bytes,
        original_filename,
    )


def list_exam_assets(data_dir: Path, exam_id: str) -> list[str]:
    """Return the sorted list of exam-relative asset paths for ``exam_id``.

    Walks ``<data_dir>/assets/<exam_id>/`` and yields one
    ``<exam_id>/<filename>`` string per image file it finds (non-image
    extensions are filtered via :data:`_ALLOWED_IMAGE_SUFFIXES`, matching
    what :func:`upload_asset_to_exam` itself accepts). The list is
    sorted by filename so the gallery UI has a stable order.

    Returns ``[]`` when the per-exam subdirectory does not exist yet
    (fresh exam with no uploads) — callers can treat this as the
    "empty gallery" signal without branching on the missing dir.
    """

    exam_dir = resolve_assets_dir(data_dir) / exam_id
    if not exam_dir.is_dir():
        return []

    result: list[str] = []
    for entry in sorted(exam_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _ALLOWED_IMAGE_SUFFIXES:
            continue
        result.append(f"{exam_id}/{entry.name}")
    return result


def list_open_exam_assets() -> list[str]:
    """Return the asset list for the currently opened exam, or ``[]``.

    UI-facing wrapper around :func:`list_exam_assets`. Unlike the
    ``*_open_exam`` helpers that distinguish "nothing open" via
    ``None``, this one returns ``[]`` because the caller (the editor
    gallery) always renders *some* UI — a missing exam just gives an
    empty gallery, not a dedicated error state.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return []

    return list_exam_assets(resolve_data_dir(), str(summary.get("id")))


def delete_exam_asset(data_dir: Path, asset_path: str) -> bool:
    """Delete an exam-pool asset from disk.

    ``asset_path`` is the exam-relative string returned by
    :func:`upload_asset_to_exam` / stored in question markdown
    (``<exam_id>/<filename>``). Returns ``True`` when the file was
    removed, ``False`` when it did not exist — idempotent no-op.

    The caller is responsible for scrubbing any remaining markdown
    references; we deliberately do NOT touch the DB because the
    exam-wide asset pool is not modeled as a first-class DB entity
    (it's just files next to ``data/*.sqlite``). Question ``image_path``
    columns that happen to still reference the deleted file will
    render as a broken thumbnail next time the Designer opens, which
    matches the 'orphan cleanup is Phase 9' rationale we use for
    :func:`clear_question_image_in_file` too.
    """

    target = resolve_assets_dir(data_dir) / asset_path
    if not target.is_file():
        return False
    target.unlink()
    return True


def delete_open_exam_asset(asset_path: str) -> bool | None:
    """Delete ``asset_path`` from the currently opened exam's pool.

    UI-facing wrapper around :func:`delete_exam_asset`. Returns:

    * ``True`` when the file was removed.
    * ``False`` when the file did not exist (idempotent no-op).
    * ``None`` when no exam is open.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return delete_exam_asset(resolve_data_dir(), asset_path)



def replace_question_hotspot_in_open_exam(
    question_id: str, new_hotspot: Hotspot
) -> bool | None:
    """Replace ``question_id``'s hotspot payload in the opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`replace_question_hotspot_in_file`. Returns:

    * ``True`` when the row was updated.
    * ``False`` when the id is unknown (idempotent no-op).
    * ``None`` when no exam is open, matching the other
      ``*_in_open_exam`` wrappers so the UI can distinguish "nothing
      open" from DAO-level outcomes. ``question_count`` stays in sync
      because hotspot edits do not add or remove rows.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    return replace_question_hotspot_in_file(path, question_id, new_hotspot)


#: Subdirectory (under the data directory) where JSON bundle exports land.
#: Keeping exports in a dedicated folder means the ``.sqlite`` listing in
#: :func:`list_exam_files` stays focused on editable source-of-truth files
#: and the user's own directory of exams does not get polluted with
#: point-in-time snapshots.
EXPORTS_DIRNAME = "exports"


def resolve_exports_dir(data_dir: Path) -> Path:
    """Return the JSON exports subdirectory, creating it if missing.

    JSON bundle snapshots live at ``data_dir / EXPORTS_DIRNAME /
    <exam_id>-<timestamp>.json``. The top-level ``exports`` dir is
    created eagerly so the Designer never has to branch on "does the
    folder exist yet"; per-export timestamped files are written
    straight from :func:`export_exam_to_json_in_file`.
    """

    exports = data_dir / EXPORTS_DIRNAME
    exports.mkdir(parents=True, exist_ok=True)
    return exports


def _format_export_timestamp(now: datetime) -> str:
    """Return ``now`` as a filesystem-safe ``YYYYMMDD-HHMMSS`` string.

    Colons and spaces from ISO-8601 break on Windows paths, so we use a
    compact numeric layout that sorts lexicographically by time. UTC vs
    local is left to the caller — for the Designer UI we use ``datetime
    .now()`` (local), which matches the user's expectation when they
    see the filename.
    """

    return now.strftime("%Y%m%d-%H%M%S")


def export_exam_to_json_in_file(
    db_path: Path,
    exam_id: str,
    data_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Export the exam at ``db_path`` to a JSON bundle under ``data_dir``.

    Opens the exam database at ``db_path``, delegates to
    :func:`posrat.io.export_exam_to_json_file` against a fresh path
    ``resolve_exports_dir(data_dir) / f"{exam_id}-{timestamp}.json"`` and
    returns the absolute path of the written file. The ``exports``
    directory is created on demand.

    ``now`` is a dependency injection seam — tests pin a specific
    ``datetime`` to get a deterministic filename; production callers
    omit it and pick up :func:`datetime.now`. Raises :class:`LookupError`
    when ``exam_id`` does not exist in the database (surfaced verbatim
    from :func:`posrat.io.export_exam_to_json_file`); no file is
    written in that case.
    """

    timestamp = _format_export_timestamp(now or datetime.now())
    target = resolve_exports_dir(data_dir) / f"{exam_id}-{timestamp}.json"

    db = open_db(db_path)
    try:
        export_exam_to_json_file(db, exam_id, target)
    finally:
        db.close()
    return target


def export_open_exam_to_json() -> Path | None:
    """Export the currently opened exam as a JSON bundle.

    Reads the "opened exam" summary from :data:`app.storage.user`,
    resolves the data directory via :func:`resolve_data_dir` and
    delegates to :func:`export_exam_to_json_in_file`. Returns the
    absolute path of the written JSON file on success, or ``None``
    when no exam is open — matching the other ``*_open_exam`` wrappers
    so the UI can distinguish "nothing open" from real outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    return export_exam_to_json_in_file(
        Path(str(summary.get("path"))),
        str(summary.get("id")),
        resolve_data_dir(),
    )


#: UI caption shown in the Designer status card when the currently opened
#: exam has no pending in-memory edits. Every per-field dialog currently
#: commits straight to the DB (see steps 5.1–5.7), so the label is
#: effectively always "Saved" today — but carving out a dedicated helper
#: :func:`is_open_exam_dirty` now means the full right-side Properties
#: panel (Phase 9) only has to flip one boolean to toggle the caption to
#: :data:`DIRTY_LABEL_TEXT`.
SAVED_LABEL_TEXT = "Saved"

#: UI caption shown when in-memory edits have not yet been persisted.
#: Wired in 5.9 as scaffold; becomes reachable when live Properties
#: bindings land in Fáze 9.
DIRTY_LABEL_TEXT = "Unsaved changes"


def is_open_exam_dirty() -> bool:
    """Return ``True`` when the opened exam has unsaved in-memory edits.

    Step 5.9 scaffold: every current Designer editor commits to disk
    inside its dialog's Save handler (5.1 text, 5.2 type, 5.3/5.4
    choices, 5.5 hotspot, 5.6 explanation, 5.7 image), so by definition
    nothing is ever dirty between dialog sessions — the function always
    returns ``False``.

    The helper exists so the UI can render a dedicated "Saved" /
    "Unsaved changes" caption without the status-card render path
    having to know anything about the (future) per-field dirty
    tracking that the Phase 9 live Properties panel will plug in.
    When that arrives, all it needs to do is flip the backing storage
    key and every banner updates on the next refresh.
    """

    return False


def delete_question_from_file(path: Path, question_id: str) -> bool:
    """Delete ``question_id`` from the exam database at ``path``.

    Thin wrapper around :func:`posrat.storage.delete_question` that owns the
    DB connection lifecycle. Returns the DAO's truthy/falsey result verbatim:
    ``True`` when a row was removed, ``False`` when the id did not exist
    (idempotent no-op). CASCADE foreign keys ensure choices / hotspot
    payload of the removed question are also gone.

    Extracted as a pure helper so tests can exercise the DAO path without a
    NiceGUI request context; the UI-facing wrapper is
    :func:`delete_question_from_open_exam`.
    """

    db = open_db(path)
    try:
        return delete_question(db, question_id)
    finally:
        db.close()


def delete_question_from_open_exam(question_id: str) -> bool | None:
    """Delete ``question_id`` from the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`delete_question_from_file`. Returns:

    * ``True`` when a row was actually removed — the cached
      ``question_count`` in the storage summary is decremented to match.
    * ``False`` when the id did not exist (no-op) — summary is left alone.
    * ``None`` when no exam is open, matching the
      :func:`add_blank_question_to_open_exam` contract so the UI can
      distinguish "nothing open" from DAO-level outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    removed = delete_question_from_file(path, question_id)

    if removed:
        current = int(summary.get("question_count", 0))
        summary["question_count"] = max(current - 1, 0)
        app.storage.user[OPEN_EXAM_STORAGE_KEY] = summary
    return removed


#: Directions accepted by :func:`move_question_in_file`. Kept as constants so
#: UI handlers and storage helpers share a single vocabulary; any drift would
#: be caught immediately by the type checker / helper ``ValueError`` path.
MOVE_UP = "up"
MOVE_DOWN = "down"


#: Key used inside ``app.storage.user`` to persist the current Designer
#: search query. The value is bound to a ``ui.input`` via
#: :meth:`nicegui.elements.mixins.value_element.ValueElement.bind_value`, so
#: the query survives Designer refreshes and tab reloads. Kept separate from
#: :data:`OPEN_EXAM_STORAGE_KEY` because it applies across exams — the user
#: can switch exams without retyping the filter.
SEARCH_QUERY_STORAGE_KEY = "designer_search_query"


def filter_questions(questions: list[Question], query: str) -> list[Question]:
    """Return questions matching ``query`` on their id or text.

    Matching is a plain case-insensitive substring check against both
    ``question.id`` and ``question.text`` so the user can type either the
    auto-generated id (``q-abc123``) or a text fragment to locate the right
    row. An empty or whitespace-only ``query`` is treated as "no filter" and
    returns a shallow copy of ``questions`` verbatim — callers can keep
    calling this helper unconditionally instead of branching on empty input.
    """

    normalized = query.strip().lower()
    if not normalized:
        return list(questions)

    return [
        question
        for question in questions
        if normalized in question.id.lower()
        or normalized in question.text.lower()
    ]



def reorder_questions_in_file(
    path: Path, exam_id: str, ordered_ids: list[str]
) -> None:
    """Apply ``ordered_ids`` to the exam DB at ``path``.

    Thin wrapper around :func:`posrat.storage.reorder_questions` that owns
    the DB connection lifecycle. Extracted as a pure helper so tests and
    higher-level reorder primitives (Move up / Move down in 4.6.b, future
    drag-n-drop in 4.6.c) can share one transactional entry point.
    """

    db = open_db(path)
    try:
        reorder_questions(db, exam_id, ordered_ids)
    finally:
        db.close()


def move_question_in_file(
    path: Path, exam_id: str, question_id: str, direction: str
) -> bool:
    """Swap ``question_id`` with its neighbour in the given ``direction``.

    Reads the current order via :func:`list_questions`, swaps
    ``question_id`` with its immediate predecessor (``direction="up"``) or
    successor (``direction="down"``) and persists the new order through
    :func:`reorder_questions_in_file`. Returns ``True`` when the order
    actually changed, ``False`` when the question is already at the
    respective edge (top row cannot move up, bottom row cannot move down)
    so callers can distinguish "did something" from "already there".

    Raises :class:`ValueError` for unknown ``direction`` values or when
    ``question_id`` is not present in the exam. Extracted as a pure helper
    so tests can drive the logic without a NiceGUI request context; the
    UI-facing wrapper is :func:`move_question_in_open_exam`.
    """

    if direction not in (MOVE_UP, MOVE_DOWN):
        raise ValueError(f"Unknown move direction: {direction!r}")

    db = open_db(path)
    try:
        current = [q.id for q in list_questions(db, exam_id)]
    finally:
        db.close()

    if question_id not in current:
        raise ValueError(
            f"question id not found in exam {exam_id!r}: {question_id!r}"
        )

    index = current.index(question_id)
    if direction == MOVE_UP and index == 0:
        return False
    if direction == MOVE_DOWN and index == len(current) - 1:
        return False

    swap_with = index - 1 if direction == MOVE_UP else index + 1
    new_order = list(current)
    new_order[index], new_order[swap_with] = (
        new_order[swap_with],
        new_order[index],
    )

    reorder_questions_in_file(path, exam_id, new_order)
    return True


def move_question_in_open_exam(question_id: str, direction: str) -> bool | None:
    """Move ``question_id`` up or down in the currently opened exam, if any.

    Reads the "opened exam" summary from :data:`app.storage.user` and
    delegates to :func:`move_question_in_file`. Returns:

    * ``True`` when the order changed.
    * ``False`` when the question is already at the respective edge — a
      benign no-op that the UI surfaces as a neutral notification rather
      than an error.
    * ``None`` when no exam is open, matching the :func:`*_from_open_exam`
      conventions so the UI can distinguish "nothing open" from DAO-level
      outcomes.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return None

    path = Path(str(summary.get("path")))
    exam_id = str(summary.get("id"))
    return move_question_in_file(path, exam_id, question_id, direction)


def _truncate_question_text(text: str, limit: int = QUESTION_LIST_TEXT_PREVIEW) -> str:
    """Return ``text`` shortened to ``limit`` chars with an ellipsis suffix."""

    flattened = " ".join(text.split())

    if len(flattened) <= limit:
        return flattened
    return flattened[: max(limit - 1, 0)] + "…"


def _render_question_list(questions: list[Question], query: str = "") -> None:
    """Render the per-question rows of the Question Browser.

    Each row shows a short type badge (``single_choice`` / ``multi_choice`` /
    ``hotspot``) followed by the question id, a truncated preview of its
    text, Move up / Move down buttons (step 4.6.b) and a "Delete" button
    that opens the delete confirmation dialog (step 4.5). The layout is
    deliberately dense — one line per question — so the list scales to
    exams with dozens of entries without scrolling. Full question editing
    will land in the Properties panel (Phase 5).

    ``query`` is the current Designer search string (step 4.7). Rows are
    filtered client-side via :func:`filter_questions` while the edge-disable
    logic for Move up / Move down stays based on each row's position in the
    *full* unfiltered list — moving a filtered row up still swaps it with
    its on-disk predecessor, not with the next visible row. Two distinct
    empty-state placeholders separate "exam has no questions" from "filter
    hides everything" so the user can always tell why the list is empty.
    """

    if not questions:
        ui.label("No questions yet. Add one (step 4.4).").classes(
            "text-caption text-grey q-mt-sm"
        )
        return

    visible = filter_questions(questions, query)
    if not visible:
        ui.label(
            "No question matches the search query."
        ).classes("text-caption text-grey q-mt-sm")
        return

    last_index = len(questions) - 1

    with ui.column().classes("q-mt-sm q-gutter-xs w-full"):
        for question in visible:
            index = questions.index(question)
            with ui.row().classes("items-center q-gutter-sm no-wrap"):
                ui.badge(question.type).props("color=primary")
                # Display ``Q1``, ``Q2``, …, ``Qn`` instead of the raw
                # UUID-ish ``question.id``. The id stays a stable key
                # for JSON export / session answers; the label is a
                # pure function of list position so reorders renumber
                # for free. Tooltip still exposes the real id for
                # debugging and for users who want to map a row back
                # to the underlying DB record.
                ui.label(format_question_label(index)).classes(
                    "text-body2 text-weight-medium"
                ).tooltip(question.id)
                ui.label(_truncate_question_text(question.text)).classes(

                    "text-body2 ellipsis"
                ).style(
                    f"max-width: {QUESTION_LIST_TEXT_WIDTH_PX}px;"
                    f" width: {QUESTION_LIST_TEXT_WIDTH_PX}px;"
                    " overflow: hidden; text-overflow: ellipsis;"
                    " white-space: nowrap;"
                )

                up_button = ui.button(
                    "Up",
                    on_click=lambda _evt=None, q=question: _handle_move_question_click(q, MOVE_UP),
                ).props("size=xs flat color=primary")
                if index == 0:
                    up_button.props("disable")
                down_button = ui.button(
                    "Down",
                    on_click=lambda _evt=None, q=question: _handle_move_question_click(q, MOVE_DOWN),
                ).props("size=xs flat color=primary")
                ui.button(
                    "Type",
                    on_click=lambda _evt=None, q=question: _handle_change_question_type_click(q),
                ).props("size=xs flat color=primary")
                ui.button(
                    "Edit",
                    on_click=lambda _evt=None, q=question: _handle_edit_question_text_click(q),
                ).props("size=xs flat color=primary")
                if question.type in ("single_choice", "multi_choice"):
                    ui.button(
                        "Answers",
                        on_click=lambda _evt=None, q=question: _handle_edit_choices_click(q),
                    ).props("size=xs flat color=primary")
                if question.type == "hotspot":
                    ui.button(
                        "Hotspot",
                        on_click=lambda _evt=None, q=question: _handle_edit_hotspot_click(q),
                    ).props("size=xs flat color=primary")
                ui.button(
                    "Explain",

                    on_click=lambda _evt=None, q=question: _handle_edit_question_explanation_click(q),
                ).props("size=xs flat color=primary")
                ui.button(
                    "Image",
                    on_click=lambda _evt=None, q=question: _handle_edit_question_image_click(q),
                ).props("size=xs flat color=primary")

                ui.button(
                    "Delete",
                    on_click=lambda _evt=None, q=question: _handle_delete_question_click(q),
                ).props("size=xs flat color=negative")





def _build_open_exam_summary(path: Path, exam: Exam) -> dict[str, object]:
    """Return the JSON-serialisable snapshot we stash in user storage.

    Since Phase 7A.5 the snapshot also carries a ``metadata`` dict with
    the four Runner-facing exam metadata fields
    (``default_question_count`` / ``time_limit_minutes`` /
    ``passing_score`` / ``target_score``) so the Properties panel can
    pre-populate its "Exam settings" section without re-opening the
    database on every render. The dict is rebuilt on each open / save
    so the Properties panel sees fresh values after a blur-save.
    """

    return {
        "path": str(path.resolve()),
        "id": exam.id,
        "name": exam.name,
        "description": exam.description,
        "question_count": len(exam.questions),
        "metadata": {
            "default_question_count": exam.default_question_count,
            "time_limit_minutes": exam.time_limit_minutes,
            "passing_score": exam.passing_score,
            "target_score": exam.target_score,
        },
    }



def _handle_open_click(path: Path) -> None:
    """Open ``path``, stash a summary in user storage, and refresh the UI."""

    try:
        exam = open_exam_from_file(path)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot open {path.name}: {exc}", type="negative")
        return

    app.storage.user[OPEN_EXAM_STORAGE_KEY] = _build_open_exam_summary(path, exam)
    ui.notify(f"Exam '{exam.name}' opened ({len(exam.questions)} questions).")
    _render_designer_body.refresh()


def _show_new_exam_dialog() -> None:
    """Open a modal dialog that collects exam metadata and creates a new DB.

    The dialog holds a minimal form (id / name / description). On submit we
    call :func:`create_exam_file` against the resolved data directory and,
    on success, immediately auto-open the newly-created exam by delegating
    to :func:`_handle_open_click`. All error paths (validation failure from
    Pydantic, duplicate path, SQLite errors) surface through
    ``ui.notify(type="negative")`` so the dialog stays usable.
    """

    with ui.dialog() as dialog, ui.card():
        ui.label("New exam").classes("text-h6")
        id_input = ui.input("ID").props("autofocus").classes("w-full")
        name_input = ui.input("Name").classes("w-full")
        description_input = ui.textarea("Description (optional)").classes("w-full")
        question_count_input = (
            ui.number(
                "Number of questions (optional)",
                min=1,
                step=1,
                format="%d",
            )
            .classes("w-full")
        )
        passing_score_input = (
            ui.number(
                "Passing score (optional)",
                min=0,
                step=1,
                format="%d",
            )
            .classes("w-full")
        )
        time_limit_input = (
            ui.number(
                "Duration — time limit in minutes (optional)",
                min=1,
                step=1,
                format="%d",
            )
            .classes("w-full")
        )


        def _optional_int(raw: object) -> int | None:
            """Return ``raw`` as an int, or ``None`` when the field is empty.

            ``ui.number`` reports an empty input as ``None`` or ``""``.
            We keep those as ``None`` so the Runner treats the metadata
            as "not set" rather than 0. Non-parseable garbage becomes
            ``None`` too — the backing Pydantic validator will reject
            any out-of-range value downstream.
            """

            if raw is None or raw == "":
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        def _submit() -> None:
            exam_id = (id_input.value or "").strip()
            name = (name_input.value or "").strip()
            description = (description_input.value or "").strip() or None
            default_question_count = _optional_int(question_count_input.value)
            passing_score = _optional_int(passing_score_input.value)
            time_limit_minutes = _optional_int(time_limit_input.value)

            data_dir = resolve_data_dir()
            try:
                new_path = create_exam_file(
                    data_dir,
                    exam_id,
                    name,
                    description,
                    default_question_count=default_question_count,
                    passing_score=passing_score,
                    time_limit_minutes=time_limit_minutes,
                )

            except FileExistsError as exc:
                ui.notify(f"File already exists: {exc}", type="negative")
                return
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(f"Cannot create exam: {exc}", type="negative")
                return

            dialog.close()
            _handle_open_click(new_path)

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Create", on_click=_submit).props("color=primary")

    dialog.open()



def _handle_add_question_click() -> None:
    """Append a blank question to the opened exam and refresh the UI.

    Thin UI wrapper around :func:`add_blank_question_to_open_exam`. All
    failure modes (DB error, missing exam id) surface as negative
    ``ui.notify`` toasts so the user gets immediate feedback without the
    Designer body silently diverging from disk state.
    """

    try:
        new_id = add_blank_question_to_open_exam()
    except (LookupError, sqlite3.DatabaseError, ValueError) as exc:
        ui.notify(f"Cannot add question: {exc}", type="negative")
        return

    if new_id is None:
        ui.notify("No exam is open.", type="warning")
        return

    ui.notify(f"Question {new_id} added.")
    _render_designer_body.refresh()


def _handle_edit_question_text_click(question: Question) -> None:
    """Open an inline editor dialog for ``question``'s ``text`` field.

    Seeds a ``ui.textarea`` with the current question text and offers
    "Save" / "Cancel" buttons. Saving delegates to
    :func:`update_question_text_in_open_exam`; success, no-op
    (``False`` — id vanished between render and save), empty-text
    validation and database errors each surface through ``ui.notify``
    with an appropriate colour. On successful save we refresh the
    Designer body so the row's truncated preview picks up the new text
    straight from disk. First slice of the Phase 5 Properties panel:
    later steps will expand this dialog with ``type`` / choices /
    hotspot / explanation / image editors.
    """

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Edit question text").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        text_input = (
            ui.textarea("Question text", value=question.text)
            .props("autofocus autogrow")
            .classes("w-full")
        )

        def _confirm() -> None:
            new_text = (text_input.value or "").strip()
            if not new_text:
                ui.notify(
                    "Question text must not be empty.", type="negative"
                )
                return

            if new_text == question.text:
                dialog.close()
                ui.notify("Question text unchanged.", type="info")
                return

            try:
                updated = update_question_text_in_open_exam(
                    question.id, new_text
                )
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(
                    f"Cannot update question: {exc}", type="negative"
                )
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Question {question.id} updated.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_change_question_type_click(question: Question) -> None:
    """Open an inline dropdown dialog to change ``question``'s ``type``.

    Seeds a :func:`ui.select` with :data:`ALLOWED_QUESTION_TYPES` values
    and the current type pre-selected. On save the dialog delegates to
    :func:`change_question_type_in_open_exam`; success, no-op
    (``False`` — id vanished between render and save), unknown-type
    ``ValueError`` and database errors each surface through
    ``ui.notify`` with an appropriate colour.

    A type change is never a byte-for-byte preserving edit (it rewrites
    the whole payload via ``update_question``), so the UI clearly
    communicates this by refreshing the Designer body on success — the
    user sees the new badge colour and row contents straight from disk.
    When the user picks the current type the helper short-circuits with
    an info toast and skips the DB hit (same pattern as
    :func:`_handle_edit_question_text_click`).
    """

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Change question type").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        ui.label(
            "Warning: changing the type may replace answers or hotspot with defaults."
        ).classes("text-caption text-orange")
        type_select = ui.select(
            options=list(ALLOWED_QUESTION_TYPES),
            value=question.type,
            label="Type",
        ).classes("w-full")

        def _confirm() -> None:
            new_type = str(type_select.value or "").strip()
            if not new_type:
                ui.notify("Select a question type.", type="negative")
                return

            if new_type == question.type:
                dialog.close()
                ui.notify("Question type unchanged.", type="info")
                return

            try:
                updated = change_question_type_in_open_exam(
                    question.id, new_type
                )
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(f"Cannot change type: {exc}", type="negative")
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(
                f"Question {question.id} type changed to {new_type}."
            )
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_edit_choices_click(question: Question) -> None:
    """Dispatch to the per-type choices editor dialog.

    Central entry point invoked from the question-list row's "Answers"
    button. The button is only rendered for ``single_choice`` /
    ``multi_choice`` rows (see :func:`_render_question_list`), but the
    dispatcher still guards against a stale ``Question`` in memory (e.g.
    the row was morphed to ``hotspot`` from another tab between render
    and click) by surfacing a warning toast instead of raising.

    Each type has its own editor because the correctness invariant is
    different: single_choice requires *exactly* one correct, multi_choice
    requires ≥1 (up to all-correct). Sharing the backing pure helper
    :func:`replace_question_choices_in_file` means both editors push
    through the same Pydantic validation and the same DB transaction.
    """

    if question.type == "single_choice":
        _handle_edit_single_choices_click(question)
        return
    if question.type == "multi_choice":
        _handle_edit_multi_choices_click(question)
        return

    ui.notify(
        f"Answer editor is not available for type {question.type}.",
        type="warning",
    )


def _handle_edit_single_choices_click(question: Question) -> None:
    """Open an editor dialog for ``question``'s ``single_choice`` list.

    Renders a ``ui.column`` of rows, each row = one existing choice with
    its id shown as a caption, a text input bound to the choice's
    ``text`` and a :func:`ui.radio` group that enforces the single-
    correct invariant client-side (only one row can be "correct" at a
    time). A "Add choice" button appends a fresh blank choice with a
    freshly-minted id; a per-row "Remove" button drops a choice,
    subject to the ≥2 guard enforced on save.

    Save flow:

    1. Require at least 2 non-blank choices, and exactly one marked
       correct. Surface violations through ``ui.notify(type="negative")``
       without closing the dialog.
    2. Build the ``list[Choice]`` and delegate to
       :func:`replace_question_choices_in_open_exam`.
    3. Standard outcomes: ``True`` = positive notify + refresh,
       ``False`` = warning + refresh (race — row vanished), ``None`` =
       warning, ``ValueError`` / ``sqlite3.DatabaseError`` = negative
       notify without closing the dialog so user can fix inputs.

    Only ``single_choice`` questions are accepted — multi_choice goes
    through :func:`_handle_edit_multi_choices_click`. Both editors share
    the backing pure helper
    :func:`replace_question_choices_in_open_exam`.
    """

    if question.type != "single_choice":
        ui.notify(
            f"Single-choice answer editor cannot be used for type {question.type}.",
            type="warning",
        )
        return

    # Working copy the dialog mutates. We clone deep so Cancel really
    # does cancel: the on-disk question keeps its original choices.
    draft: list[dict[str, object]] = [
        {"id": c.id, "text": c.text, "is_correct": c.is_correct}
        for c in question.choices
    ]
    # Stable correct-option tracker — the radio value is the chosen
    # choice's ``id`` so we can mutate the draft list without rebuilding
    # the radio every time a row gets added/removed.
    correct_id: str | None = next(
        (str(c["id"]) for c in draft if c["is_correct"]), None
    )

    def _next_choice_id() -> str:
        """Mint a fresh choice id anchored to the owning question."""

        existing = {str(c["id"]) for c in draft}
        while True:
            candidate = f"{question.id}-{uuid.uuid4().hex[:6]}"
            if candidate not in existing:
                return candidate

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Edit answers (single_choice)").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        ui.label(
            "Pick exactly one correct answer. At least 2 choices must remain."
        ).classes("text-caption text-grey")

        rows_container = ui.column().classes("w-full q-gutter-sm q-mt-sm")

        @ui.refreshable
        def _render_rows() -> None:
            nonlocal correct_id
            with rows_container:
                rows_container.clear()
                for entry in draft:
                    choice_id = str(entry["id"])
                    with ui.row().classes("items-start q-gutter-sm no-wrap w-full"):
                        ui.label(choice_id).classes(
                            "text-caption text-grey q-mt-sm"
                        )
                        # Multi-line textarea with auto-grow so the user
                        # can pohodlně odřádkovat longer explanations or
                        # paste text containing newlines. ``autogrow``
                        # keeps the field compact for short answers yet
                        # expands for full-paragraph ones.
                        text_input = ui.textarea(
                            "Text", value=str(entry["text"])
                        ).props("autogrow dense").classes("col-grow")

                        def _on_text_change(
                            event=None, e=entry, inp=text_input
                        ) -> None:
                            e["text"] = inp.value or ""

                        text_input.on_value_change(_on_text_change)

                        def _mark_correct(
                            event=None, cid=choice_id
                        ) -> None:
                            nonlocal correct_id
                            correct_id = cid
                            for d in draft:
                                d["is_correct"] = str(d["id"]) == cid

                        correct_btn = ui.button(
                            "Correct" if correct_id == choice_id else "Set as correct",
                            on_click=_mark_correct,
                        ).props(
                            "size=xs "
                            + (
                                "color=positive"
                                if correct_id == choice_id
                                else "flat color=primary"
                            )
                        )
                        del correct_btn  # silence unused-var lints

                        def _remove_row(
                            event=None, cid=choice_id
                        ) -> None:
                            nonlocal correct_id
                            # Drop the entry; keep ``correct_id`` in sync
                            # if the removed row was the correct one.
                            draft[:] = [d for d in draft if str(d["id"]) != cid]
                            if correct_id == cid:
                                correct_id = None
                            _render_rows.refresh()

                        ui.button("Remove", on_click=_remove_row).props(
                            "size=xs flat color=negative"
                        )

        def _add_row() -> None:
            draft.append(
                {
                    "id": _next_choice_id(),
                    "text": "",
                    "is_correct": False,
                }
            )
            _render_rows.refresh()

        with ui.row().classes("justify-start q-mt-sm"):
            ui.button("Add choice", on_click=_add_row).props(
                "size=sm color=primary"
            )

        _render_rows()

        def _confirm() -> None:
            # Normalise texts — strip whitespace and drop fully-empty rows
            # so a user who added a placeholder row they forgot to fill in
            # can still save (as long as 2 real rows survive).
            cleaned: list[dict[str, object]] = []
            for entry in draft:
                text_value = str(entry.get("text") or "").strip()
                if not text_value:
                    continue
                cleaned.append(
                    {
                        "id": str(entry["id"]),
                        "text": text_value,
                        "is_correct": bool(entry.get("is_correct")),
                    }
                )

            if len(cleaned) < 2:
                ui.notify(
                    "Single-choice question requires at least 2 choices with text.",
                    type="negative",
                )
                return

            correct_count = sum(1 for c in cleaned if c["is_correct"])
            if correct_count != 1:
                ui.notify(
                    "Exactly one correct answer must be marked.",
                    type="negative",
                )
                return

            new_choices = [
                Choice(
                    id=str(c["id"]),
                    text=str(c["text"]),
                    is_correct=bool(c["is_correct"]),
                )
                for c in cleaned
            ]

            try:
                updated = replace_question_choices_in_open_exam(
                    question.id, new_choices
                )
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(
                    f"Cannot save answers: {exc}", type="negative"
                )
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Answers for question {question.id} saved.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_edit_multi_choices_click(question: Question) -> None:
    """Open an editor dialog for ``question``'s ``multi_choice`` list.

    Twin of :func:`_handle_edit_single_choices_click`, but swapping the
    single-correct radio for a per-row checkbox so any non-empty subset of
    choices can be marked correct — matching multi_choice's Pydantic
    invariant (≥2 choices, ≥1 correct, all-correct is legal). The
    "Add choice" button appends a fresh blank choice with a freshly-
    minted id; a per-row "Remove" button drops a choice, subject to the
    ≥2 guard enforced on save.

    Save flow:

    1. Require at least 2 non-blank choices and at least one marked
       correct. Surface violations through ``ui.notify(type="negative")``
       without closing the dialog.
    2. Build the ``list[Choice]`` and delegate to
       :func:`replace_question_choices_in_open_exam`.
    3. Standard outcomes: ``True`` = positive notify + refresh,
       ``False`` = warning + refresh (race — row vanished), ``None`` =
       warning, ``ValueError`` / ``sqlite3.DatabaseError`` = negative
       notify without closing the dialog so user can fix inputs.

    Only ``multi_choice`` questions are accepted — single_choice goes
    through :func:`_handle_edit_single_choices_click`. Both editors
    share the backing pure helper
    :func:`replace_question_choices_in_open_exam` so persistence,
    transaction boundary and Pydantic validation stay identical.
    """

    if question.type != "multi_choice":
        ui.notify(
            f"Multi-choice answer editor cannot be used for type {question.type}.",
            type="warning",
        )
        return

    # Working copy the dialog mutates. We clone deep so Cancel really
    # does cancel: the on-disk question keeps its original choices.
    draft: list[dict[str, object]] = [
        {"id": c.id, "text": c.text, "is_correct": c.is_correct}
        for c in question.choices
    ]

    def _next_choice_id() -> str:
        """Mint a fresh choice id anchored to the owning question."""

        existing = {str(c["id"]) for c in draft}
        while True:
            candidate = f"{question.id}-{uuid.uuid4().hex[:6]}"
            if candidate not in existing:
                return candidate

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Edit answers (multi_choice)").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        ui.label(
            "Mark one or more correct answers. At least 2 choices "
            "must remain and at least one must be correct."
        ).classes("text-caption text-grey")

        rows_container = ui.column().classes("w-full q-gutter-sm q-mt-sm")

        @ui.refreshable
        def _render_rows() -> None:
            with rows_container:
                rows_container.clear()
                for entry in draft:
                    choice_id = str(entry["id"])
                    with ui.row().classes("items-start q-gutter-sm no-wrap w-full"):
                        ui.label(choice_id).classes(
                            "text-caption text-grey q-mt-sm"
                        )
                        # Multi-line textarea with auto-grow so the user
                        # can pohodlně odřádkovat longer explanations or
                        # paste text containing newlines. Matches the
                        # single_choice dialog for visual consistency.
                        text_input = ui.textarea(
                            "Text", value=str(entry["text"])
                        ).props("autogrow dense").classes("col-grow")

                        def _on_text_change(
                            event=None, e=entry, inp=text_input
                        ) -> None:
                            e["text"] = inp.value or ""

                        text_input.on_value_change(_on_text_change)

                        # Per-row checkbox drives ``is_correct`` directly.
                        # Multi_choice allows any non-empty subset, so we
                        # don't need the clear-others-when-picked logic
                        # that single_choice's radio uses.
                        correct_checkbox = ui.checkbox(
                            "Correct",
                            value=bool(entry["is_correct"]),
                        )

                        def _on_correct_change(
                            event=None, e=entry, cb=correct_checkbox
                        ) -> None:
                            e["is_correct"] = bool(cb.value)

                        correct_checkbox.on_value_change(_on_correct_change)

                        def _remove_row(
                            event=None, cid=choice_id
                        ) -> None:
                            draft[:] = [d for d in draft if str(d["id"]) != cid]
                            _render_rows.refresh()

                        ui.button("Remove", on_click=_remove_row).props(
                            "size=xs flat color=negative"
                        )

        def _add_row() -> None:
            draft.append(
                {
                    "id": _next_choice_id(),
                    "text": "",
                    "is_correct": False,
                }
            )
            _render_rows.refresh()

        with ui.row().classes("justify-start q-mt-sm"):
            ui.button("Add choice", on_click=_add_row).props(
                "size=sm color=primary"
            )

        _render_rows()

        def _confirm() -> None:
            # Normalise texts — strip whitespace and drop fully-empty rows
            # so a user who added a placeholder row they forgot to fill in
            # can still save (as long as 2 real rows survive).
            cleaned: list[dict[str, object]] = []
            for entry in draft:
                text_value = str(entry.get("text") or "").strip()
                if not text_value:
                    continue
                cleaned.append(
                    {
                        "id": str(entry["id"]),
                        "text": text_value,
                        "is_correct": bool(entry.get("is_correct")),
                    }
                )

            if len(cleaned) < 2:
                ui.notify(
                    "Multi-choice question requires at least 2 choices with text.",
                    type="negative",
                )
                return

            correct_count = sum(1 for c in cleaned if c["is_correct"])
            if correct_count < 1:
                ui.notify(
                    "At least one answer must be marked as correct.",
                    type="negative",
                )
                return

            new_choices = [
                Choice(
                    id=str(c["id"]),
                    text=str(c["text"]),
                    is_correct=bool(c["is_correct"]),
                )
                for c in cleaned
            ]

            try:
                updated = replace_question_choices_in_open_exam(
                    question.id, new_choices
                )
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(
                    f"Cannot save answers: {exc}", type="negative"
                )
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Answers for question {question.id} saved.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_edit_hotspot_click(question: Question) -> None:
    """Open an editor dialog for ``question``'s ``hotspot`` payload.

    The dialog exposes two sections:

    * **Options pool** — each entry carries a stable id (caption) and a
      text input. "Add choice" appends a fresh blank option; per-row
      "Remove" removes one, subject to the ≥1 option guard enforced on
      save. Removing an option that is referenced by any step also
      surfaces as a ``ValueError`` on save because
      :class:`posrat.models.hotspot.Hotspot`'s model validator rejects
      dangling ``correct_option_id`` references.
    * **Kroky (steps)** — each step has a text input for its
      ``prompt`` and a :func:`ui.select` whose options are driven live
      by the current options pool (the dropdown's option list comes
      from ``_option_choices()`` which is called inside the refreshable
      row renderer, so adding/removing options updates the dropdown on
      the very next refresh). "Add step" appends a blank step
      auto-pointing at the first option; per-row "Remove" removes one,
      subject to the ≥1 step guard enforced on save.

    Save flow:

    1. Validate both sections' minimum counts (≥1 option, ≥1 step) and
       that every step has a non-empty prompt + a dropdown selection.
       Negative toasts without closing the dialog — user can fix.
    2. Build ``Hotspot(options=..., steps=...)`` — Pydantic enforces the
       remaining invariants (unique option ids, unique step ids, every
       ``step.correct_option_id`` references a real option). Invalid
       shape raises ``ValueError`` surfaced as a negative toast.
    3. Delegate to :func:`replace_question_hotspot_in_open_exam`.
       Standard outcomes apply: ``True`` = positive notify + refresh,
       ``False`` = warning + refresh (race), ``None`` = warning,
       ``ValueError`` / ``sqlite3.DatabaseError`` = negative notify.

    Only ``hotspot`` questions are accepted — choice-based questions go
    through :func:`_handle_edit_choices_click`.
    """

    if question.type != "hotspot":
        ui.notify(
            f"Hotspot editor cannot be used for type {question.type}.",
            type="warning",
        )
        return

    # Working copies the dialog mutates. Cloning deep means Cancel really
    # does cancel: the on-disk hotspot keeps its original shape.
    options_draft: list[dict[str, object]] = []
    steps_draft: list[dict[str, object]] = []
    if question.hotspot is not None:
        options_draft = [
            {"id": opt.id, "text": opt.text}
            for opt in question.hotspot.options
        ]
        steps_draft = [
            {
                "id": step.id,
                "prompt": step.prompt,
                "correct_option_id": step.correct_option_id,
            }
            for step in question.hotspot.steps
        ]

    def _next_option_id() -> str:
        """Mint a fresh option id anchored to the owning question."""

        existing = {str(o["id"]) for o in options_draft}
        while True:
            candidate = f"{question.id}-opt-{uuid.uuid4().hex[:6]}"
            if candidate not in existing:
                return candidate

    def _next_step_id() -> str:
        """Mint a fresh step id anchored to the owning question."""

        existing = {str(s["id"]) for s in steps_draft}
        while True:
            candidate = f"{question.id}-step-{uuid.uuid4().hex[:6]}"
            if candidate not in existing:
                return candidate

    def _option_choices() -> dict[str, str]:
        """Return a ``{option_id: display_text}`` map for step dropdowns.

        The select uses the id as the value and a short label
        ``<id>: <text>`` as the visible caption so the user can tell
        options apart even when two have the same text. Stripped texts
        fall back to ``(bez textu)`` so freshly-added blank options are
        still selectable — on save the actual text strip happens in
        the ``Hotspot`` constructor via Pydantic's ``min_length``.
        """

        result: dict[str, str] = {}
        for opt in options_draft:
            opt_id = str(opt["id"])
            raw_text = str(opt.get("text") or "").strip() or "(no text)"
            result[opt_id] = f"{opt_id}: {raw_text}"
        return result

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Edit hotspot").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        ui.label(
            "Options form a shared pool. Each step has a prompt and a "
            "dropdown with the correct option from the pool. At least 1 option and 1 step."
        ).classes("text-caption text-grey")

        # --- Options section ----------------------------------------
        ui.separator().classes("q-my-sm")
        ui.label("Options (pool)").classes("text-subtitle2")
        options_container = ui.column().classes("w-full q-gutter-sm")
        steps_container = ui.column().classes("w-full q-gutter-sm")

        @ui.refreshable
        def _render_options() -> None:
            with options_container:
                options_container.clear()
                for opt in options_draft:
                    opt_id = str(opt["id"])
                    with ui.row().classes("items-center q-gutter-sm no-wrap w-full"):
                        ui.label(opt_id).classes("text-caption text-grey")
                        text_input = ui.input(
                            "Option text", value=str(opt["text"])
                        ).classes("col-grow")

                        def _on_text_change(
                            event=None, e=opt, inp=text_input
                        ) -> None:
                            e["text"] = inp.value or ""
                            # Step dropdown captions include the option
                            # text, so refresh them whenever text changes.
                            _render_steps.refresh()

                        text_input.on_value_change(_on_text_change)

                        def _remove_option(
                            event=None, oid=opt_id
                        ) -> None:
                            options_draft[:] = [
                                o for o in options_draft if str(o["id"]) != oid
                            ]
                            # Any step still pointing at the removed
                            # option is now dangling; clear it so the
                            # user has to re-pick before save.
                            for step in steps_draft:
                                if step.get("correct_option_id") == oid:
                                    step["correct_option_id"] = None
                            _render_options.refresh()
                            _render_steps.refresh()

                        ui.button(
                            "Remove", on_click=_remove_option
                        ).props("size=xs flat color=negative")

        def _add_option() -> None:
            options_draft.append(
                {"id": _next_option_id(), "text": ""}
            )
            _render_options.refresh()
            _render_steps.refresh()

        _render_options()
        with ui.row().classes("justify-start q-mt-sm"):
            ui.button("Add choice", on_click=_add_option).props(
                "size=sm color=primary"
            )

        # --- Steps section ------------------------------------------
        ui.separator().classes("q-my-sm")
        ui.label("Steps").classes("text-subtitle2")

        @ui.refreshable
        def _render_steps() -> None:
            with steps_container:
                steps_container.clear()
                choices = _option_choices()
                for step in steps_draft:
                    step_id = str(step["id"])
                    with ui.row().classes("items-center q-gutter-sm no-wrap w-full"):
                        ui.label(step_id).classes("text-caption text-grey")
                        prompt_input = ui.input(
                            "Prompt", value=str(step["prompt"])
                        ).classes("col-grow")

                        def _on_prompt_change(
                            event=None, e=step, inp=prompt_input
                        ) -> None:
                            e["prompt"] = inp.value or ""

                        prompt_input.on_value_change(_on_prompt_change)

                        current_value = step.get("correct_option_id")
                        # Defensive: if the referenced option was
                        # removed, ``current_value`` may no longer be
                        # a key of ``choices``. Passing None is OK —
                        # the select renders empty and save validation
                        # catches it.
                        option_select = ui.select(
                            options=choices,
                            value=current_value if current_value in choices else None,
                            label="Correct option",
                        ).classes("col-grow")

                        def _on_option_change(
                            event=None, e=step, sel=option_select
                        ) -> None:
                            e["correct_option_id"] = sel.value

                        option_select.on_value_change(_on_option_change)

                        def _remove_step(
                            event=None, sid=step_id
                        ) -> None:
                            steps_draft[:] = [
                                s for s in steps_draft if str(s["id"]) != sid
                            ]
                            _render_steps.refresh()

                        ui.button(
                            "Remove", on_click=_remove_step
                        ).props("size=xs flat color=negative")

        def _add_step() -> None:
            default_option_id: str | None = (
                str(options_draft[0]["id"]) if options_draft else None
            )
            steps_draft.append(
                {
                    "id": _next_step_id(),
                    "prompt": "",
                    "correct_option_id": default_option_id,
                }
            )
            _render_steps.refresh()

        _render_steps()
        with ui.row().classes("justify-start q-mt-sm"):
            ui.button("Add step", on_click=_add_step).props(
                "size=sm color=primary"
            )

        def _confirm() -> None:
            # Normalise options: strip texts, drop entries that ended up
            # with an empty text after strip. Pydantic would reject
            # empty ``text`` anyway, but giving a dedicated message is
            # kinder than surfacing the raw validator error.
            cleaned_options: list[HotspotOption] = []
            for opt in options_draft:
                opt_text = str(opt.get("text") or "").strip()
                if not opt_text:
                    continue
                cleaned_options.append(
                    HotspotOption(id=str(opt["id"]), text=opt_text)
                )

            if not cleaned_options:
                ui.notify(
                    "Hotspot requires at least one option with text.",
                    type="negative",
                )
                return

            # Build steps the same way. Missing prompt / missing
            # ``correct_option_id`` surface as targeted messages before
            # Pydantic's more generic complaint.
            cleaned_steps: list[HotspotStep] = []
            for step in steps_draft:
                prompt = str(step.get("prompt") or "").strip()
                correct_option_id = step.get("correct_option_id")
                if not prompt:
                    ui.notify(
                        f"Step {step['id']} has no prompt.",
                        type="negative",
                    )
                    return
                if not correct_option_id:
                    ui.notify(
                        f"Step {step['id']} has no correct option selected.",
                        type="negative",
                    )
                    return
                cleaned_steps.append(
                    HotspotStep(
                        id=str(step["id"]),
                        prompt=prompt,
                        correct_option_id=str(correct_option_id),
                    )
                )

            if not cleaned_steps:
                ui.notify(
                    "Hotspot requires at least one step.",
                    type="negative",
                )
                return

            try:
                new_hotspot = Hotspot(
                    options=cleaned_options, steps=cleaned_steps
                )
            except ValueError as exc:
                ui.notify(
                    f"Invalid hotspot: {exc}", type="negative"
                )
                return

            try:
                updated = replace_question_hotspot_in_open_exam(
                    question.id, new_hotspot
                )
            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(
                    f"Cannot save hotspot: {exc}", type="negative"
                )
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Hotspot for question {question.id} saved.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_edit_question_explanation_click(question: Question) -> None:


    """Open an inline editor dialog for ``question``'s ``explanation`` field.

    Unlike :func:`_handle_edit_question_text_click`, ``explanation`` is an
    ``Optional[str]``: clearing the textarea maps to ``None`` (SQL
    ``NULL``) rather than being rejected. Whitespace-only input is
    likewise collapsed to ``None`` so the Training-mode Runner never has
    to render a blank explanation bubble.

    Saving delegates to :func:`update_question_explanation_in_open_exam`.
    Success, no-op (``False`` — id vanished between render and save)
    and database errors each surface through ``ui.notify`` with an
    appropriate colour; on successful save we refresh the Designer body
    to re-read disk state even though the row preview doesn't currently
    display the explanation (future Properties panel will).
    """

    current = question.explanation or ""

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Edit explanation").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        explanation_input = (
            ui.textarea("Explanation (optional)", value=current)
            .props("autofocus autogrow")
            .classes("w-full")
        )

        def _confirm() -> None:
            raw = (explanation_input.value or "").strip()
            new_explanation: str | None = raw or None

            if new_explanation == question.explanation:
                dialog.close()
                ui.notify("Explanation unchanged.", type="info")
                return

            try:
                updated = update_question_explanation_in_open_exam(
                    question.id, new_explanation
                )
            except sqlite3.DatabaseError as exc:
                ui.notify(
                    f"Cannot update explanation: {exc}", type="negative"
                )
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Explanation for question {question.id} updated.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_confirm).props("color=primary")

    dialog.open()


def _handle_edit_question_image_click(question: Question) -> None:
    """Open an inline dialog to upload / remove ``question``'s image.

    The dialog shows (when present) a thumbnail of the currently
    attached image served from the static ``/assets`` route, plus two
    actions:

    * :func:`ui.upload` (``auto-upload``, ``max_file_size`` pinned to
      :data:`MAX_IMAGE_SIZE_BYTES`, ``accept=image/*``) whose
      ``on_upload`` delegates to
      :func:`attach_image_to_question_in_open_exam`. The upload
      component auto-regenerates a fresh safe basename on disk, so the
      user sees the filename they picked in the browser but the assets
      directory stays clean.
    * "Remove image" — only rendered when the question already has
      an attachment — calls
      :func:`clear_question_image_in_open_exam` to set
      ``image_path = NULL``. The underlying file on disk is **not**
      deleted (orphan cleanup is a Phase 9 polish task).

    Upload outcomes: ``True`` → positive notify + close + refresh,
    ``False`` (race — id vanished) → warning + close + refresh,
    ``None`` (no exam open) → warning, ``ValueError`` /
    ``sqlite3.DatabaseError`` → negative notify without closing so the
    user can try again with a different file.
    """

    data_dir = resolve_data_dir()
    current_image_abs = resolve_question_image_path(
        data_dir, question.image_path
    )

    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("Question image").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")

        # Preview of the currently attached image, if any. We use
        # ``ui.image`` with the absolute filesystem path — NiceGUI
        # knows how to stream a local file to the browser as long as
        # the path exists and is readable.
        if current_image_abs is not None and current_image_abs.is_file():
            ui.image(str(current_image_abs)).classes(
                "q-mt-sm"
            ).style("max-width: 480px; max-height: 320px;")
            ui.label(str(question.image_path)).classes(
                "text-caption text-grey"
            )
        else:
            ui.label("No image uploaded.").classes(
                "text-caption text-grey q-mt-sm"
            )

        ui.separator().classes("q-my-sm")
        ui.label(
            f"Upload a new image (max "
            f"{MAX_IMAGE_SIZE_BYTES // 1_000_000} MB, PNG/JPG/GIF/WebP/SVG)."
        ).classes("text-caption")

        async def _on_upload(event: events.UploadEventArguments) -> None:
            """Read the uploaded bytes and delegate to the pure helper.

            NiceGUI 3's ``UploadEventArguments`` exposes the payload as
            an awaitable ``event.file.read()`` coroutine (back-end
            transparently spool small uploads in memory and large ones
            to a temp file). The pure helper still wants plain bytes,
            so we await and forward.
            """

            raw = await event.file.read()
            filename = event.file.name

            try:
                updated = attach_image_to_question_in_open_exam(
                    question.id, raw, filename
                )

            except (ValueError, sqlite3.DatabaseError) as exc:
                ui.notify(f"Cannot upload image: {exc}", type="negative")
                return

            dialog.close()

            if updated is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not updated:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Image for question {question.id} uploaded.")
            _render_designer_body.refresh()

        def _on_rejected() -> None:
            ui.notify(
                f"File exceeded the limit "
                f"{MAX_IMAGE_SIZE_BYTES // 1_000_000} MB.",
                type="negative",
            )

        ui.upload(
            on_upload=_on_upload,
            on_rejected=_on_rejected,
            max_file_size=MAX_IMAGE_SIZE_BYTES,
            auto_upload=True,
        ).props("accept=image/*").classes("w-full q-mt-sm")

        def _on_remove() -> None:
            try:
                cleared = clear_question_image_in_open_exam(question.id)
            except sqlite3.DatabaseError as exc:
                ui.notify(
                    f"Cannot remove image: {exc}", type="negative"
                )
                return

            dialog.close()

            if cleared is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not cleared:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Image for question {question.id} removed.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Close", on_click=dialog.close).props("flat")
            if current_image_abs is not None and current_image_abs.is_file():
                ui.button(
                    "Remove image", on_click=_on_remove
                ).props("color=negative")

    dialog.open()


def _handle_delete_question_click(question: Question) -> None:

    """Open a confirmation dialog before deleting ``question``.

    The dialog shows the question id and a truncated preview of its text so
    the user can sanity-check the target before committing. Choosing
    "Delete" delegates to :func:`delete_question_from_open_exam`; success,
    no-op and error paths each surface through ``ui.notify`` with an
    appropriate colour. On any outcome that changed disk state we refresh
    the Designer body so the list and header count stay in sync.
    """

    with ui.dialog() as dialog, ui.card():
        ui.label("Delete question?").classes("text-h6")
        ui.label(question.id).classes("text-caption text-grey")
        ui.label(_truncate_question_text(question.text)).classes("text-body2")

        def _confirm() -> None:
            try:
                removed = delete_question_from_open_exam(question.id)
            except sqlite3.DatabaseError as exc:
                ui.notify(f"Cannot delete question: {exc}", type="negative")
                dialog.close()
                return

            dialog.close()

            if removed is None:
                ui.notify("No exam is open.", type="warning")
                return

            if not removed:
                ui.notify(
                    f"Question {question.id} no longer exists.", type="warning"
                )
                _render_designer_body.refresh()
                return

            ui.notify(f"Question {question.id} deleted.")
            _render_designer_body.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=_confirm).props("color=negative")

    dialog.open()


def _handle_export_exam_click() -> None:
    """Export the currently opened exam to a JSON bundle and notify.

    Thin UI wrapper around :func:`export_open_exam_to_json`. Success
    surfaces as a positive toast with the absolute export path so the
    user can locate the file; no refresh is needed because the exam
    itself is unchanged. The three outcomes mirror the standard notify
    matrix:

    * ``Path`` (success) → positive toast with the filename.
    * ``None`` → warning "no exam open".
    * ``LookupError`` / ``sqlite3.DatabaseError`` / ``OSError`` → negative
      toast with the error detail.
    """

    try:
        exported = export_open_exam_to_json()
    except (LookupError, sqlite3.DatabaseError, OSError) as exc:
        ui.notify(f"Cannot export: {exc}", type="negative")
        return

    if exported is None:
        ui.notify("No exam is open.", type="warning")
        return

    ui.notify(f"Exam exported: {exported}")


def _handle_move_question_click(question: Question, direction: str) -> None:
    """Move ``question`` up or down in the opened exam and refresh the UI.

    Thin UI wrapper around :func:`move_question_in_open_exam`. Success,
    no-op (edge row) and error paths each surface through ``ui.notify``
    with an appropriate colour so the user always sees what happened:

    * ``True`` → positive toast + refresh.
    * ``False`` → neutral info toast (already at edge), no refresh needed
      because the list order hasn't changed.
    * ``None`` → warning toast ("no exam open").
    * Any ``ValueError`` / ``sqlite3.DatabaseError`` surfaces as a
      negative toast; the Designer body still refreshes so the list
      re-reads the on-disk truth and self-heals from any stale state.
    """

    try:
        changed = move_question_in_open_exam(question.id, direction)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot move question: {exc}", type="negative")
        _render_designer_body.refresh()
        return

    if changed is None:
        ui.notify("No exam is open.", type="warning")
        return

    if not changed:
        label = "at the top" if direction == MOVE_UP else "at the bottom"
        ui.notify(f"Question {question.id} is already {label}.", type="info")
        return

    arrow = "up" if direction == MOVE_UP else "down"
    ui.notify(f"Question {question.id} moved {arrow}.")
    _render_designer_body.refresh()


def _render_open_exam_status() -> None:
    """Render the "currently opened exam" banner if user has one stashed.


    Besides the header metadata (name / description / path / question count)
    the card also hosts the list of questions for the opened exam — fresh
    from disk via :func:`load_questions_for_open_exam`, so edits made in
    other tabs or via raw SQL are picked up on the next refresh.
    """

    summary = app.storage.user.get(OPEN_EXAM_STORAGE_KEY)
    if not summary:
        return

    with ui.card().classes("q-mb-md w-full").props("bordered"):
        ui.label("Open exam").classes("text-subtitle2")
        ui.label(
            f"{summary.get('name')} ({summary.get('question_count')} questions)"
        ).classes("text-body1")
        description = summary.get("description")
        if description:
            ui.label(str(description)).classes("text-caption")
        ui.label(str(summary.get("path"))).classes("text-caption text-grey")

        with ui.row().classes("items-center q-gutter-sm q-mt-sm"):
            ui.button(
                "Export JSON", on_click=_handle_export_exam_click
            ).props("size=sm color=secondary")

        ui.separator().classes("q-my-sm")
        with ui.row().classes("items-center q-gutter-sm"):
            ui.label("Questions").classes("text-subtitle2")
            ui.button(
                "Add question", on_click=_handle_add_question_click
            ).props("size=sm color=primary")

        # Client-side search filter (step 4.7). We bind the input to
        # ``app.storage.user`` so the query survives Designer refreshes
        # triggered by add/delete/move. ``on_change`` refreshes the body
        # so the filtered list re-renders live; it's cheap because the
        # questions are already in memory for the current render pass.
        app.storage.user.setdefault(SEARCH_QUERY_STORAGE_KEY, "")
        ui.input(
            "Search",
            placeholder="Filter by id or question text",
            on_change=lambda _evt: _render_designer_body.refresh(),
        ).bind_value(app.storage.user, SEARCH_QUERY_STORAGE_KEY).props(
            "clearable dense"
        ).classes("w-full q-mt-sm")

        query = str(app.storage.user.get(SEARCH_QUERY_STORAGE_KEY, ""))
        _render_question_list(load_questions_for_open_exam(), query)



@ui.refreshable
def _render_designer_body() -> None:
    """Render the Designer body — now delegates to the 3-panel layout.

    Kept as a :func:`ui.refreshable` shim so the dozens of existing
    per-field dialog handlers (``_handle_edit_*_click`` etc.) that
    already call ``_render_designer_body.refresh()`` keep working
    without a global find-and-replace. The actual layout now lives in
    :mod:`posrat.designer.layout` and is assembled from three dedicated
    panels (Explorer / Properties / Editor) that each own their own
    refresh path.
    """

    # Lazy import to break the ``browser → layout → (explorer / properties
    # / editor) → browser`` circular dependency. The layout module needs
    # to import dialog handlers from this file, so a top-level import
    # here would deadlock the module system.
    from posrat.designer.layout import render_designer_layout

    render_designer_layout()


def render_designer() -> None:
    """Render the Designer page body (public entry point).

    Since the Phase-R refactor the Designer is a 3-panel layout
    (Explorer + Properties + Main editor) assembled by
    :func:`posrat.designer.layout.render_designer_layout`. This
    wrapper only exists to provide a stable import path for
    :mod:`posrat.app` and to preserve the ``ui.refreshable`` shim
    (``_render_designer_body``) that legacy dialog handlers call into.
    """

    _render_designer_body()


