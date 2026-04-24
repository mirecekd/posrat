"""Tests for :mod:`posrat.runner.picker`.

Scope:

* :func:`summarise_runnable_exam` returns a full ``RunnerExamSummary``
  for a valid POSRAT DB, including the new Phase 7A metadata fields.
* :func:`summarise_runnable_exam` returns ``None`` for non-POSRAT
  ``.sqlite`` files (no ``exams`` table) or for empty exam tables,
  without raising.
* :func:`list_runnable_exams` walks a data directory, skips unusable
  files and sorts the result alphabetically.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from posrat.designer.browser import create_exam_file
from posrat.models import Choice, Exam, Question
from posrat.runner.picker import (
    RunnerExamSummary,
    list_runnable_exams,
    summarise_runnable_exam,
)
from posrat.storage import create_exam, open_db


def _seed_full_exam(data_dir: Path, exam: Exam) -> Path:
    """Write ``exam`` into ``<data_dir>/<exam.id>.sqlite`` and close the DB."""

    path = data_dir / f"{exam.id}.sqlite"
    db = open_db(path)
    try:
        create_exam(db, exam)
    finally:
        db.close()
    return path


def _basic_question(qid: str = "q-1") -> Question:
    """Return a minimal valid single_choice question for fixture seeding."""

    return Question(
        id=qid,
        type="single_choice",
        text="Pick one",
        choices=[
            Choice(id=f"{qid}-a", text="A", is_correct=True),
            Choice(id=f"{qid}-b", text="B", is_correct=False),
        ],
    )


def test_summarise_runnable_exam_reads_metadata(tmp_path) -> None:
    """Valid POSRAT DB → complete :class:`RunnerExamSummary`."""

    exam = Exam(
        id="aif-c01",
        name="AIF-C01",
        description="AWS AI Practitioner",
        questions=[_basic_question("q-1"), _basic_question("q-2")],
        default_question_count=65,
        time_limit_minutes=90,
        passing_score=700,
        target_score=1000,
    )
    path = _seed_full_exam(tmp_path, exam)

    summary = summarise_runnable_exam(path)
    assert summary is not None
    assert summary.path == path
    assert summary.exam_id == "aif-c01"
    assert summary.name == "AIF-C01"
    assert summary.description == "AWS AI Practitioner"
    assert summary.question_count == 2
    assert summary.default_question_count == 65
    assert summary.time_limit_minutes == 90
    assert summary.passing_score == 700
    assert summary.target_score == 1000


def test_summarise_runnable_exam_handles_metadata_none(tmp_path) -> None:
    """Exam without 7A metadata → fields surface as ``None``."""

    path = create_exam_file(tmp_path, "legacy", "Legacy")

    summary = summarise_runnable_exam(path)
    assert summary is not None
    assert summary.default_question_count is None
    assert summary.time_limit_minutes is None
    assert summary.passing_score is None
    assert summary.target_score is None
    assert summary.question_count == 0


def test_summarise_runnable_exam_returns_none_for_non_posrat_sqlite(
    tmp_path,
) -> None:
    """A plain SQLite file with no ``exams`` table must be skipped."""

    path = tmp_path / "random.sqlite"
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        db.commit()
    finally:
        db.close()

    assert summarise_runnable_exam(path) is None


def test_list_runnable_exams_returns_sorted_summaries(tmp_path) -> None:
    """Picker list is alphabetical by display name, case-insensitive."""

    _seed_full_exam(
        tmp_path,
        Exam(id="beta", name="Beta exam", questions=[]),
    )
    _seed_full_exam(
        tmp_path,
        Exam(id="alpha", name="alpha exam", questions=[]),
    )
    # A non-POSRAT sibling that must be skipped.
    random_db = tmp_path / "garbage.sqlite"
    conn = sqlite3.connect(random_db)
    try:
        conn.execute("CREATE TABLE foo (x INT)")
    finally:
        conn.close()

    summaries = list_runnable_exams(tmp_path)
    names = [s.name for s in summaries]
    assert names == ["alpha exam", "Beta exam"]
    # Type-guard: every entry is a frozen dataclass instance.
    assert all(isinstance(s, RunnerExamSummary) for s in summaries)


def test_list_runnable_exams_handles_missing_data_dir(tmp_path) -> None:
    """Non-existent data dir returns an empty list, not an error."""

    missing = tmp_path / "does-not-exist"
    assert list_runnable_exams(missing) == []
