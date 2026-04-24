"""Tests for the question DAO (step 2.6)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from posrat.models import Choice, Exam, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep
from posrat.storage import (
    add_question,
    create_exam,
    delete_question,
    get_exam,
    list_questions,
    open_db,
    reorder_questions,
    update_question,
)



def _sc(qid: str, correct: str = "a") -> Question:
    return Question(
        id=qid,
        type="single_choice",
        text=f"Question {qid}",
        choices=[
            Choice(id=f"{qid}-a", text="A", is_correct=(correct == "a")),
            Choice(id=f"{qid}-b", text="B", is_correct=(correct == "b")),
        ],
    )


def _mc(qid: str) -> Question:
    return Question(
        id=qid,
        type="multi_choice",
        text=f"Multi {qid}",
        choices=[
            Choice(id=f"{qid}-a", text="A", is_correct=True),
            Choice(id=f"{qid}-b", text="B", is_correct=True),
            Choice(id=f"{qid}-c", text="C", is_correct=False),
        ],
    )


def _hs(qid: str = "q-hs") -> Question:
    return Question(
        id=qid,
        type="hotspot",
        text="hs",
        choices=[],
        hotspot=Hotspot(
            options=[HotspotOption(id="o-1", text="A")],
            steps=[HotspotStep(id="s-1", prompt="p", correct_option_id="o-1")],
        ),
    )


def _seed_empty_exam(db: sqlite3.Connection, exam_id: str = "e-1") -> None:
    create_exam(db, Exam(id=exam_id, name="Seed", questions=[_sc("q-seed")]))
    # Remove the seed question so tests start with an empty exam but a valid row.
    delete_question(db, "q-seed")


def test_add_question_appends_and_returns_order_index(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))

        idx_1 = add_question(db, "e-1", _sc("q-1"))
        idx_2 = add_question(db, "e-1", _mc("q-2"))
        assert idx_1 == 1
        assert idx_2 == 2

        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert [q.id for q in loaded.questions] == ["q-0", "q-1", "q-2"]
    finally:
        db.close()


def test_add_question_honors_explicit_order_index(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        add_question(db, "e-1", _sc("q-9"), order_index=9)
        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert [q.id for q in loaded.questions] == ["q-0", "q-9"]
    finally:
        db.close()


def test_add_question_raises_lookup_error_for_unknown_exam(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        with pytest.raises(LookupError):
            add_question(db, "missing", _sc("q-1"))
    finally:
        db.close()


def test_add_question_rejects_duplicate_id(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        with pytest.raises(sqlite3.IntegrityError):
            add_question(db, "e-1", _sc("q-0"))
    finally:
        db.close()


def test_add_question_persists_hotspot(tmp_path: Path) -> None:
    """Hotspot questions are supported since step 2.10."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        add_question(db, "e-1", _hs("q-hs"))

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-0", "q-hs"]
        assert listed[1].type == "hotspot"
        assert listed[1].hotspot is not None
        assert [o.id for o in listed[1].hotspot.options] == ["o-1"]
        assert [s.id for s in listed[1].hotspot.steps] == ["s-1"]
    finally:
        db.close()


