"""DAO for :class:`posrat.models.Session` and :class:`posrat.models.Answer`.

Sessions record a single training or exam attempt at a given exam. The
Runner (Phase 7) is the primary producer: it calls :func:`start_session`
when the user picks an exam, :func:`record_answer` after each question,
and :func:`finish_session` at the end. The Designer never writes here.

All mutations go through the ``with db:`` context manager so a failure
half-way through rolls back cleanly.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from posrat.models import Answer, Session, SessionMode


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 (``...Z``) form."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def start_session(
    db: sqlite3.Connection,
    exam_id: str,
    mode: SessionMode,
    session_id: Optional[str] = None,
    started_at: Optional[str] = None,
    *,
    candidate_name: Optional[str] = None,
    question_count: Optional[int] = None,
    time_limit_minutes: Optional[int] = None,
    passing_score: Optional[int] = None,
    target_score: Optional[int] = None,
) -> Session:
    """Create and persist a new session for ``exam_id``.

    ``session_id`` and ``started_at`` are accepted for tests that need
    deterministic values; in production both default to a fresh UUID and
    the current UTC timestamp respectively.

    The remaining keyword-only fields are the Phase 7B snapshot that
    pins the exam's run-time configuration at session-start time. The
    Runner populates them from the exam's metadata + the user's mode
    dialog input; callers that do not care (e.g. legacy tests) can
    omit them all and the session just persists ``NULL`` in every
    snapshot column.

    Raises :class:`LookupError` when the exam does not exist.
    """
    exam_row = db.execute(
        "SELECT id FROM exams WHERE id = ?", (exam_id,)
    ).fetchone()
    if exam_row is None:
        raise LookupError(f"exam id not found: {exam_id!r}")

    session = Session(
        id=session_id or str(uuid.uuid4()),
        exam_id=exam_id,
        mode=mode,
        started_at=started_at or _utc_now_iso(),
        finished_at=None,
        candidate_name=candidate_name,
        question_count=question_count,
        time_limit_minutes=time_limit_minutes,
        passing_score=passing_score,
        target_score=target_score,
    )

    with db:
        db.execute(
            "INSERT INTO sessions (id, exam_id, mode, started_at,"
            " finished_at, candidate_name, question_count,"
            " time_limit_minutes, passing_score, target_score)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.exam_id,
                session.mode,
                session.started_at,
                session.finished_at,
                session.candidate_name,
                session.question_count,
                session.time_limit_minutes,
                session.passing_score,
                session.target_score,
            ),
        )
    return session


def finish_session(
    db: sqlite3.Connection,
    session_id: str,
    finished_at: Optional[str] = None,
) -> None:
    """Mark the given session as finished.

    Idempotent when the session is already finished (the timestamp is
    overwritten with the new value). Raises :class:`LookupError` when
    the session does not exist.
    """
    row = db.execute(
        "SELECT id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"session id not found: {session_id!r}")

    with db:
        db.execute(
            "UPDATE sessions SET finished_at = ? WHERE id = ?",
            (finished_at or _utc_now_iso(), session_id),
        )


def _load_answers(
    db: sqlite3.Connection, session_id: str
) -> list[Answer]:
    rows = db.execute(
        "SELECT id, question_id, given_json, is_correct, time_ms"
        " FROM answers WHERE session_id = ? ORDER BY rowid ASC",
        (session_id,),
    ).fetchall()
    return [
        Answer(
            id=row["id"],
            session_id=session_id,
            question_id=row["question_id"],
            given_json=row["given_json"],
            is_correct=bool(row["is_correct"]),
            time_ms=row["time_ms"],
        )
        for row in rows
    ]


#: SELECT clause used in every session-row load. Extracted as a module
#: constant so :func:`get_session` / :func:`list_sessions` (and any
#: future listing helpers) stay in lock-step — forgetting to update one
#: of them would silently drop snapshot fields.
_SESSION_SELECT_COLUMNS = (
    "id, exam_id, mode, started_at, finished_at,"
    " candidate_name, question_count, time_limit_minutes,"
    " passing_score, target_score"
)


def _row_to_session(
    db: sqlite3.Connection, row: sqlite3.Row
) -> Session:
    """Hydrate a single ``sessions`` row (plus its answers) into a :class:`Session`."""

    return Session(
        id=row["id"],
        exam_id=row["exam_id"],
        mode=row["mode"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        candidate_name=row["candidate_name"],
        question_count=row["question_count"],
        time_limit_minutes=row["time_limit_minutes"],
        passing_score=row["passing_score"],
        target_score=row["target_score"],
        answers=_load_answers(db, row["id"]),
    )


def get_session(
    db: sqlite3.Connection, session_id: str
) -> Session | None:
    """Return the session with ``session_id`` (plus its answers) or ``None``."""
    row = db.execute(
        f"SELECT {_SESSION_SELECT_COLUMNS} FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(db, row)


def list_sessions(
    db: sqlite3.Connection, exam_id: str
) -> list[Session]:
    """Return sessions for ``exam_id`` ordered by ``started_at`` ascending.

    Answers are loaded eagerly so each returned :class:`Session` is
    complete. For lightweight listings a future caller may want a
    header-only variant; that can live alongside this one later.
    """
    rows = db.execute(
        f"SELECT {_SESSION_SELECT_COLUMNS} FROM sessions"
        " WHERE exam_id = ? ORDER BY started_at ASC, id ASC",
        (exam_id,),
    ).fetchall()
    return [_row_to_session(db, row) for row in rows]


def record_answer(
    db: sqlite3.Connection,
    session_id: str,
    question_id: str,
    given_json: str,
    is_correct: bool,
    time_ms: Optional[int] = None,
    answer_id: Optional[str] = None,
) -> Answer:
    """Persist a single :class:`Answer` for ``session_id``.

    ``given_json`` is validated as parseable JSON via the
    :class:`~posrat.models.Answer` model before the DB insert so invalid
    payloads fail fast without touching SQLite.

    **Replace semantics.** A question has at most one answer per
    session: when the Runner resubmits (prev/next revisit with a new
    pick), we delete the previous answer for ``(session_id,
    question_id)`` inside the same transaction and then insert the
    fresh row. This keeps :func:`compute_session_score` honest —
    otherwise a double-answered question would show up twice in the
    tally and tilt the percentage ("half correct" even though a
    question is strictly correct or incorrect).

    Raises :class:`LookupError` when either the session or the question
    does not exist.
    """
    sess_row = db.execute(
        "SELECT id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if sess_row is None:
        raise LookupError(f"session id not found: {session_id!r}")

    q_row = db.execute(
        "SELECT id FROM questions WHERE id = ?", (question_id,)
    ).fetchone()
    if q_row is None:
        raise LookupError(f"question id not found: {question_id!r}")

    answer = Answer(
        id=answer_id or str(uuid.uuid4()),
        session_id=session_id,
        question_id=question_id,
        given_json=given_json,
        is_correct=is_correct,
        time_ms=time_ms,
    )

    with db:
        # Drop any earlier answer for this (session, question) pair so
        # re-submissions overwrite rather than accumulate.
        db.execute(
            "DELETE FROM answers"
            " WHERE session_id = ? AND question_id = ?",
            (session_id, question_id),
        )
        db.execute(
            "INSERT INTO answers (id, session_id, question_id, given_json,"
            " is_correct, time_ms) VALUES (?, ?, ?, ?, ?, ?)",
            (
                answer.id,
                answer.session_id,
                answer.question_id,
                answer.given_json,
                1 if answer.is_correct else 0,
                answer.time_ms,
            ),
        )
    return answer

