"""Runner picker — list of runnable exams shown on the ``/runner`` landing page.

The Runner has to walk the same ``data/*.sqlite`` directory the
Designer already uses, but it needs a *read-only* summary of each exam
with enough metadata for the "Start" button (name, description, question
count, default question count, timer, passing score). We build a
dedicated :class:`RunnerExamSummary` dataclass rather than reusing
:class:`posrat.models.Exam` because loading the full question payload
for every exam on the picker page would be wasteful — the Runner
picker only needs header fields.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from posrat.designer.browser import (
    EXAM_FILE_SUFFIX,
    list_exam_files,
)
from posrat.storage import open_db


@dataclass(frozen=True)
class RunnerExamSummary:
    """Lightweight read-only snapshot of an exam for the Runner picker.

    Mirrors the columns of the ``exams`` table plus a computed
    ``question_count``. The Runner never mutates these — every edit
    flows through the Designer — so the class is frozen to guard
    against accidental in-place modification in the picker's UI layer.
    """

    path: Path
    exam_id: str
    name: str
    description: Optional[str]
    question_count: int
    default_question_count: Optional[int]
    time_limit_minutes: Optional[int]
    passing_score: Optional[int]
    target_score: Optional[int]


def summarise_runnable_exam(path: Path) -> Optional[RunnerExamSummary]:
    """Return a :class:`RunnerExamSummary` for the ``.sqlite`` at ``path``.

    Returns ``None`` when the file is not a POSRAT exam database (no
    ``exams`` table, or the table is empty). We silently skip such
    files rather than raising because the data directory may contain
    in-progress uploads, stray backups or other SQLite files that the
    Runner has no business rendering. The Designer is still the authority
    for diagnosing invalid exam files.

    Reads are wrapped in a fresh :class:`sqlite3.Connection` closed in
    a ``finally`` block so the helper is safe to call hundreds of
    times on a busy picker page.
    """

    try:
        db = open_db(path)
    except sqlite3.DatabaseError:
        return None

    try:
        try:
            exam_row = db.execute(
                "SELECT id, name, description, default_question_count,"
                " time_limit_minutes, passing_score, target_score"
                " FROM exams LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            # No ``exams`` table — this isn't a POSRAT file.
            return None

        if exam_row is None:
            return None

        (question_count,) = db.execute(
            "SELECT COUNT(*) FROM questions WHERE exam_id = ?",
            (exam_row["id"],),
        ).fetchone()

        return RunnerExamSummary(
            path=path,
            exam_id=str(exam_row["id"]),
            name=str(exam_row["name"]),
            description=(
                str(exam_row["description"])
                if exam_row["description"] is not None
                else None
            ),
            question_count=int(question_count),
            default_question_count=exam_row["default_question_count"],
            time_limit_minutes=exam_row["time_limit_minutes"],
            passing_score=exam_row["passing_score"],
            target_score=exam_row["target_score"],
        )
    finally:
        db.close()


def list_runnable_exams(data_dir: Path) -> list[RunnerExamSummary]:
    """Return a sorted list of exam summaries found under ``data_dir``.

    Walks every ``*.sqlite`` file via
    :func:`posrat.designer.browser.list_exam_files` and summarises
    each one via :func:`summarise_runnable_exam`. Files that fail
    to summarise (invalid DB, no exam row, etc.) are silently omitted
    — the Runner is read-only from the picker's point of view.

    The resulting list is sorted by exam ``name`` (case-insensitive)
    so the UI has a human-friendly order regardless of filename.
    """

    _ = EXAM_FILE_SUFFIX  # ensure the constant stays in the import graph
    summaries: list[RunnerExamSummary] = []
    for path in list_exam_files(data_dir):
        summary = summarise_runnable_exam(path)
        if summary is not None:
            summaries.append(summary)

    summaries.sort(key=lambda s: s.name.casefold())
    return summaries