def test_update_question_swaps_single_choice_for_hotspot(tmp_path: Path) -> None:
    """Changing a question's type to hotspot must replace its payload."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-1")]))
        update_question(db, _hs("q-1"))

        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert loaded.questions[0].type == "hotspot"
        assert loaded.questions[0].choices == []
        assert loaded.questions[0].hotspot is not None

        (choice_count,) = db.execute(
            "SELECT COUNT(*) FROM choices WHERE question_id = 'q-1'"
        ).fetchone()
        assert choice_count == 0
    finally:
        db.close()


def test_update_question_replaces_text_and_choices(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-1")]))

        updated = Question(
            id="q-1",
            type="multi_choice",
            text="Rewritten",
            explanation="new",
            image_path="img.png",
            choices=[
                Choice(id="c-x", text="X", is_correct=True),
                Choice(id="c-y", text="Y", is_correct=True),
                Choice(id="c-z", text="Z", is_correct=False),
            ],
        )
        update_question(db, updated)

        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert len(loaded.questions) == 1
        q = loaded.questions[0]
        assert q.type == "multi_choice"
        assert q.text == "Rewritten"
        assert q.explanation == "new"
        assert q.image_path == "img.png"
        assert [c.id for c in q.choices] == ["c-x", "c-y", "c-z"]
        assert [c.is_correct for c in q.choices] == [True, True, False]
    finally:
        db.close()


def test_update_question_preserves_order_index(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(
                id="e-1",
                name="E",
                questions=[_sc("q-0"), _sc("q-1"), _sc("q-2")],
            ),
        )
        update_question(
            db,
            Question(
                id="q-1",
                type="single_choice",
                text="updated",
                choices=[
                    Choice(id="n-a", text="A", is_correct=True),
                    Choice(id="n-b", text="B", is_correct=False),
                ],
            ),
        )
        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert [q.id for q in loaded.questions] == ["q-0", "q-1", "q-2"]
    finally:
        db.close()


def test_update_question_raises_lookup_error_for_unknown_id(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        with pytest.raises(LookupError):
            update_question(db, _sc("not-there"))
    finally:
        db.close()


def test_delete_question_removes_row_and_choices(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-1", name="E", questions=[_sc("q-0"), _sc("q-1")]),
        )
        assert delete_question(db, "q-0") is True
        loaded = get_exam(db, "e-1")
        assert loaded is not None
        assert [q.id for q in loaded.questions] == ["q-1"]
        (choice_count,) = db.execute(
            "SELECT COUNT(*) FROM choices WHERE question_id = 'q-0'"
        ).fetchone()
        assert choice_count == 0
    finally:
        db.close()


def test_delete_question_returns_false_when_missing(tmp_path: Path) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        assert delete_question(db, "never-existed") is False
    finally:
        db.close()


def test_list_questions_respects_order_index(tmp_path: Path) -> None:
    """Questions must come back in ``order_index`` order regardless of insertion order."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        # Insert q-5 before q-3 by explicit order_index so we prove ORDER BY
        # drives the output rather than insertion order.
        add_question(db, "e-1", _sc("q-5"), order_index=5)
        add_question(db, "e-1", _sc("q-3"), order_index=3)
        add_question(db, "e-1", _sc("q-1"), order_index=1)

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-0", "q-1", "q-3", "q-5"]
    finally:
        db.close()


def test_list_questions_returns_empty_for_missing_or_empty_exam(
    tmp_path: Path,
) -> None:
    db = open_db(tmp_path / "exam.sqlite")
    try:
        assert list_questions(db, "missing") == []

        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        delete_question(db, "q-0")
        assert list_questions(db, "e-1") == []
    finally:
        db.close()


def test_list_questions_reconstructs_choices(tmp_path: Path) -> None:
    """Loaded question must keep its choices ordered by order_index."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_mc("q-0")]))
        listed = list_questions(db, "e-1")
        assert len(listed) == 1
        assert [c.id for c in listed[0].choices] == ["q-0-a", "q-0-b", "q-0-c"]
        assert [c.is_correct for c in listed[0].choices] == [True, True, False]
    finally:
        db.close()


def test_reorder_questions_applies_new_order(tmp_path: Path) -> None:
    """Happy path: reverse a 3-question exam and watch list_questions follow."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(
                id="e-1",
                name="E",
                questions=[_sc("q-a"), _sc("q-b"), _sc("q-c")],
            ),
        )

        reorder_questions(db, "e-1", ["q-c", "q-a", "q-b"])

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-c", "q-a", "q-b"]
    finally:
        db.close()


def test_reorder_questions_redistributes_dense_zero_based_indices(
    tmp_path: Path,
) -> None:
    """Reorder rewrites order_index into a tight 0-based range regardless of gaps."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-1", name="E", questions=[_sc("q-0")]))
        # Deliberately sparse indices so we can prove reorder compacts them.
        add_question(db, "e-1", _sc("q-5"), order_index=5)
        add_question(db, "e-1", _sc("q-9"), order_index=9)

        reorder_questions(db, "e-1", ["q-9", "q-0", "q-5"])

        rows = db.execute(
            "SELECT id, order_index FROM questions WHERE exam_id = ?"
            " ORDER BY order_index ASC",
            ("e-1",),
        ).fetchall()
        assert [(row["id"], row["order_index"]) for row in rows] == [
            ("q-9", 0),
            ("q-0", 1),
            ("q-5", 2),
        ]
    finally:
        db.close()


def test_reorder_questions_is_noop_for_same_order(tmp_path: Path) -> None:
    """Passing the current order is a valid (idempotent) call."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-1", name="E", questions=[_sc("q-a"), _sc("q-b")]),
        )

        reorder_questions(db, "e-1", ["q-a", "q-b"])

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-a", "q-b"]
    finally:
        db.close()


