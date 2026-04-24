# tests/test_importers_conversion.py
"""Tests for posrat/importers/conversion.py — ParsedQuestion → Question conversion + persistence."""
from __future__ import annotations

from pathlib import Path

import pytest

from posrat.importers.base import ParsedChoice, ParsedImage, ParsedQuestion
from posrat.importers.conversion import (
    ImportReport,
    convert_parsed_to_question,
    persist_parsed_questions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sc_parsed(source_index: int = 1, text: str = "Single choice Q?") -> ParsedQuestion:
    return ParsedQuestion(
        source_index=source_index,
        text=text,
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="single_choice",
    )


def _mc_parsed(source_index: int = 2, text: str = "Multi choice Q?") -> ParsedQuestion:
    return ParsedQuestion(
        source_index=source_index,
        text=text,
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=True),
            ParsedChoice(letter="C", text="Option C", is_correct=False),
        ],
        question_type="multi_choice",
    )


def _mc_no_correct(source_index: int = 3) -> ParsedQuestion:
    return ParsedQuestion(
        source_index=source_index,
        text="Invalid multi choice?",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=False),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="multi_choice",
    )


def _seed_exam(db_path: Path, exam_id: str) -> None:
    from posrat.models import Exam
    from posrat.storage import create_exam, open_db

    db = open_db(db_path)
    try:
        create_exam(db, Exam(id=exam_id, name="Test Exam"))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Step 1: ImportReport dataclass shape
# ---------------------------------------------------------------------------


def test_import_report_shape() -> None:
    pq = _sc_parsed()
    report = ImportReport(
        imported=2,
        skipped=[(pq, "some reason")],
        image_paths=[Path("/tmp/img.png")],
    )
    assert report.imported == 2
    assert report.skipped == [(pq, "some reason")]
    assert report.image_paths == [Path("/tmp/img.png")]



# ---------------------------------------------------------------------------
# Step 2: convert_parsed_to_question
# ---------------------------------------------------------------------------


def test_convert_single_choice_returns_valid_question() -> None:
    parsed = _sc_parsed()
    q = convert_parsed_to_question(parsed)
    assert q.id.startswith("q-")
    assert len(q.id) == len("q-") + 8
    assert q.type == "single_choice"
    assert q.text == parsed.text
    assert q.explanation is None
    assert q.image_path is None
    assert len(q.choices) == 2


def test_convert_choice_ids_derived_from_question_id() -> None:
    q = convert_parsed_to_question(_sc_parsed())
    for choice in q.choices:
        assert choice.id.startswith(q.id + "-")


def test_convert_choice_id_uses_letter_lowercase() -> None:
    q = convert_parsed_to_question(_sc_parsed())
    letters = {c.id.split("-")[-1] for c in q.choices}
    assert letters == {"a", "b"}


def test_convert_preserves_correctness() -> None:
    q = convert_parsed_to_question(_sc_parsed())
    correct = [c for c in q.choices if c.is_correct]
    assert len(correct) == 1
    assert correct[0].id.endswith("-a")


def test_convert_multi_choice() -> None:
    q = convert_parsed_to_question(_mc_parsed())
    assert q.type == "multi_choice"
    correct = [c for c in q.choices if c.is_correct]
    assert len(correct) == 2


def test_convert_preserves_explanation() -> None:
    parsed = ParsedQuestion(
        source_index=1,
        text="Q?",
        choices=[
            ParsedChoice(letter="A", text="A", is_correct=True),
            ParsedChoice(letter="B", text="B", is_correct=False),
        ],
        question_type="single_choice",
        explanation="Because of X.",
    )
    q = convert_parsed_to_question(parsed)
    assert q.explanation == "Because of X."


def test_convert_preserves_img_placeholders_in_text() -> None:
    parsed = ParsedQuestion(
        source_index=1,
        text="What does ⟨IMG:0⟩ show?",
        choices=[
            ParsedChoice(letter="A", text="A", is_correct=True),
            ParsedChoice(letter="B", text="B", is_correct=False),
        ],
        question_type="single_choice",
    )
    q = convert_parsed_to_question(parsed)
    assert "⟨IMG:0⟩" in q.text


def test_convert_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        convert_parsed_to_question(_mc_no_correct())


def test_convert_uses_custom_prefix() -> None:
    q = convert_parsed_to_question(_sc_parsed(), id_prefix="myq-")
    assert q.id.startswith("myq-")
    for choice in q.choices:
        assert choice.id.startswith("myq-")


# ---------------------------------------------------------------------------
# Step 3: persist happy path (no images)
# ---------------------------------------------------------------------------


