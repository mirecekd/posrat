"""Tests for the :class:`~posrat.models.exam.Exam` runner metadata fields.

Phase 7A.1 — Runner-facing exam metadata:

* ``default_question_count`` — picker prefill for "Take N questions".
* ``time_limit_minutes`` — default session timer budget.
* ``passing_score`` + ``target_score`` — raw-points scoring threshold
  (e.g. 700 / 1000 as in Visual CertExam). The percentage shown to the
  user is always a derivation: ``raw_score / target_score * 100``.

Legacy JSON without any of these fields must still load — all four
fields are ``Optional`` and default to ``None``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from posrat.models.exam import Exam


def _make_minimal_exam(**overrides: object) -> Exam:
    """Build the smallest possible :class:`Exam` for metadata tests.

    The question list is empty because these tests only exercise
    exam-level metadata fields.
    """

    kwargs: dict[str, object] = {"id": "e1", "name": "Smoke Exam"}
    kwargs.update(overrides)
    return Exam(**kwargs)  # type: ignore[arg-type]


def test_exam_metadata_defaults_are_none() -> None:
    """Unset metadata fields must default to ``None`` (legacy compat)."""

    exam = _make_minimal_exam()

    assert exam.default_question_count is None
    assert exam.time_limit_minutes is None
    assert exam.passing_score is None
    assert exam.target_score is None


def test_exam_accepts_valid_metadata() -> None:
    """Happy path — all four metadata fields accepted together."""

    exam = _make_minimal_exam(
        default_question_count=65,
        time_limit_minutes=90,
        passing_score=700,
        target_score=1000,
    )

    assert exam.default_question_count == 65
    assert exam.time_limit_minutes == 90
    assert exam.passing_score == 700
    assert exam.target_score == 1000


def test_exam_rejects_zero_default_question_count() -> None:
    """``default_question_count`` must be ``>= 1`` (0 is meaningless)."""

    with pytest.raises(ValidationError):
        _make_minimal_exam(default_question_count=0)


def test_exam_rejects_zero_time_limit_minutes() -> None:
    """``time_limit_minutes`` must be ``>= 1`` (0 / negative rejected)."""

    with pytest.raises(ValidationError):
        _make_minimal_exam(time_limit_minutes=0)


def test_exam_rejects_negative_passing_score() -> None:
    """``passing_score`` must be ``>= 0`` (raw points)."""

    with pytest.raises(ValidationError):
        _make_minimal_exam(passing_score=-10)


def test_exam_rejects_zero_target_score() -> None:
    """``target_score`` must be ``>= 1`` (``0`` would divide by zero)."""

    with pytest.raises(ValidationError):
        _make_minimal_exam(target_score=0)


def test_exam_rejects_passing_score_above_target_score() -> None:
    """Cross-field validator: ``passing_score`` ≤ ``target_score``."""

    with pytest.raises(ValidationError):
        _make_minimal_exam(passing_score=1100, target_score=1000)


def test_exam_allows_passing_score_equal_to_target_score() -> None:
    """Edge case: requiring a perfect score is valid, if unusual."""

    exam = _make_minimal_exam(passing_score=1000, target_score=1000)

    assert exam.passing_score == 1000
    assert exam.target_score == 1000


def test_exam_allows_passing_score_without_target_score() -> None:
    """Cross-check only fires when both fields are set (legacy-safe)."""

    exam = _make_minimal_exam(passing_score=700)

    assert exam.passing_score == 700
    assert exam.target_score is None


def test_exam_metadata_roundtrips_through_model_dump() -> None:
    """``Exam.model_dump`` + ``Exam.model_validate`` must preserve metadata."""

    exam = _make_minimal_exam(
        default_question_count=65,
        time_limit_minutes=90,
        passing_score=700,
        target_score=1000,
    )

    payload = exam.model_dump()
    rehydrated = Exam.model_validate(payload)

    assert rehydrated.default_question_count == 65
    assert rehydrated.time_limit_minutes == 90
    assert rehydrated.passing_score == 700
    assert rehydrated.target_score == 1000


def test_exam_legacy_json_without_metadata_fields_still_loads() -> None:
    """Old JSON bundles (pre-7A) must load with ``None`` metadata.

    We explicitly model-validate a dict that looks like a legacy bundle
    — without any of the four new keys — to guard against accidentally
    making them required.
    """

    legacy_payload = {
        "id": "legacy",
        "name": "Legacy Exam",
        "description": "Pre-Phase-7A bundle.",
        "questions": [],
    }

    exam = Exam.model_validate(legacy_payload)

    assert exam.default_question_count is None
    assert exam.time_limit_minutes is None
    assert exam.passing_score is None
    assert exam.target_score is None