def test_reorder_questions_raises_for_unknown_exam(tmp_path: Path) -> None:
    """Missing exam id surfaces as LookupError (consistent with add_question)."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        with pytest.raises(LookupError):
            reorder_questions(db, "missing-exam", [])
    finally:
        db.close()


def test_reorder_questions_rejects_missing_id(tmp_path: Path) -> None:
    """ordered_ids must cover every current question — missing id → ValueError."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-1", name="E", questions=[_sc("q-a"), _sc("q-b")]),
        )

        with pytest.raises(ValueError):
            reorder_questions(db, "e-1", ["q-a"])

        # Order on disk must be unchanged.
        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-a", "q-b"]
    finally:
        db.close()


def test_reorder_questions_rejects_extra_id(tmp_path: Path) -> None:
    """Ids that don't belong to the exam are rejected (no silent inserts)."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-1", name="E", questions=[_sc("q-a"), _sc("q-b")]),
        )

        with pytest.raises(ValueError):
            reorder_questions(db, "e-1", ["q-a", "q-b", "q-ghost"])

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-a", "q-b"]
    finally:
        db.close()


def test_reorder_questions_rejects_duplicate_id(tmp_path: Path) -> None:
    """Duplicates in ordered_ids cannot produce a valid permutation → ValueError."""
    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-1", name="E", questions=[_sc("q-a"), _sc("q-b")]),
        )

        with pytest.raises(ValueError):
            reorder_questions(db, "e-1", ["q-a", "q-a"])

        listed = list_questions(db, "e-1")
        assert [q.id for q in listed] == ["q-a", "q-b"]
    finally:
        db.close()



def test_add_question_persists_complexity_and_section(tmp_path: Path) -> None:
    """add_question must persist complexity + section so list_questions sees them.

    Guard for step 8.5: the INSERT statement carries the two new
    metadata columns and the SELECT in list_questions reads them back.
    Legacy rows that leave both fields ``None`` must not break listing.
    """

    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(db, Exam(id="e-meta", name="Meta", questions=[]))

        rated = _sc("q-rated").model_copy(
            update={"complexity": 4, "section": "Compute"}
        )
        bare = _sc("q-bare")  # complexity=None, section=None

        add_question(db, "e-meta", rated)
        add_question(db, "e-meta", bare)

        questions = list_questions(db, "e-meta")
        by_id = {q.id: q for q in questions}
        assert by_id["q-rated"].complexity == 4
        assert by_id["q-rated"].section == "Compute"
        assert by_id["q-bare"].complexity is None
        assert by_id["q-bare"].section is None
    finally:
        db.close()


def test_update_question_overwrites_complexity_and_section(
    tmp_path: Path,
) -> None:
    """update_question must refresh complexity + section on existing rows.

    Covers the common Designer path: user rates an unrated question
    (None → 3) and tags it with a section, then clears both. Both
    transitions must be reflected by the next read.
    """

    db = open_db(tmp_path / "exam.sqlite")
    try:
        create_exam(
            db,
            Exam(id="e-upd", name="Update", questions=[_sc("q-1")]),
        )

        # First update: set both fields.
        rated = _sc("q-1").model_copy(
            update={"complexity": 3, "section": "IAM"}
        )
        update_question(db, rated)

        after_set = list_questions(db, "e-upd")[0]
        assert after_set.complexity == 3
        assert after_set.section == "IAM"

        # Second update: clear both. Because Pydantic normalises
        # ``section=""`` to ``None`` we set it through the constructor
        # explicitly to make sure NULL round-trips too.
        cleared = _sc("q-1").model_copy(
            update={"complexity": None, "section": None}
        )
        update_question(db, cleared)

        after_clear = list_questions(db, "e-upd")[0]
        assert after_clear.complexity is None
        assert after_clear.section is None
    finally:
        db.close()
