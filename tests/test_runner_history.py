"""Tests for :mod:`posrat.runner.history`.

Scope:

* :func:`list_session_results` walks a data directory, flattens all
  sessions from every exam, sorts newest-first, and attaches a
  computed :class:`SessionScore` to each row.
* Non-POSRAT files (no ``exams`` table, empty DB) are silently
  skipped — the picker never crashes on a stray file.
* In-progress sessions (no ``finished_at``) surface via
  :attr:`SessionResultSummary.is_finished` as ``False``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from posrat.models import Choice, Exam, Question
from posrat.runner.history import (
    SessionResultSummary,
    list_session_results,
)
from posrat.storage import (
    create_exam,
    finish_session,
    open_db,
    record_answer,
    start_session,
)


def _seed_exam(path: Path, *, exam_id: str, name: str) -> None:
    db = open_db(path)
    try:
        create_exam(
            db,
            Exam(
                id=exam_id,
                name=name,
                questions=[
                    Question(
                        id=f"{exam_id}-q1",
                        type="single_choice",
                        text="Q1",
                        choices=[
                            Choice(
                                id=f"{exam_id}-q1-a",
                                text="A",
                                is_correct=True,
                            ),
                            Choice(
                                id=f"{exam_id}-q1-b",
                                text="B",
                                is_correct=False,
                            ),
                        ],
                    )
                ],
                passing_score=700,
                target_score=1000,
            ),
        )
    finally:
        db.close()


def test_list_session_results_returns_empty_for_empty_dir(tmp_path) -> None:
    """No .sqlite files → empty list, no errors."""

    assert list_session_results(tmp_path) == []


def test_list_session_results_skips_non_posrat_sqlite(tmp_path) -> None:
    """Stray SQLite DBs without ``exams`` table are silently skipped."""

    random_db = tmp_path / "random.sqlite"
    db = sqlite3.connect(random_db)
    try:
        db.execute("CREATE TABLE notes (id INTEGER)")
        db.commit()
    finally:
        db.close()

    assert list_session_results(tmp_path) == []


def test_list_session_results_flattens_and_sorts_newest_first(tmp_path) -> None:
    """Sessions from multiple exam files sorted by ``started_at`` DESC."""

    path_a = tmp_path / "a.sqlite"
    path_b = tmp_path / "b.sqlite"
    _seed_exam(path_a, exam_id="a", name="A Exam")
    _seed_exam(path_b, exam_id="b", name="B Exam")

    db_a = open_db(path_a)
    try:
        start_session(
            db_a,
            exam_id="a",
            mode="training",
            session_id="s-old",
            started_at="2026-04-20T10:00:00Z",
            candidate_name="Alice",
            question_count=1,
            passing_score=700,
            target_score=1000,
        )
        finish_session(db_a, "s-old", finished_at="2026-04-20T10:30:00Z")
        record_answer(
            db_a,
            session_id="s-old",
            question_id="a-q1",
            given_json='{"choice_id":"a-q1-a"}',
            is_correct=True,
        )
    finally:
        db_a.close()

    db_b = open_db(path_b)
    try:
        start_session(
            db_b,
            exam_id="b",
            mode="exam",
            session_id="s-new",
            started_at="2026-04-23T12:00:00Z",
            candidate_name="Bob",
            question_count=1,
            passing_score=700,
            target_score=1000,
        )
        # No finish_session — session is in progress.
        record_answer(
            db_b,
            session_id="s-new",
            question_id="b-q1",
            given_json='{"choice_id":"b-q1-b"}',
            is_correct=False,
        )
    finally:
        db_b.close()

    results = list_session_results(tmp_path)
    assert [r.session_id for r in results] == ["s-new", "s-old"]

    new_summary = results[0]
    assert isinstance(new_summary, SessionResultSummary)
    assert new_summary.exam_name == "B Exam"
    assert new_summary.candidate_name == "Bob"
    assert new_summary.is_finished is False
    # Answered q-1 wrong → 0 correct out of 1 snapshot → 0 raw_score
    # of target_score=1000 → passed=False.
    assert new_summary.score.correct_count == 0
    assert new_summary.score.total_count == 1
    assert new_summary.score.passed is False

    old_summary = results[1]
    assert old_summary.exam_name == "A Exam"
    assert old_summary.candidate_name == "Alice"
    assert old_summary.is_finished is True
    # 1 correct / 1 total = 100 % → raw_score 1000 → passed True.
    assert old_summary.score.correct_count == 1
    assert old_summary.score.passed is True
