"""Tests for SQLite → JSON export (step 2.9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from posrat.io import (
    dump_exam_to_json,
    export_exam_to_json_file,
    import_exam_from_json_file,
    load_exam_from_json_str,
)
from posrat.models import Choice, Exam, Question
from posrat.storage import create_exam, open_db


def _sample_exam() -> Exam:
    return Exam(
        id="exp-1",
        name="Export target",
        description="Round-trip candidate.",
        questions=[
            Question(
                id="q-1",
                type="single_choice",
                text="Pick one",
                choices=[
                    Choice(id="q-1-a", text="A", is_correct=True),
                    Choice(id="q-1-b", text="B", is_correct=False),
                ],
            ),
            Question(
                id="q-2",
                type="multi_choice",
                text="Pick many",
                image_path="diagram.png",
                choices=[
                    Choice(id="q-2-a", text="A", is_correct=True),
                    Choice(id="q-2-b", text="B", is_correct=True),
                    Choice(id="q-2-c", text="C", is_correct=False),
                ],
            ),
        ],
    )


def test_dump_exam_to_json_matches_model_dump(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        exam = _sample_exam()
        create_exam(db, exam)

        payload = dump_exam_to_json(db, "exp-1")
        assert isinstance(payload, str)

        # The payload must parse as JSON and the reconstructed Exam must
        # equal the original one after validation.
        data = json.loads(payload)
        assert data["id"] == "exp-1"

        restored = load_exam_from_json_str(payload)
        assert restored.model_dump() == exam.model_dump()
    finally:
        db.close()


def test_dump_exam_to_json_raises_lookup_error_when_missing(
    tmp_path: Path,
) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        with pytest.raises(LookupError):
            dump_exam_to_json(db, "nope")
    finally:
        db.close()


def test_export_to_file_writes_indented_json(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        exam = _sample_exam()
        create_exam(db, exam)

        out = tmp_path / "out" / "exam.json"
        export_exam_to_json_file(db, "exp-1", out)
        assert out.exists()

        text = out.read_text(encoding="utf-8")
        # Default indent=2 → expect a newline after the opening brace.
        assert text.startswith("{\n  ")
        data = json.loads(text)
        assert data["id"] == "exp-1"
    finally:
        db.close()


def test_export_and_reimport_round_trip(tmp_path: Path) -> None:
    """Export from DB_A then import into DB_B must preserve the exam."""
    db_a = open_db(tmp_path / "a.sqlite")
    try:
        exam = _sample_exam()
        create_exam(db_a, exam)
        out = tmp_path / "between.json"
        export_exam_to_json_file(db_a, "exp-1", out)
    finally:
        db_a.close()

    db_b = open_db(tmp_path / "b.sqlite")
    try:
        imported = import_exam_from_json_file(db_b, out)
        assert imported.model_dump() == _sample_exam().model_dump()
    finally:
        db_b.close()
