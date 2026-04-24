"""Tests for :mod:`posrat.runner.session_detail`.

Covers the happy path (one answered question hydrated into a
:class:`QuestionReview`), unanswered-question accounting (snapshot
denominator minus persisted rows), and defensive fall-backs for
missing exam files / absent sessions / malformed ``given_json``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from posrat.models import Choice, Exam, Question
from posrat.runner.session_detail import (
    QuestionReview,
    SessionDetail,
    load_session_detail,
)
from posrat.storage import (
    create_exam,
    finish_session,
    open_db,
    record_answer,
    start_session,
)


def _seed_exam_with_two_questions(path: Path) -> None:
    """Seed a minimal exam: one single_choice + one multi_choice."""

    db = open_db(path)
    try:
        create_exam(
            db,
            Exam(
                id="e1",
                name="E1 Exam",
                questions=[
                    Question(
                        id="q1",
                        type="single_choice",
                        text="Q1 text",
                        explanation="Correct is A",
                        choices=[
                            Choice(id="q1-a", text="A", is_correct=True),
                            Choice(id="q1-b", text="B", is_correct=False),
                        ],
                    ),
                    Question(
                        id="q2",
                        type="multi_choice",
                        text="Q2 text",
                        choices=[
                            Choice(id="q2-a", text="A", is_correct=True),
                            Choice(id="q2-b", text="B", is_correct=True),
                            Choice(id="q2-c", text="C", is_correct=False),
                        ],
                    ),
                ],
                passing_score=700,
                target_score=1000,
            ),
        )
    finally:
        db.close()


def test_load_session_detail_returns_none_for_missing_file(tmp_path) -> None:
    """Non-existent exam file → ``None`` rather than an exception."""

    assert load_session_detail(tmp_path / "ghost.sqlite", "any") is None


def test_load_session_detail_returns_none_for_stray_sqlite(tmp_path) -> None:
    """SQLite without an ``exams`` row → ``None`` (stray file guard)."""

    random_db = tmp_path / "random.sqlite"
    db = sqlite3.connect(random_db)
    try:
        db.execute("CREATE TABLE notes (id INTEGER)")
        db.commit()
    finally:
        db.close()

    assert load_session_detail(random_db, "any") is None


def test_load_session_detail_returns_none_for_unknown_session(tmp_path) -> None:
    """Unknown session id → ``None`` — caller renders a friendly error."""

    path = tmp_path / "e1.sqlite"
    _seed_exam_with_two_questions(path)
    assert load_session_detail(path, "missing") is None


def test_load_session_detail_hydrates_each_answer_into_review(tmp_path) -> None:
    """Happy path: per-answer :class:`QuestionReview` list in submit order."""

    path = tmp_path / "e1.sqlite"
    _seed_exam_with_two_questions(path)

    db = open_db(path)
    try:
        start_session(
            db,
            exam_id="e1",
            mode="training",
            session_id="s1",
            started_at="2026-04-23T10:00:00Z",
            candidate_name="Alice",
            question_count=2,
            passing_score=700,
            target_score=1000,
        )
        # q1 answered correctly.
        record_answer(
            db,
            session_id="s1",
            question_id="q1",
            given_json='{"choice_id":"q1-a"}',
            is_correct=True,
        )
        # q2 answered wrong (missing q2-b from the set).
        record_answer(
            db,
            session_id="s1",
            question_id="q2",
            given_json='{"choice_ids":["q2-a"]}',
            is_correct=False,
        )
        finish_session(db, "s1", finished_at="2026-04-23T10:30:00Z")
    finally:
        db.close()

    detail = load_session_detail(path, "s1")
    assert isinstance(detail, SessionDetail)
    assert detail.exam_name == "E1 Exam"
    assert detail.session.candidate_name == "Alice"
    assert detail.unanswered_count == 0
    assert detail.score.correct_count == 1
    assert detail.score.total_count == 2

    assert len(detail.reviews) == 2
    r1, r2 = detail.reviews
    assert isinstance(r1, QuestionReview)
    assert r1.index == 1
    assert r1.question.id == "q1"
    assert r1.is_correct is True
    assert r1.was_answered is True
    assert r1.given_payload == {"choice_id": "q1-a"}

    assert r2.index == 2
    assert r2.question.id == "q2"
    assert r2.is_correct is False
    assert r2.given_payload == {"choice_ids": ["q2-a"]}


def test_load_session_detail_counts_unanswered_from_snapshot(tmp_path) -> None:
    """``question_count - len(answers)`` surfaces as ``unanswered_count``."""

    path = tmp_path / "e1.sqlite"
    _seed_exam_with_two_questions(path)

    db = open_db(path)
    try:
        start_session(
            db,
            exam_id="e1",
            mode="exam",
            session_id="s-partial",
            started_at="2026-04-23T10:00:00Z",
            candidate_name="Bob",
            question_count=5,  # bigger than actual answer count
        )
        record_answer(
            db,
            session_id="s-partial",
            question_id="q1",
            given_json='{"choice_id":"q1-a"}',
            is_correct=True,
        )
    finally:
        db.close()

    detail = load_session_detail(path, "s-partial")
    assert detail is not None
    assert len(detail.reviews) == 1
    # snapshot said 5, only 1 recorded → 4 unanswered
    assert detail.unanswered_count == 4


def test_decode_payload_safely_returns_none_for_bad_json() -> None:
    """Defensive decoder: malformed JSON → ``None`` rather than ``ValueError``.

    The :class:`~posrat.models.Answer` validator already rejects
    invalid JSON at load time, so reaching this branch requires a
    future loosening of that contract (or a direct caller bypassing
    the model). The guard stays in :func:`load_session_detail` as a
    belt-and-suspenders safety net — this test locks its behaviour
    in place so it keeps working should the model contract change.
    """

    from posrat.runner.session_detail import _decode_payload_safely

    assert _decode_payload_safely("not-json{") is None
    assert _decode_payload_safely('{"choice_id":"x"}') == {"choice_id": "x"}
