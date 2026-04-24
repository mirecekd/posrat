"""Tests for :mod:`posrat.storage.session_repo` (step 2.5.3b)."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from posrat.models import Choice, Exam, Question
from posrat.storage import (
    create_exam,
    finish_session,
    get_session,
    list_sessions,
    open_db,
    record_answer,
    start_session,
)


def _build_exam(exam_id: str = "exam-1") -> Exam:
    """Minimal exam with one single_choice question for FK targets."""
    return Exam(
        id=exam_id,
        name="Sample",
        description=None,
        questions=[
            Question(
                id="q-1",
                type="single_choice",
                text="What?",
                explanation=None,
                choices=[
                    Choice(id="q-1-c-a", text="A", is_correct=True),
                    Choice(id="q-1-c-b", text="B", is_correct=False),
                ],
            ),
        ],
    )


def _fresh_db(tmp_path) -> sqlite3.Connection:
    return open_db(tmp_path / "exam.sqlite")


def test_start_session_persists_a_new_session(tmp_path) -> None:
    """start_session inserts a row and returns the Session model."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        session = start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )
        assert session.id == "s-1"
        assert session.exam_id == "exam-1"
        assert session.mode == "training"
        assert session.finished_at is None
        assert session.answers == []

        (count,) = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert count == 1
    finally:
        db.close()


def test_start_session_raises_for_unknown_exam(tmp_path) -> None:
    """Missing exam must be reported clearly (LookupError)."""
    db = _fresh_db(tmp_path)
    try:
        with pytest.raises(LookupError):
            start_session(db, exam_id="missing", mode="exam")
    finally:
        db.close()


def test_finish_session_sets_finished_at(tmp_path) -> None:
    """finish_session writes the supplied timestamp."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        start_session(
            db,
            exam_id="exam-1",
            mode="exam",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )
        finish_session(
            db, session_id="s-1", finished_at="2025-01-01T00:30:00Z"
        )

        reloaded = get_session(db, "s-1")
        assert reloaded is not None
        assert reloaded.finished_at == "2025-01-01T00:30:00Z"
    finally:
        db.close()


def test_finish_session_raises_for_unknown_session(tmp_path) -> None:
    """finish_session on a missing id must raise LookupError."""
    db = _fresh_db(tmp_path)
    try:
        with pytest.raises(LookupError):
            finish_session(db, session_id="nope")
    finally:
        db.close()


def test_get_session_returns_none_when_missing(tmp_path) -> None:
    """get_session on a missing id must return None, not raise."""
    db = _fresh_db(tmp_path)
    try:
        assert get_session(db, "nope") is None
    finally:
        db.close()


def test_record_answer_persists_and_roundtrips(tmp_path) -> None:
    """record_answer writes a row retrievable via get_session."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )

        answer = record_answer(
            db,
            session_id="s-1",
            question_id="q-1",
            given_json='{"choice_id":"q-1-c-a"}',
            is_correct=True,
            time_ms=1500,
            answer_id="a-1",
        )
        assert answer.id == "a-1"
        assert answer.is_correct is True

        reloaded = get_session(db, "s-1")
        assert reloaded is not None
        assert len(reloaded.answers) == 1
        stored = reloaded.answers[0]
        assert stored.question_id == "q-1"
        assert stored.given_json == '{"choice_id":"q-1-c-a"}'
        assert stored.is_correct is True
        assert stored.time_ms == 1500
    finally:
        db.close()


def test_record_answer_rejects_malformed_given_json(tmp_path) -> None:
    """Invalid JSON must fail fast at model layer (no DB write)."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )
        with pytest.raises(ValidationError):
            record_answer(
                db,
                session_id="s-1",
                question_id="q-1",
                given_json="{not-json",
                is_correct=False,
            )

        (count,) = db.execute("SELECT COUNT(*) FROM answers").fetchone()
        assert count == 0
    finally:
        db.close()


def test_record_answer_raises_for_unknown_session_or_question(tmp_path) -> None:
    """Missing session or question must be reported as LookupError."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        with pytest.raises(LookupError):
            record_answer(
                db,
                session_id="missing",
                question_id="q-1",
                given_json="{}",
                is_correct=False,
            )

        start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )
        with pytest.raises(LookupError):
            record_answer(
                db,
                session_id="s-1",
                question_id="missing",
                given_json="{}",
                is_correct=False,
            )
    finally:
        db.close()


