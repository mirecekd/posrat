"""DAO for individual :class:`posrat.models.Question` mutations.

Complements :mod:`posrat.storage.exam_repo` which handles whole-exam
operations. The helpers here let the Designer work on one question at a
time without rewriting the entire exam row. All question types
(``single_choice``, ``multi_choice``, ``hotspot``) are supported since
step 2.10.
"""

from __future__ import annotations

import sqlite3

from posrat.models import Question
from posrat.storage.exam_repo import _insert_question_children, _load_question


def add_question(
    db: sqlite3.Connection,
    exam_id: str,
    question: Question,
    order_index: int | None = None,
) -> int:
    """Append (or insert at ``order_index``) ``question`` into ``exam_id``.

    When ``order_index`` is omitted the question is appended after the
    current maximum ``order_index`` for the exam. Returns the
    ``order_index`` actually used.

    Raises :class:`LookupError` when the exam does not exist and
    :class:`sqlite3.IntegrityError` when the question id collides.
    """
    exam_row = db.execute(
        "SELECT id FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    if exam_row is None:
        raise LookupError(f"exam id not found: {exam_id!r}")

    if order_index is None:
        (max_index,) = db.execute(
            "SELECT COALESCE(MAX(order_index), -1) FROM questions"
            " WHERE exam_id = ?",
            (exam_id,),
        ).fetchone()
        order_index = int(max_index) + 1

    with db:
        db.execute(
            "INSERT INTO questions (id, exam_id, type, text, explanation,"
            " image_path, order_index, complexity, section, allow_shuffle)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                question.id,
                exam_id,
                question.type,
                question.text,
                question.explanation,
                question.image_path,
                order_index,
                question.complexity,
                question.section,
                1 if question.allow_shuffle else 0,
            ),
        )
        _insert_question_children(db, question)
    return order_index




def update_question(db: sqlite3.Connection, question: Question) -> None:
    """Replace stored text/explanation/image/payload of ``question``.

    ``order_index`` and ``exam_id`` are preserved. Choices, hotspot
    options and hotspot steps are fully replaced (delete-all +
    re-insert) because the Designer treats these lists as atomic units.
    The question ``type`` can be changed freely; Pydantic validation on
    the caller side ensures the new payload shape is consistent.

    Raises :class:`LookupError` when the question id is unknown.
    """
    existing = db.execute(
        "SELECT id FROM questions WHERE id = ?", (question.id,)
    ).fetchone()
    if existing is None:
        raise LookupError(f"question id not found: {question.id!r}")

    with db:
        db.execute(
            "UPDATE questions SET type = ?, text = ?, explanation = ?,"
            " image_path = ?, complexity = ?, section = ?,"
            " allow_shuffle = ? WHERE id = ?",
            (
                question.type,
                question.text,
                question.explanation,
                question.image_path,
                question.complexity,
                question.section,
                1 if question.allow_shuffle else 0,
                question.id,
            ),
        )


        db.execute("DELETE FROM choices WHERE question_id = ?", (question.id,))
        db.execute(
            "DELETE FROM hotspot_steps WHERE question_id = ?", (question.id,)
        )
        db.execute(
            "DELETE FROM hotspot_options WHERE question_id = ?",
            (question.id,),
        )
        _insert_question_children(db, question)


def delete_question(db: sqlite3.Connection, question_id: str) -> bool:
    """Delete the question with ``question_id`` and cascade its payload.

    Returns ``True`` when a row was removed, ``False`` when the question
    did not exist (so callers can distinguish idempotent no-ops).
    """
    with db:
        cursor = db.execute(
            "DELETE FROM questions WHERE id = ?", (question_id,)
        )
    return cursor.rowcount > 0


def list_questions(db: sqlite3.Connection, exam_id: str) -> list[Question]:
    """Return questions of ``exam_id`` ordered by ``order_index`` ascending.

    The function does not distinguish between "exam missing" and "exam
    exists but is empty" — both yield an empty list. Callers needing
    that distinction can cross-check via :func:`posrat.storage.get_exam`.
    """
    question_rows = db.execute(
        "SELECT id, type, text, explanation, image_path, complexity, section,"
        " allow_shuffle"
        " FROM questions WHERE exam_id = ?"
        " ORDER BY order_index ASC, id ASC",
        (exam_id,),
    ).fetchall()
    return [_load_question(db, q_row) for q_row in question_rows]




def reorder_questions(
    db: sqlite3.Connection,
    exam_id: str,
    ordered_ids: list[str],
) -> None:
    """Rewrite ``order_index`` of ``exam_id`` questions to match ``ordered_ids``.

    ``ordered_ids`` must be a permutation of the exam's current question
    ids (same set, no missing / extra). On success every question row in
    the exam has its ``order_index`` rewritten to ``0, 1, 2, …`` in the
    supplied order, so :func:`list_questions` will return them in that
    exact sequence afterwards. A dense 0-based range keeps future
    :func:`add_question` appends predictable (``MAX(order_index)+1`` stays
    tight against the real question count).

    Raises :class:`LookupError` when the exam itself does not exist and
    :class:`ValueError` when ``ordered_ids`` is not a permutation of the
    current question ids (missing ids, unknown ids, or duplicates). The
    whole reorder runs inside a single transaction so a validation
    failure leaves ``order_index`` untouched.
    """
    exam_row = db.execute(
        "SELECT id FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    if exam_row is None:
        raise LookupError(f"exam id not found: {exam_id!r}")

    current_rows = db.execute(
        "SELECT id FROM questions WHERE exam_id = ?", (exam_id,)
    ).fetchall()
    current_ids = {row["id"] for row in current_rows}

    if len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError(
            f"reorder list contains duplicates: {ordered_ids!r}"
        )
    if set(ordered_ids) != current_ids:
        missing = current_ids - set(ordered_ids)
        extra = set(ordered_ids) - current_ids
        raise ValueError(
            "reorder list does not match current questions "
            f"(missing={sorted(missing)!r}, extra={sorted(extra)!r})"
        )

    with db:
        for new_index, question_id in enumerate(ordered_ids):
            db.execute(
                "UPDATE questions SET order_index = ?"
                " WHERE id = ? AND exam_id = ?",
                (new_index, question_id, exam_id),
            )

