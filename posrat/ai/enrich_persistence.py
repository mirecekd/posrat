"""Disk-side helpers for the bulk Auto-enrich CLI.

Three responsibilities:

1. Make a timestamped backup of the exam ``.sqlite`` before any
   write happens. Operator decision (2026-05-19): keep history of
   every run via ``<exam>.bak-<timestamp>.sqlite`` so a botched
   enrichment can be reverted to *any* prior state, not just the
   last one.

2. Load the single exam id stored in the per-exam DB (POSRAT keeps
   one exam per file by convention) plus the questions list.

3. Persist a finished :class:`~posrat.ai.enrich.EnrichResult` back
   to disk:

   * Replace ``explanation`` with the AI reply (already includes the
     community-vote summary trailer when applicable).
   * When the verdict is ``"mismatch"`` and ``auto_correct`` is on,
     rewrite the ``is_correct`` flags on the question's choices
     using the AI's letters. Choice texts and IDs stay byte-for-byte;
     only the boolean flags flip.

The Designer's :func:`posrat.designer.browser.update_question_explanation_in_file`
already implements (3a), so we delegate to it. (3b) gets a new
focused helper here because the existing
``replace_question_choices_in_file`` would rewrite *all* choice rows
(re-shuffling ids), which loses the connection between choice texts
and their letter positions during a partial mismatch update.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from posrat.ai.enrich import EnrichResult
from posrat.designer.browser import (
    load_questions_from_file,
    update_question_explanation_in_file,
)
from posrat.models import Question


def _format_backup_timestamp(now: Optional[datetime] = None) -> str:
    """Return ``YYYYMMDD-HHMMSS`` UTC timestamp for backup filenames.

    Mirrors the export-bundle timestamp convention from
    :func:`posrat.designer.browser._format_export_timestamp` so
    operators don't have to context-switch between two formats.
    """

    if now is None:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S")


def make_backup(
    exam_path: Path,
    *,
    now: Optional[datetime] = None,
) -> Path:
    """Copy ``exam_path`` to ``<stem>.bak-<timestamp>.sqlite`` and return the path.

    Uses :func:`shutil.copy2` so file metadata (mtime, permissions)
    propagates — the operator can ``ls -la`` to see when each backup
    was actually taken.

    Raises :class:`FileNotFoundError` when the source file is
    missing, :class:`FileExistsError` when the (sub-second-precision)
    timestamped target somehow already exists. The latter is purely
    defensive: two backups within the same second of the same exam
    is virtually impossible in practice.
    """

    if not exam_path.exists():
        raise FileNotFoundError(f"exam file not found: {exam_path}")
    timestamp = _format_backup_timestamp(now)
    backup_path = exam_path.with_name(
        f"{exam_path.stem}.bak-{timestamp}{exam_path.suffix}"
    )
    if backup_path.exists():
        raise FileExistsError(
            f"backup already exists: {backup_path}; wait a second and retry"
        )
    shutil.copy2(exam_path, backup_path)
    return backup_path


def resolve_exam_id(exam_path: Path) -> str:
    """Return the single exam id stored in ``exam_path``.

    Mirrors the lookup in
    :func:`posrat.designer.browser.open_exam_from_file` — POSRAT puts
    exactly one row in the ``exams`` table per ``.sqlite`` file. Raises
    :class:`ValueError` when the table is empty so the CLI can refuse
    to keep going on a corrupt / wrong file.
    """

    db = sqlite3.connect(exam_path)
    try:
        row = db.execute("SELECT id FROM exams LIMIT 1").fetchone()
    finally:
        db.close()
    if row is None:
        raise ValueError(f"exam DB has no exam row: {exam_path}")
    return str(row[0])


def load_exam_questions(exam_path: Path) -> List[Question]:
    """Return all questions in ``exam_path`` ordered by ``order_index``.

    Convenience wrapper that combines :func:`resolve_exam_id` with
    :func:`posrat.designer.browser.load_questions_from_file`. Keeps
    the CLI dispatcher free of per-step open / close boilerplate.
    """

    exam_id = resolve_exam_id(exam_path)
    return load_questions_from_file(exam_path, exam_id)


def update_choice_correctness_in_file(
    exam_path: Path,
    question_id: str,
    correct_letters: list[str],
) -> bool:
    """Re-flag ``is_correct`` on ``question_id``'s choices by letter.

    ``correct_letters`` is the list parsed from the AI reply
    (``["B"]`` for single, ``["B","D"]`` for multi). Choice text and
    id are preserved byte-for-byte; only ``choices.is_correct``
    flips (1 for letters in ``correct_letters``, 0 otherwise).

    Returns ``True`` when at least one ``UPDATE`` actually wrote a
    different value, ``False`` when the question id is unknown or
    the new flags exactly match the existing ones (idempotent
    no-op). Raises :class:`ValueError` when ``correct_letters`` is
    empty (caller must ensure non-empty before calling — the bulk
    runner has already classified the verdict).
    """

    if not correct_letters:
        raise ValueError(
            "correct_letters must not be empty; "
            "guard with classify_verdict()"
        )
    target = {ltr.upper() for ltr in correct_letters}

    db = sqlite3.connect(exam_path)
    try:
        rows = db.execute(
            "SELECT id FROM questions WHERE id = ?", (question_id,)
        ).fetchall()
        if not rows:
            return False

        # Ordered by ROWID — same insertion order ``add_question``
        # uses to guarantee letter assignment matches the
        # Designer / context-render letter scheme.
        choice_rows = db.execute(
            "SELECT id, is_correct FROM choices WHERE question_id = ?"
            " ORDER BY ROWID ASC",
            (question_id,),
        ).fetchall()

        any_change = False
        with db:
            for idx, (choice_id, current_flag) in enumerate(choice_rows):
                if idx >= 26:  # pragma: no cover - Question caps choices
                    continue
                letter = chr(ord("A") + idx)
                desired = 1 if letter in target else 0
                if int(current_flag) == desired:
                    continue
                db.execute(
                    "UPDATE choices SET is_correct = ? WHERE id = ?",
                    (desired, choice_id),
                )
                any_change = True
        return any_change
    finally:
        db.close()


def persist_enrich_result(
    exam_path: Path,
    result: EnrichResult,
    *,
    auto_correct_mismatches: bool,
) -> None:
    """Write ``result`` back to ``exam_path``.

    The CLI builds an ``async`` wrapper around this so the runner's
    ``persist`` callback can ``await`` it; the disk side itself is
    sync. Decisions:

    * Skipped / errored / unknown / no-explanation rows are no-ops
      (nothing to write). The CLI report still surfaces them.
    * Match + mismatch results have their ``explanation`` field
      replaced wholesale.
    * Mismatch results additionally re-flag ``is_correct`` when
      ``auto_correct_mismatches`` is ``True`` (default ON per the
      operator's request — "chci s tim mit co nejmene prace").
    """

    if result.new_explanation is None:
        return
    if result.verdict in {
        "skipped_hotspot",
        "skipped_already_enriched",
        "error",
    }:
        return

    update_question_explanation_in_file(
        exam_path, result.question_id, result.new_explanation
    )

    if (
        auto_correct_mismatches
        and result.verdict == "mismatch"
        and result.ai_letters
    ):
        update_choice_correctness_in_file(
            exam_path, result.question_id, result.ai_letters
        )


__all__ = [
    "load_exam_questions",
    "make_backup",
    "persist_enrich_result",
    "resolve_exam_id",
    "update_choice_correctness_in_file",
]
