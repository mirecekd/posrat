"""Smoke test: examples/sample-exam.json must load cleanly into ``Exam``.

This is our JSON bundle contract test — it guarantees that the shipped
sample file stays in sync with the Pydantic model as the model evolves.
"""

from __future__ import annotations

from pathlib import Path

from posrat.models import Exam

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "sample-exam.json"


def test_sample_exam_loads() -> None:
    assert SAMPLE_PATH.is_file(), f"missing sample file at {SAMPLE_PATH}"
    exam = Exam.model_validate_json(SAMPLE_PATH.read_text(encoding="utf-8"))

    assert exam.id == "posrat-sample-aws-basics"
    assert len(exam.questions) == 3

    types = [q.type for q in exam.questions]
    assert types == ["single_choice", "multi_choice", "hotspot"]

    # Hotspot payload integrity: each step points at a real option.
    hs_q = exam.questions[2]
    assert hs_q.hotspot is not None
    option_ids = {o.id for o in hs_q.hotspot.options}
    for step in hs_q.hotspot.steps:
        assert step.correct_option_id in option_ids


def test_sample_exam_is_roundtrip_stable() -> None:
    """Loading the file and dumping it back must yield an object equal
    to the reload of that dump — i.e. no silent coercion or drift."""

    exam = Exam.model_validate_json(SAMPLE_PATH.read_text(encoding="utf-8"))
    dumped = exam.model_dump_json()
    reloaded = Exam.model_validate_json(dumped)
    assert reloaded == exam
