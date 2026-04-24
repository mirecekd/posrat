"""Tests for JSON import → SQLite (step 2.8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from posrat.io import (
    import_exam_from_json_file,
    load_exam_from_json_file,
    load_exam_from_json_str,
)
from posrat.storage import get_exam, open_db


def _valid_exam_json() -> str:
    exam = {
        "id": "imp-1",
        "name": "Imported",
        "description": "Validated JSON bundle.",
        "questions": [
            {
                "id": "q-1",
                "type": "single_choice",
                "text": "pick",
                "explanation": None,
                "image_path": None,
                "hotspot": None,
                "choices": [
                    {"id": "c-a", "text": "A", "is_correct": True},
                    {"id": "c-b", "text": "B", "is_correct": False},
                ],
            },
            {
                "id": "q-2",
                "type": "multi_choice",
                "text": "pick many",
                "explanation": "because",
                "image_path": "diagram.png",
                "hotspot": None,
                "choices": [
                    {"id": "c-x", "text": "X", "is_correct": True},
                    {"id": "c-y", "text": "Y", "is_correct": True},
                    {"id": "c-z", "text": "Z", "is_correct": False},
                ],
            },
        ],
    }
    return json.dumps(exam)


def test_load_exam_from_json_str_validates_and_returns_model() -> None:
    exam = load_exam_from_json_str(_valid_exam_json())
    assert exam.id == "imp-1"
    assert [q.id for q in exam.questions] == ["q-1", "q-2"]


def test_load_exam_from_json_str_raises_on_invalid_shape() -> None:
    # single_choice with zero correct choices must fail at the model layer.
    bad = {
        "id": "bad",
        "name": "B",
        "questions": [
            {
                "id": "q",
                "type": "single_choice",
                "text": "x",
                "choices": [
                    {"id": "a", "text": "A", "is_correct": False},
                    {"id": "b", "text": "B", "is_correct": False},
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        load_exam_from_json_str(json.dumps(bad))


def test_load_exam_from_json_file_reads_utf8(tmp_path: Path) -> None:
    f = tmp_path / "exam.json"
    f.write_text(_valid_exam_json(), encoding="utf-8")
    exam = load_exam_from_json_file(f)
    assert exam.name == "Imported"


def test_import_exam_from_json_file_persists_to_sqlite(tmp_path: Path) -> None:
    f = tmp_path / "exam.json"
    f.write_text(_valid_exam_json(), encoding="utf-8")

    db = open_db(tmp_path / "exam.sqlite")
    try:
        imported = import_exam_from_json_file(db, f)
        assert imported.id == "imp-1"

        loaded = get_exam(db, "imp-1")
        assert loaded is not None
        assert loaded.model_dump() == imported.model_dump()
    finally:
        db.close()


def test_import_sample_exam_json_end_to_end(tmp_path: Path) -> None:
    """The canonical sample exam (incl. hotspot) must import into SQLite."""
    source = Path(__file__).resolve().parents[1] / "examples" / "sample-exam.json"
    db = open_db(tmp_path / "sample.sqlite")
    try:
        exam = import_exam_from_json_file(db, source)
        loaded = get_exam(db, exam.id)
        assert loaded is not None
        assert loaded.model_dump() == exam.model_dump()

        # Sample contains one of each supported type.
        types = sorted(q.type for q in loaded.questions)
        assert types == ["hotspot", "multi_choice", "single_choice"]
    finally:
        db.close()


def test_import_exam_fails_fast_before_writing_when_invalid(tmp_path: Path) -> None:
    """An invalid JSON must not leave any partial rows behind."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text(
        json.dumps(
            {
                "id": "bad",
                "name": "B",
                "questions": [
                    {
                        "id": "q",
                        "type": "single_choice",
                        "text": "x",
                        "choices": [
                            {"id": "a", "text": "A", "is_correct": False},
                            {"id": "b", "text": "B", "is_correct": False},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    db = open_db(tmp_path / "exam.sqlite")
    try:
        with pytest.raises(ValidationError):
            import_exam_from_json_file(db, bad_json)

        (count,) = db.execute("SELECT COUNT(*) FROM exams").fetchone()
        assert count == 0
    finally:
        db.close()