def test_list_sessions_returns_ordered_sessions_with_answers(tmp_path) -> None:
    """list_sessions sorts by started_at ASC and loads answers eagerly."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())

        start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-later",
            started_at="2025-01-02T00:00:00Z",
        )
        start_session(
            db,
            exam_id="exam-1",
            mode="exam",
            session_id="s-first",
            started_at="2025-01-01T00:00:00Z",
        )

        record_answer(
            db,
            session_id="s-first",
            question_id="q-1",
            given_json='{"choice_id":"q-1-c-a"}',
            is_correct=True,
            answer_id="a-1",
        )

        sessions = list_sessions(db, "exam-1")
        assert [s.id for s in sessions] == ["s-first", "s-later"]
        assert sessions[0].mode == "exam"
        assert len(sessions[0].answers) == 1
        assert sessions[0].answers[0].id == "a-1"
        assert sessions[1].answers == []
    finally:
        db.close()


def test_list_sessions_returns_empty_for_unknown_exam(tmp_path) -> None:
    """list_sessions for a missing exam must return an empty list."""
    db = _fresh_db(tmp_path)
    try:
        assert list_sessions(db, "nope") == []
    finally:
        db.close()


def test_sessions_cascade_when_exam_is_deleted(tmp_path) -> None:
    """Deleting an exam removes its sessions (and their answers)."""
    db = _fresh_db(tmp_path)
    try:
        create_exam(db, _build_exam())
        start_session(
            db,
            exam_id="exam-1",
            mode="training",
            session_id="s-1",
            started_at="2025-01-01T00:00:00Z",
        )
        record_answer(
            db,
            session_id="s-1",
            question_id="q-1",
            given_json='{"choice_id":"q-1-c-a"}',
            is_correct=True,
            answer_id="a-1",
        )

        db.execute("DELETE FROM exams WHERE id = ?", ("exam-1",))
        db.commit()

        (sess_count,) = db.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()
        (ans_count,) = db.execute("SELECT COUNT(*) FROM answers").fetchone()
        assert sess_count == 0
        assert ans_count == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Phase 7B — Session snapshot metadata round-trip                             #
# --------------------------------------------------------------------------- #


def test_start_session_persists_snapshot_metadata(tmp_path) -> None:
    """``start_session`` must store the five Phase 7B snapshot fields."""

    from posrat.storage import create_exam, get_session, open_db, start_session
    from posrat.models import Exam

    db = open_db(tmp_path / "snapshot.sqlite")
    try:
        create_exam(db, Exam(id="e1", name="E1"))
        session = start_session(
            db,
            exam_id="e1",
            mode="exam",
            session_id="s-snapshot",
            started_at="2026-04-23T10:00:00Z",
            candidate_name="Alice",
            question_count=65,
            time_limit_minutes=90,
            passing_score=700,
            target_score=1000,
        )
        assert session.candidate_name == "Alice"
        assert session.question_count == 65
        assert session.time_limit_minutes == 90
        assert session.passing_score == 700
        assert session.target_score == 1000

        # Fresh get_session round-trip (not just the in-memory object).
        reloaded = get_session(db, "s-snapshot")
        assert reloaded is not None
        assert reloaded.candidate_name == "Alice"
        assert reloaded.question_count == 65
        assert reloaded.time_limit_minutes == 90
        assert reloaded.passing_score == 700
        assert reloaded.target_score == 1000
    finally:
        db.close()


def test_start_session_without_snapshot_persists_nulls(tmp_path) -> None:
    """Legacy call-pattern (no snapshot kwargs) must persist ``NULL`` fields.

    Guarantees backward compatibility with the pre-7B tests in this
    file: calling ``start_session(db, exam_id, mode)`` without any of
    the Runner snapshot kwargs must keep working and load all five
    snapshot columns as ``None``.
    """

    from posrat.storage import create_exam, get_session, open_db, start_session
    from posrat.models import Exam

    db = open_db(tmp_path / "legacy.sqlite")
    try:
        create_exam(db, Exam(id="e1", name="E1"))
        start_session(
            db,
            exam_id="e1",
            mode="training",
            session_id="s-legacy",
            started_at="2026-04-23T10:00:00Z",
        )
        reloaded = get_session(db, "s-legacy")
        assert reloaded is not None
        assert reloaded.candidate_name is None
        assert reloaded.question_count is None
        assert reloaded.time_limit_minutes is None
        assert reloaded.passing_score is None
        assert reloaded.target_score is None
    finally:
        db.close()


def test_list_sessions_preserves_snapshot_metadata(tmp_path) -> None:
    """``list_sessions`` must load snapshot fields the same way ``get_session`` does.

    Guards against one of the two SELECTs silently dropping the new
    columns — they share the ``_SESSION_SELECT_COLUMNS`` constant but a
    future refactor could drift.
    """

    from posrat.storage import create_exam, list_sessions, open_db, start_session
    from posrat.models import Exam

    db = open_db(tmp_path / "list.sqlite")
    try:
        create_exam(db, Exam(id="e1", name="E1"))
        start_session(
            db,
            exam_id="e1",
            mode="exam",
            session_id="s-a",
            started_at="2026-04-23T10:00:00Z",
            candidate_name="Alice",
            question_count=65,
            time_limit_minutes=90,
            passing_score=700,
            target_score=1000,
        )
        start_session(
            db,
            exam_id="e1",
            mode="training",
            session_id="s-b",
            started_at="2026-04-23T11:00:00Z",
        )
        sessions = list_sessions(db, "e1")
        assert [s.id for s in sessions] == ["s-a", "s-b"]
        assert sessions[0].candidate_name == "Alice"
        assert sessions[0].question_count == 65
        assert sessions[1].candidate_name is None
        assert sessions[1].question_count is None
    finally:
        db.close()
