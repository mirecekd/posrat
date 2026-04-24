"""High-level Runner orchestration primitives.

These helpers compose the low-level DAO (``start_session`` /
``record_answer`` / ``finish_session``) and the Runner's pure sampler
into the two operations the UI actually performs:

* :func:`start_runner_session` — pick N questions, persist a new
  :class:`~posrat.models.Session` row with snapshot metadata and
  return the session id + sampled question ids so the Runner page can
  stash them in ``app.storage.user`` for resume.
* :func:`submit_runner_answer` — grade the candidate's payload via
  :func:`posrat.runner.grading.grade_answer` and persist the result.
* :func:`compute_session_score` — derive score / pass-fail / percent
  from a session's accumulated answers and its snapshotted
  passing/target score. Used by both the training mode feedback (8.1)
  and the results summary (8.3).

The orchestrator deliberately owns no NiceGUI state; every argument
comes in as a value and every side-effect goes through the DAO layer.
That keeps the business rules testable without booting a UI.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from posrat.models import Session, SessionMode
from posrat.runner.grading import grade_answer
from posrat.runner.sampler import (
    sample_question_ids,
    select_questions_by_range,
)
from posrat.storage import (
    list_questions,
    open_db,
    record_answer,
    start_session,
)


# --------------------------------------------------------------------------- #
# Question-selection strategies (Phase 12 — mode dialog Visual CertExam port) #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SelectAll:
    """Take N random questions from the whole exam pool.

    ``count = None`` means "take every question" (full shuffle). When
    ``count`` exceeds the exam pool size the sampler clamps silently —
    see :func:`posrat.runner.sampler.sample_question_ids`.
    """

    count: Optional[int] = None


@dataclass(frozen=True)
class SelectRange:
    """Take the inclusive 1-based slice ``start..end`` of the exam pool.

    Mirrors the VCE "Take question range from X to Y" dialog option
    shown on the screenshot used when planning this feature. Order is
    preserved — the sampler does not shuffle a hand-picked range.
    """

    start: int
    end: int


@dataclass(frozen=True)
class SelectIncorrect:
    """Take questions the current candidate answered wrong ≥ N times.

    The orchestrator queries the exam DB for finished sessions
    belonging to ``candidate_name`` (the same user that's about to
    run this session) and picks every question where the total
    ``is_correct = 0`` count is at least ``min_wrong_count``.
    """

    min_wrong_count: int = 1


QuestionSelection = Union[SelectAll, SelectRange, SelectIncorrect]



#: Default raw-point "100 %" mark applied to sessions when neither the
#: exam nor the caller provided a ``target_score``. 1000 matches the
#: Visual CertExam convention and keeps pass/fail evaluation always
#: active so the Runner never silently falls into "no verdict" mode.
DEFAULT_TARGET_SCORE = 1000

#: Default passing threshold applied when the exam and caller leave
#: ``passing_score`` unset. 700 / 1000 = 70 %, matching the most common
#: AWS / Microsoft / CompTIA certification cut-offs.
DEFAULT_PASSING_SCORE = 700



def list_incorrect_question_ids(
    db: sqlite3.Connection,
    *,
    exam_id: str,
    candidate_name: str,
    min_wrong_count: int,
) -> list[str]:
    """Return ids of questions the candidate answered wrong ≥ N times.

    Scope:

    * Counts only **finished** sessions (``finished_at IS NOT NULL``)
      so in-progress attempts don't warp the statistic.
    * Filters by ``candidate_name`` — the "questions *I* answered
      incorrectly" semantics from the VCE screenshot.
    * Dedups per ``(session_id, question_id)`` at the
      :func:`record_answer` DAO layer already, so a plain ``COUNT(*)``
      here yields the number of distinct *sessions* that got it wrong.

    Returns ids in ``order_index`` order (same as
    :func:`posrat.storage.list_questions`) so the downstream sampler
    keeps a natural browsing order. Empty list when nothing matches —
    the caller handles the "no questions found" UX.
    """

    if min_wrong_count < 1:
        raise ValueError(
            f"min_wrong_count must be >= 1, got {min_wrong_count}"
        )

    rows = db.execute(
        """
        SELECT a.question_id, COUNT(*) AS wrong_count
        FROM answers a
        JOIN sessions s ON s.id = a.session_id
        JOIN questions q ON q.id = a.question_id
        WHERE s.exam_id = ?
          AND s.candidate_name = ?
          AND s.finished_at IS NOT NULL
          AND a.is_correct = 0
        GROUP BY a.question_id
        HAVING wrong_count >= ?
        ORDER BY q.order_index ASC, a.question_id ASC
        """,
        (exam_id, candidate_name, min_wrong_count),
    ).fetchall()
    return [str(row["question_id"]) for row in rows]


def _resolve_selection(
    *,
    db: sqlite3.Connection,
    exam_id: str,
    candidate_name: str,
    questions,
    selection: QuestionSelection,
    rng: Optional[random.Random],
) -> list[str]:
    """Dispatch to the concrete sampler based on ``selection`` kind."""

    if isinstance(selection, SelectAll):
        return sample_question_ids(questions, selection.count, rng=rng)
    if isinstance(selection, SelectRange):
        return select_questions_by_range(
            questions, start=selection.start, end=selection.end
        )
    if isinstance(selection, SelectIncorrect):
        ids = list_incorrect_question_ids(
            db,
            exam_id=exam_id,
            candidate_name=candidate_name,
            min_wrong_count=selection.min_wrong_count,
        )
        # Intersect with the current question pool so a stale id (e.g.
        # an old answer row whose question has been deleted in the
        # Designer) doesn't crash the Runner when the question is
        # fetched later. ``questions`` is already order_index sorted.
        pool_ids = {q.id for q in questions}
        return [qid for qid in ids if qid in pool_ids]
    raise TypeError(f"unknown selection type: {type(selection).__name__}")


@dataclass(frozen=True)
class StartedSession:
    """Output of :func:`start_runner_session`.


    Bundles the persisted :class:`Session` with the order in which the
    Runner should present questions. The list is stashed verbatim in
    ``app.storage.user`` so resume after a page reload re-renders the
    same order.
    """

    session: Session
    question_ids: list[str]


def start_runner_session(
    db_path: Path,
    *,
    exam_id: str,
    mode: SessionMode,
    candidate_name: str,
    question_count: Optional[int] = None,
    selection: Optional[QuestionSelection] = None,
    time_limit_minutes: Optional[int] = None,
    passing_score: Optional[int] = None,
    target_score: Optional[int] = None,
    session_id: Optional[str] = None,
    started_at: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> StartedSession:
    """Create a new Runner session and return the selected question ids.

    Opens the exam DB at ``db_path``, picks question ids using one of
    the three :data:`QuestionSelection` strategies, and calls
    :func:`start_session` with every snapshot kwarg.

    ``selection`` is the canonical way to control which questions the
    session picks up. When omitted we fall back to the legacy
    ``question_count`` alias (``SelectAll(count=question_count)``) so
    existing callers keep compiling.

    Raises:

    * :class:`LookupError` — ``exam_id`` does not exist.
    * :class:`ValueError` — selection is empty / malformed, or the
      exam has no questions at all.
    * :class:`sqlite3.DatabaseError` — underlying I/O failure.

    ``session_id`` / ``started_at`` / ``rng`` are dependency-injection
    seams for tests — production callers leave them at their defaults.

    **Scoring defaults.** When neither the exam nor the caller
    supplied ``target_score`` / ``passing_score`` we apply
    :data:`DEFAULT_TARGET_SCORE` (1000) and :data:`DEFAULT_PASSING_SCORE`
    (700) so every session always evaluates to a pass/fail verdict.
    If the caller only set ``target_score`` we scale the default
    passing score proportionally (70 % of the override) so a custom
    1200-point exam still defaults to a 840-point pass mark.
    """

    if target_score is None:
        target_score = DEFAULT_TARGET_SCORE
    if passing_score is None:
        # 70 % of the effective target_score. For the default 1000
        # this yields 700; for a custom 1200 it yields 840. int()
        # truncates so the pass mark never rounds *up* into a pass.
        passing_score = int(target_score * DEFAULT_PASSING_SCORE / DEFAULT_TARGET_SCORE)

    # ``selection`` wins when both are given; legacy callers pass only
    # ``question_count`` and we upgrade it to the equivalent SelectAll
    # so the rest of the function has a single code path.
    if selection is None:
        selection = SelectAll(count=question_count)

    db = open_db(db_path)

    try:
        exam_row = db.execute(
            "SELECT id FROM exams WHERE id = ?", (exam_id,)
        ).fetchone()
        if exam_row is None:
            raise LookupError(f"exam id not found: {exam_id!r}")

        questions = list_questions(db, exam_id)
        if not questions:
            raise ValueError(
                f"exam {exam_id!r} has no questions to run"
            )

        sampled_ids = _resolve_selection(
            db=db,
            exam_id=exam_id,
            candidate_name=candidate_name,
            questions=questions,
            selection=selection,
            rng=rng,
        )
        if not sampled_ids:
            raise ValueError(
                "selection produced no questions — cannot start a session"
            )
        # The actual size persisted in the snapshot is the *effective*
        # question count (post-sampling/range/filter). Users who asked
        # for "65 from a 50-question exam" get 50 recorded, not 65.
        effective_count = len(sampled_ids)


        session = start_session(
            db,
            exam_id=exam_id,
            mode=mode,
            session_id=session_id,
            started_at=started_at,
            candidate_name=candidate_name,
            question_count=effective_count,
            time_limit_minutes=time_limit_minutes,
            passing_score=passing_score,
            target_score=target_score,
        )
    finally:
        db.close()

    return StartedSession(session=session, question_ids=sampled_ids)


def submit_runner_answer(
    db_path: Path,
    *,
    session_id: str,
    question_id: str,
    payload: Any,
    time_ms: Optional[int] = None,
    answer_id: Optional[str] = None,
) -> tuple[bool, str]:
    """Grade ``payload`` and persist the resulting :class:`Answer`.

    Looks up the question (must belong to the session's exam) and runs
    it through :func:`posrat.runner.grading.grade_answer`. Returns the
    ``(is_correct, given_json)`` tuple so the caller can render
    training-mode feedback without a second DB round-trip.

    Raises:

    * :class:`LookupError` — session or question not found.
    * :class:`ValueError` — malformed ``payload`` or unknown question
      type (surfaced from :mod:`posrat.runner.grading`).
    """

    db = open_db(db_path)
    try:
        q_row = db.execute(
            "SELECT exam_id FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        if q_row is None:
            raise LookupError(f"question id not found: {question_id!r}")

        # Load the question with full payload via list_questions + filter
        # rather than a second hand-crafted SELECT, so the Runner uses
        # the same deserialisation path as every other DAO consumer.
        exam_questions = list_questions(db, q_row["exam_id"])
        question = next(
            (q for q in exam_questions if q.id == question_id), None
        )
        if question is None:  # pragma: no cover - race-only
            raise LookupError(
                f"question id {question_id!r} disappeared mid-grading"
            )

        is_correct, given_json = grade_answer(question, payload)

        record_answer(
            db,
            session_id=session_id,
            question_id=question_id,
            given_json=given_json,
            is_correct=is_correct,
            time_ms=time_ms,
            answer_id=answer_id,
        )
    finally:
        db.close()

    return is_correct, given_json


@dataclass(frozen=True)
class SessionScore:
    """Derived scoring summary for a :class:`Session`.

    * ``correct_count`` / ``total_count`` — raw answer tally (the
      session may still be in progress, in which case
      ``total_count < snapshotted question_count``).
    * ``percent`` — ``correct_count / total_count * 100`` as a float,
      or ``None`` when ``total_count == 0``.
    * ``raw_score`` — scaled raw points when ``target_score`` is
      known: ``percent / 100 * target_score``. Otherwise ``None``.
    * ``passed`` — ``True`` when ``raw_score >= passing_score``,
      ``False`` when the threshold was not met, ``None`` when the exam
      has no pass/fail criterion (either ``passing_score`` or
      ``target_score`` missing).
    """

    correct_count: int
    total_count: int
    percent: Optional[float]
    raw_score: Optional[int]
    passed: Optional[bool]


def compute_session_score(
    session: Session,
    *,
    answers: Optional[Sequence] = None,
) -> SessionScore:
    """Derive a :class:`SessionScore` from ``session``.

    Scoring rules (Baby-Steps™ version of the VCE contract):

    * **Per question: all-or-nothing.** A question is ``is_correct``
      (binary) based on :func:`~posrat.runner.grading.grade_answer`; no
      partial credit even for multi-answer questions.
    * **Total count = session snapshot.** We use
      ``session.question_count`` (pinned at ``start_session``) as the
      denominator when available. Unanswered questions therefore count
      toward the total — stepping through 5 of 65 and seeing 4/65 on
      the results page tells the candidate exactly where they are. If
      the snapshot is missing (legacy sessions) we fall back to
      ``len(answers)`` to preserve backwards compatibility.
    * **Dedup per question id.** The ``record_answer`` DAO already
      replaces on re-submit, but we still ``dict``-dedupe here as a
      safety net so hand-edited DBs with duplicate rows don't tilt the
      tally.
    * **Raw score truncates.** 69.9 % of 1000 → 699, matching the
      conservative VCE convention so borderline candidates do not
      accidentally "round up" into a pass.

    The ``answers`` override is a test seam for scoring-only tests
    that don't want to materialise a full :class:`Session`.
    """

    raw_answers = list(answers) if answers is not None else list(session.answers)

    # Dedup per question_id: later submissions win. record_answer
    # already replaces, so the on-disk state has at most one row per
    # (session, question) — but legacy / hand-edited DBs might break
    # that. dict over question_id preserves insertion order which
    # matches rowid ordering from _load_answers.
    by_question: dict[str, object] = {}
    for a in raw_answers:
        by_question[a.question_id] = a
    recorded = list(by_question.values())

    answered_count = len(recorded)

    # Denominator: prefer the session's pinned snapshot so unanswered
    # questions count as wrong (VCE-style behaviour). Legacy sessions
    # without a snapshot fall back to the number of recorded answers.
    total = session.question_count if session.question_count is not None else answered_count

    if total == 0:
        return SessionScore(
            correct_count=0,
            total_count=0,
            percent=None,
            raw_score=None,
            passed=None,
        )

    correct = sum(1 for a in recorded if a.is_correct)
    percent = correct / total * 100.0

    raw_score: Optional[int] = None
    if session.target_score is not None:
        raw_score = int(percent / 100.0 * session.target_score)

    passed: Optional[bool] = None
    if session.passing_score is not None and raw_score is not None:
        passed = raw_score >= session.passing_score

    return SessionScore(
        correct_count=correct,
        total_count=total,
        percent=percent,
        raw_score=raw_score,
        passed=passed,
    )



# Re-use the module-level constant so callers can spot "no-op" for
# time budgets without re-deriving it. The pattern mirrors how the
# Designer uses ``SAVED_LABEL_TEXT`` etc.
__all__ = [
    "QuestionSelection",
    "SelectAll",
    "SelectIncorrect",
    "SelectRange",
    "SessionScore",
    "StartedSession",
    "compute_session_score",
    "list_incorrect_question_ids",
    "start_runner_session",
    "submit_runner_answer",
]

