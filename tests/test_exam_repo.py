"""Tests for the exam DAO (step 2.5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from posrat.models import Choice, Exam, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep
from posrat.storage import create_exam, get_exam, open_db


def _single_choice(qid: str = "q-sc") -> Question:
    return Question(
        id=qid,
        type="single_choice",
        text="Pick one",
        explanation="Because.",
        choices=[
            Choice(id=f"{qid}-a", text="A", is_correct=True),
            Choice(id=f"{qid}-b", text="B", is_correct=False),
        ],
    )


def _multi_choice(qid: str = "q-mc") -> Question:
    return Question(
        id=qid,
        type="multi_choice",
        text="Pick all",
        explanation=None,
        image_path="diagram.png",
        choices=[
            Choice(id=f"{qid}-a", text="A", is_correct=True),
            Choice(id=f"{qid}-b", text="B", is_correct=True),
            Choice(id=f"{qid}-c", text="C", is_correct=False),
        ],
    )


def _hotspot_question(qid: str = "q-hs") -> Question:
    return Question(
        id=qid,
        type="hotspot",
        text="Map fields",
        choices=[],
        hotspot=Hotspot(
            options=[
                HotspotOption(id="o-1", text="EC2"),
                HotspotOption(id="o-2", text="RDS"),
            ],
            steps=[HotspotStep(id="s-1", prompt="compute", correct_option_id="o-1")],
        ),
    )


def test_create_and_get_exam_round_trip(tmp_path: Path) -> None:
    """Exam persisted via create_exam must come back identical via get_exam."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        original = Exam(
            id="aws-saa-c03",
            name="AWS SAA-C03 practice",
            description="Two-question smoke set.",
            questions=[_single_choice(), _multi_choice()],
        )
        create_exam(db, original)

        loaded = get_exam(db, "aws-saa-c03")
        assert loaded is not None
        assert loaded.model_dump() == original.model_dump()
    finally:
        db.close()


def test_get_exam_returns_none_for_unknown_id(tmp_path: Path) -> None:
    """Missing exam id yields ``None`` rather than raising."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        assert get_exam(db, "does-not-exist") is None
    finally:
        db.close()


def test_create_exam_rejects_duplicate_id(tmp_path: Path) -> None:
    """Two exams with the same id must trigger an IntegrityError."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        exam = Exam(id="dup", name="One", questions=[_single_choice()])
        create_exam(db, exam)
        with pytest.raises(sqlite3.IntegrityError):
            create_exam(db, exam)
    finally:
        db.close()


def test_create_exam_is_atomic_on_failure(tmp_path: Path) -> None:
    """A failing insert must roll back every previously inserted row."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        good_exam = Exam(id="good", name="G", questions=[_single_choice()])
        create_exam(db, good_exam)

        # Model validation forbids duplicate question ids, so we build the
        # colliding exam via ``model_construct`` to bypass the guard and
        # verify the DAO layer's rollback on sqlite IntegrityError.
        colliding = Exam.model_construct(
            id="bad",
            name="B",
            description=None,
            questions=[_single_choice("q-1"), _single_choice("q-1")],
        )

        with pytest.raises(sqlite3.IntegrityError):
            create_exam(db, colliding)

        # "bad" must not appear and "good" must remain intact.
        (count_bad,) = db.execute(
            "SELECT COUNT(*) FROM exams WHERE id = 'bad'"
        ).fetchone()
        assert count_bad == 0

        loaded = get_exam(db, "good")
        assert loaded is not None
        assert loaded.model_dump() == good_exam.model_dump()
    finally:
        db.close()


def test_create_and_get_hotspot_question_round_trip(tmp_path: Path) -> None:
    """Hotspot persistence (step 2.10) must round-trip options and steps."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        original = Exam(
            id="with-hs",
            name="With hotspot",
            questions=[_hotspot_question()],
        )
        create_exam(db, original)

        loaded = get_exam(db, "with-hs")
        assert loaded is not None
        assert loaded.model_dump() == original.model_dump()
    finally:
        db.close()


def test_create_and_get_exam_round_trips_complexity_and_section(
    tmp_path: Path,
) -> None:
    """Step 8.5 metadata must survive create_exam → get_exam verbatim.

    Covers all three question types in one shot so we know the two
    optional fields flow through the ``single_choice`` / ``multi_choice``
    / ``hotspot`` branches of ``_load_question`` identically. Legacy
    questions that leave both fields ``None`` must still round-trip as
    ``None`` (no NULL-coercion surprises).
    """

    sc = _single_choice("q-sc-meta")
    sc = sc.model_copy(update={"complexity": 2, "section": "Compute"})

    mc = _multi_choice("q-mc-meta")
    mc = mc.model_copy(update={"complexity": 5, "section": "  IAM  "})

    hs = _hotspot_question("q-hs-meta")
    hs = hs.model_copy(update={"complexity": 3, "section": "Networking"})

    legacy = _single_choice("q-legacy")  # complexity=None, section=None

    db = open_db(tmp_path / "exam.sqlite")
    try:
        original = Exam(
            id="meta",
            name="Meta round-trip",
            questions=[sc, mc, hs, legacy],
        )
        create_exam(db, original)

        loaded = get_exam(db, "meta")
        assert loaded is not None

        by_id = {q.id: q for q in loaded.questions}
        assert by_id["q-sc-meta"].complexity == 2
        assert by_id["q-sc-meta"].section == "Compute"
        assert by_id["q-mc-meta"].complexity == 5
        # "  IAM  " was normalised to "IAM" by the model validator at
        # construction time — persistence must not re-introduce the
        # surrounding whitespace.
        assert by_id["q-mc-meta"].section == "IAM"
        assert by_id["q-hs-meta"].complexity == 3
        assert by_id["q-hs-meta"].section == "Networking"
        assert by_id["q-legacy"].complexity is None
        assert by_id["q-legacy"].section is None
    finally:
        db.close()



# --------------------------------------------------------------------------- #
# Phase 7A.3 — Runner metadata round-trip                                     #
# --------------------------------------------------------------------------- #


def test_create_and_get_exam_roundtrip_runner_metadata(tmp_path) -> None:
    """``create_exam`` + ``get_exam`` must preserve the four 7A metadata fields."""

    db = open_db(tmp_path / "meta.sqlite")
    try:
        original = Exam(
            id="aif-c01",
            name="AIF-C01",
            description="AWS AI Practitioner",
            questions=[],
            default_question_count=65,
            time_limit_minutes=90,
            passing_score=700,
            target_score=1000,
        )
        create_exam(db, original)

        loaded = get_exam(db, "aif-c01")
        assert loaded is not None
        assert loaded.default_question_count == 65
        assert loaded.time_limit_minutes == 90
        assert loaded.passing_score == 700
        assert loaded.target_score == 1000
    finally:
        db.close()


def test_get_exam_returns_none_metadata_for_legacy_exam(tmp_path) -> None:
    """Exam built without metadata must rehydrate all four fields as ``None``.

    This guards the legacy-compat promise: pre-Phase-7A exams stored on
    disk (complexity/section already present from v8, but no v10 columns
    set) must keep loading with ``None`` defaults — Pydantic ``Optional``
    + SQLite ``NULL`` round-trip without losing information.
    """

    db = open_db(tmp_path / "legacy.sqlite")
    try:
        original = Exam(id="legacy", name="Legacy")
        create_exam(db, original)

        loaded = get_exam(db, "legacy")
        assert loaded is not None
        assert loaded.default_question_count is None
        assert loaded.time_limit_minutes is None
        assert loaded.passing_score is None
        assert loaded.target_score is None
    finally:
        db.close()
