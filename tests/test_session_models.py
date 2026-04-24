"""Tests for the Session and Answer Pydantic models (step 2.5.3a)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from posrat.models import Answer, Session


def test_session_minimal_fields_are_accepted() -> None:
    """A freshly started session has no finished_at and no answers."""
    s = Session(
        id="sess-1",
        exam_id="exam-1",
        mode="training",
        started_at="2025-01-01T00:00:00Z",
    )
    assert s.finished_at is None
    assert s.answers == []


def test_session_mode_must_be_training_or_exam() -> None:
    """Any value outside the literal set is rejected."""
    with pytest.raises(ValidationError):
        Session(
            id="sess-1",
            exam_id="exam-1",
            mode="bogus",  # type: ignore[arg-type]
            started_at="2025-01-01T00:00:00Z",
        )


def test_session_rejects_empty_string_identifiers() -> None:
    """Empty ``id`` and ``exam_id`` are invalid."""
    with pytest.raises(ValidationError):
        Session(
            id="",
            exam_id="exam-1",
            mode="exam",
            started_at="2025-01-01T00:00:00Z",
        )
    with pytest.raises(ValidationError):
        Session(
            id="sess-1",
            exam_id="",
            mode="exam",
            started_at="2025-01-01T00:00:00Z",
        )


def test_answer_accepts_valid_opaque_json_payload() -> None:
    """``given_json`` is stored as-is as long as it parses."""
    a = Answer(
        id="ans-1",
        session_id="sess-1",
        question_id="q-1",
        given_json='{"choice_id":"c-ok"}',
        is_correct=True,
        time_ms=1234,
    )
    assert a.time_ms == 1234
    assert a.is_correct is True


def test_answer_rejects_malformed_given_json() -> None:
    """Malformed JSON must fail fast at model layer."""
    with pytest.raises(ValidationError):
        Answer(
            id="ans-1",
            session_id="sess-1",
            question_id="q-1",
            given_json="{not-json",
            is_correct=False,
        )


def test_answer_rejects_negative_time_ms() -> None:
    """Negative time spent on a question is nonsensical."""
    with pytest.raises(ValidationError):
        Answer(
            id="ans-1",
            session_id="sess-1",
            question_id="q-1",
            given_json="{}",
            is_correct=True,
            time_ms=-5,
        )


def test_session_with_answers_round_trips_through_json() -> None:
    """A session with embedded answers survives dump + load unchanged."""
    original = Session(
        id="sess-1",
        exam_id="exam-1",
        mode="exam",
        started_at="2025-01-01T00:00:00Z",
        finished_at="2025-01-01T00:30:00Z",
        answers=[
            Answer(
                id="ans-1",
                session_id="sess-1",
                question_id="q-1",
                given_json='{"choice_id":"q-1-c-a"}',
                is_correct=True,
                time_ms=1000,
            ),
            Answer(
                id="ans-2",
                session_id="sess-1",
                question_id="q-2",
                given_json='{"choice_ids":["q-2-c-a","q-2-c-b"]}',
                is_correct=False,
                time_ms=None,
            ),
        ],
    )

    dumped = original.model_dump_json()
    reloaded = Session.model_validate_json(dumped)
    assert reloaded == original