def test_persist_happy_path(tmp_path: Path) -> None:
    from posrat.storage import list_questions, open_db

    exam_id = "exam-test"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    report = persist_parsed_questions(
        [_sc_parsed(source_index=1), _mc_parsed(source_index=2)],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 2
    assert report.skipped == []
    assert report.image_paths == []

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()
    assert len(questions) == 2


def test_persist_invalid_question_skipped(tmp_path: Path) -> None:
    from posrat.storage import list_questions, open_db

    exam_id = "exam-test"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    report = persist_parsed_questions(
        [_mc_no_correct()],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 0
    assert len(report.skipped) == 1
    skipped_pq, reason = report.skipped[0]
    assert skipped_pq.source_index == 3
    assert "correct" in reason.lower()

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()
    assert questions == []


def test_persist_invalid_leaves_no_orphan_assets(tmp_path: Path) -> None:
    exam_id = "exam-test"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    persist_parsed_questions(
        [_mc_no_correct()],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assets_dir = tmp_path / "assets" / exam_id
    assert not assets_dir.exists() or list(assets_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Step 4: persist with images + orphan-free guarantees
# ---------------------------------------------------------------------------


def test_persist_image_file_written(tmp_path: Path) -> None:
    import re

    exam_id = "exam-imgs"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    parsed = ParsedQuestion(
        source_index=1,
        text="What does ⟨IMG:0⟩ show?",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="single_choice",
        images=[ParsedImage(placeholder_id=0, data=b"\x89PNG fake", suffix=".png")],
    )

    report = persist_parsed_questions(
        [parsed],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 1
    assert len(report.image_paths) == 1
    written = report.image_paths[0]
    assert written.exists()
    assert written.read_bytes() == b"\x89PNG fake"
    assert written.suffix == ".png"
    assert written.parent == tmp_path / "assets" / exam_id


def test_persist_image_placeholder_replaced_in_text(tmp_path: Path) -> None:
    import re

    from posrat.storage import list_questions, open_db

    exam_id = "exam-imgs"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    parsed = ParsedQuestion(
        source_index=1,
        text="What does ⟨IMG:0⟩ show?",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="single_choice",
        images=[ParsedImage(placeholder_id=0, data=b"img data", suffix=".jpg")],
    )

    persist_parsed_questions(
        [parsed],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()

    assert len(questions) == 1
    assert re.search(rf"!\[\]\({re.escape(exam_id)}/", questions[0].text)
    assert "⟨IMG:0⟩" not in questions[0].text


def test_persist_image_unknown_placeholder_left_raw(tmp_path: Path) -> None:
    from posrat.storage import list_questions, open_db

    exam_id = "exam-imgs"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    parsed = ParsedQuestion(
        source_index=1,
        text="Missing image: ⟨IMG:99⟩ here.",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="single_choice",
        images=[],
    )

    report = persist_parsed_questions(
        [parsed],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()

    assert report.imported == 1
    assert "⟨IMG:99⟩" in questions[0].text


def test_persist_image_not_in_text_discarded(tmp_path: Path) -> None:
    exam_id = "exam-imgs"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    parsed = ParsedQuestion(
        source_index=1,
        text="No placeholders here.",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=True),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="single_choice",
        images=[ParsedImage(placeholder_id=5, data=b"orphan data", suffix=".png")],
    )

    report = persist_parsed_questions(
        [parsed],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 1
    assert report.image_paths == []


def test_persist_no_orphan_files_on_invalid(tmp_path: Path) -> None:
    exam_id = "exam-imgs"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    parsed = ParsedQuestion(
        source_index=1,
        text="Invalid ⟨IMG:0⟩ question?",
        choices=[
            ParsedChoice(letter="A", text="Option A", is_correct=False),
            ParsedChoice(letter="B", text="Option B", is_correct=False),
        ],
        question_type="multi_choice",
        images=[ParsedImage(placeholder_id=0, data=b"img data", suffix=".png")],
    )

    report = persist_parsed_questions(
        [parsed],
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 0
    assert len(report.skipped) == 1
    assets_dir = tmp_path / "assets" / exam_id
    assert not assets_dir.exists() or list(assets_dir.iterdir()) == []


def test_persist_per_question_isolation(tmp_path: Path) -> None:
    from posrat.storage import list_questions, open_db

    exam_id = "exam-test"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    selected = [_sc_parsed(1), _mc_no_correct(2), _mc_parsed(3)]
    report = persist_parsed_questions(
        selected,
        db_path=db_path,
        data_dir=tmp_path,
        exam_id=exam_id,
    )

    assert report.imported == 2
    assert len(report.skipped) == 1

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()
    assert len(questions) == 2


# ---------------------------------------------------------------------------
# Step 5: retry on id collision
# ---------------------------------------------------------------------------


def test_persist_retry_on_id_collision(tmp_path: Path) -> None:
    """When uuid4 generates the same id twice, persist retries with a fresh id."""
    import uuid
    from unittest.mock import patch

    from posrat.storage import list_questions, open_db

    exam_id = "exam-retry"
    db_path = tmp_path / "test.sqlite"
    _seed_exam(db_path, exam_id)

    uuid_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    uuid_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    # Q1 gets uuid_a (unique), Q2 gets uuid_a (collision!), retry gets uuid_b
    side_effects = [uuid_a, uuid_a, uuid_b]

    with patch("posrat.importers.conversion.uuid4", side_effect=side_effects):
        report = persist_parsed_questions(
            [_sc_parsed(1), _sc_parsed(2)],
            db_path=db_path,
            data_dir=tmp_path,
            exam_id=exam_id,
        )

    assert report.imported == 2
    assert report.skipped == []

    db = open_db(db_path)
    try:
        questions = list_questions(db, exam_id)
    finally:
        db.close()

    assert len(questions) == 2
    ids = {q.id for q in questions}
    assert "q-aaaaaaaa" in ids
    assert "q-bbbbbbbb" in ids
