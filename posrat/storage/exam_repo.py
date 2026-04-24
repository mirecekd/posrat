"""DAO for :class:`posrat.models.Exam` persistence.

- :func:`create_exam` — insert the exam row plus its questions, choices
  and hotspot payload in a single transaction.
- :func:`get_exam` — reconstruct an :class:`~posrat.models.Exam` from the
  database, preserving question/choice/option/step order via
  ``order_index``.

Hotspot persistence was added in step 2.10 on top of the schema
migration ``v5`` (``hotspot_options`` + ``hotspot_steps`` tables).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from posrat.models import Choice, Exam, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep


def _now_iso_utc() -> str:
    """Return the current UTC timestamp as an ISO-8601 string with ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_question_children(
    db: sqlite3.Connection,
    question: Question,
) -> None:
    """Insert choices and/or hotspot payload for an already-inserted question."""
    for c_index, choice in enumerate(question.choices):
        db.execute(
            "INSERT INTO choices (id, question_id, text, is_correct,"
            " order_index) VALUES (?, ?, ?, ?, ?)",
            (
                choice.id,
                question.id,
                choice.text,
                1 if choice.is_correct else 0,
                c_index,
            ),
        )
    if question.hotspot is not None:
        for o_index, option in enumerate(question.hotspot.options):
            db.execute(
                "INSERT INTO hotspot_options (id, question_id, text,"
                " order_index) VALUES (?, ?, ?, ?)",
                (option.id, question.id, option.text, o_index),
            )
        for s_index, step in enumerate(question.hotspot.steps):
            db.execute(
                "INSERT INTO hotspot_steps (id, question_id, prompt,"
                " correct_option_id, order_index)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    step.id,
                    question.id,
                    step.prompt,
                    step.correct_option_id,
                    s_index,
                ),
            )


def _load_hotspot(db: sqlite3.Connection, question_id: str) -> Hotspot:
    """Return the :class:`Hotspot` payload stored for ``question_id``."""
    option_rows = db.execute(
        "SELECT id, text FROM hotspot_options WHERE question_id = ?"
        " ORDER BY order_index ASC, id ASC",
        (question_id,),
    ).fetchall()
    step_rows = db.execute(
        "SELECT id, prompt, correct_option_id FROM hotspot_steps"
        " WHERE question_id = ? ORDER BY order_index ASC, id ASC",
        (question_id,),
    ).fetchall()
    return Hotspot(
        options=[
            HotspotOption(id=o_row["id"], text=o_row["text"])
            for o_row in option_rows
        ],
        steps=[
            HotspotStep(
                id=s_row["id"],
                prompt=s_row["prompt"],
                correct_option_id=s_row["correct_option_id"],
            )
            for s_row in step_rows
        ],
    )


def _load_question(db: sqlite3.Connection, q_row: sqlite3.Row) -> Question:
    """Reconstruct a :class:`Question` from a ``questions`` row.

    Expects ``q_row`` to carry the v8 metadata columns (``complexity``
    and ``section``) alongside the v3 core. Callers in this module all
    read them via ``SELECT … complexity, section …``; external callers
    must update their SELECT to match before piping rows in.
    """

    if q_row["type"] == "hotspot":
        hotspot = _load_hotspot(db, q_row["id"])
        return Question(
            id=q_row["id"],
            type=q_row["type"],
            text=q_row["text"],
            explanation=q_row["explanation"],
            image_path=q_row["image_path"],
            choices=[],
            hotspot=hotspot,
            complexity=q_row["complexity"],
            section=q_row["section"],
            allow_shuffle=bool(q_row["allow_shuffle"]),
        )

    choice_rows = db.execute(
        "SELECT id, text, is_correct FROM choices"
        " WHERE question_id = ? ORDER BY order_index ASC, id ASC",
        (q_row["id"],),
    ).fetchall()
    return Question(
        id=q_row["id"],
        type=q_row["type"],
        text=q_row["text"],
        explanation=q_row["explanation"],
        image_path=q_row["image_path"],
        choices=[
            Choice(
                id=c_row["id"],
                text=c_row["text"],
                is_correct=bool(c_row["is_correct"]),
            )
            for c_row in choice_rows
        ],
        hotspot=None,
        complexity=q_row["complexity"],
        section=q_row["section"],
        allow_shuffle=bool(q_row["allow_shuffle"]),
    )




def create_exam(db: sqlite3.Connection, exam: Exam) -> None:
    """Persist ``exam`` (row + every question + its payload) atomically.

    Also persists the Phase 7A Runner metadata columns
    (``default_question_count`` / ``time_limit_minutes`` / ``passing_score``
    / ``target_score``) — all are ``Optional[int]`` on the model, and the
    v10 schema stores ``NULL`` for unset values, so legacy exams built
    with only the core four fields round-trip unchanged.

    Raises :class:`sqlite3.IntegrityError` when the exam id already exists.
    """
    created_at = _now_iso_utc()
    with db:
        db.execute(
            "INSERT INTO exams (id, name, description, created_at,"
            " default_question_count, time_limit_minutes, passing_score,"
            " target_score)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exam.id,
                exam.name,
                exam.description,
                created_at,
                exam.default_question_count,
                exam.time_limit_minutes,
                exam.passing_score,
                exam.target_score,
            ),
        )

        for q_index, question in enumerate(exam.questions):
            db.execute(
                "INSERT INTO questions (id, exam_id, type, text,"
                " explanation, image_path, order_index, complexity, section,"
                " allow_shuffle)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    question.id,
                    exam.id,
                    question.type,
                    question.text,
                    question.explanation,
                    question.image_path,
                    q_index,
                    question.complexity,
                    question.section,
                    1 if question.allow_shuffle else 0,
                ),
            )
            _insert_question_children(db, question)


def get_exam(db: sqlite3.Connection, exam_id: str) -> Exam | None:
    """Return the :class:`Exam` with ``exam_id`` or ``None`` when missing.

    Reads the Phase 7A Runner metadata columns (``default_question_count``
    / ``time_limit_minutes`` / ``passing_score`` / ``target_score``) —
    ``NULL`` in SQLite maps to ``None`` on the model so legacy exams
    built before the v10 migration keep rehydrating unchanged.
    """
    exam_row = db.execute(
        "SELECT id, name, description, default_question_count,"
        " time_limit_minutes, passing_score, target_score"
        " FROM exams WHERE id = ?",
        (exam_id,),
    ).fetchone()
    if exam_row is None:
        return None

    question_rows = db.execute(
        "SELECT id, type, text, explanation, image_path, complexity, section,"
        " allow_shuffle"
        " FROM questions WHERE exam_id = ? ORDER BY order_index ASC, id ASC",
        (exam_id,),
    ).fetchall()

    questions = [_load_question(db, q_row) for q_row in question_rows]

    return Exam(
        id=exam_row["id"],
        name=exam_row["name"],
        description=exam_row["description"],
        questions=questions,
        default_question_count=exam_row["default_question_count"],
        time_limit_minutes=exam_row["time_limit_minutes"],
        passing_score=exam_row["passing_score"],
        target_score=exam_row["target_score"],
    )

