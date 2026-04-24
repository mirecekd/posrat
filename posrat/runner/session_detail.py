"""Pure helper that hydrates a finished session into per-question review rows.

The Runner's history panel lets the candidate drill into one past
attempt and see — per question — what they picked, what the correct
answer was, and whether the item was graded correct. This module owns
the read-only load that powers that view; the rendering lives in
:mod:`posrat.runner.session_detail_view`.

Why a dedicated module? Three reasons:

1. Keeps the DB I/O + decoding in one place that is easy to test
   without a UI (we can point the helper at a temp ``.sqlite`` and
   assert the returned dataclasses).
2. Decouples the view from the storage layout — the renderer only
   ever sees :class:`QuestionReview` dataclasses, so changing how
   answers are stored does not cascade into NiceGUI code.
3. Guarantees the UI never has to re-parse ``given_json`` or chase a
   question by id; every lookup happens here once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from posrat.models import Answer, Question, Session
from posrat.runner.grading import decode_given_json
from posrat.runner.orchestrator import SessionScore, compute_session_score
from posrat.storage import get_session, list_questions, open_db


@dataclass(frozen=True)
class QuestionReview:
    """One row of the session detail view — a single graded question.

    * ``index`` — 1-based position in the order answers were recorded
      (matches the Runner's presentation order because ``_load_answers``
      returns rows by ``rowid ASC``).
    * ``question`` — the full :class:`Question` so the view can render
      every choice / hotspot step without another DB call.
    * ``answer`` — the persisted :class:`Answer` row when the candidate
      submitted a response; ``None`` for questions that were scheduled
      but never answered (still counted as wrong by the snapshot-based
      denominator, see :func:`compute_session_score`).
    * ``given_payload`` — decoded ``given_json`` dict (``choice_id`` /
      ``choice_ids`` / ``step_option_ids``). ``None`` when ``answer`` is
      ``None`` or the payload is malformed.
    """

    index: int
    question: Question
    answer: Optional[Answer]
    given_payload: Optional[Any]

    @property
    def is_correct(self) -> bool:
        """Return ``True`` when the candidate got this question right."""

        return bool(self.answer and self.answer.is_correct)

    @property
    def was_answered(self) -> bool:
        """Return ``True`` when the candidate submitted an answer at all."""

        return self.answer is not None


@dataclass(frozen=True)
class SessionDetail:
    """Everything the session-detail view renders in one package.

    Built by :func:`load_session_detail` so the view layer is pure
    presentation. Fields mirror what the history card already shows
    (exam name, score) plus the per-question :class:`QuestionReview`
    list and a count of unanswered items for the "X unanswered"
    footer note.
    """

    exam_path: Path
    exam_id: str
    exam_name: str
    session: Session
    score: SessionScore
    reviews: list[QuestionReview]
    unanswered_count: int


def _decode_payload_safely(given_json: str) -> Optional[Any]:
    """Return the decoded payload, or ``None`` when the JSON is bad.

    Grading already rejects malformed ``given_json`` at submit time,
    but defending against hand-edited DBs keeps the detail view robust
    — a single corrupted row should not blank the whole screen.
    """

    try:
        return decode_given_json(given_json)
    except ValueError:
        return None


def load_session_detail(
    exam_path: Path, session_id: str
) -> Optional[SessionDetail]:
    """Load a finished-or-in-progress session and build per-question reviews.

    Returns ``None`` when the exam file is missing, when the session
    does not exist inside it, or when the DB has no exam header row
    (stray ``.sqlite`` in the data dir). The caller — the history
    panel's drill-down button — renders a friendly error in that case.
    """

    if not exam_path.is_file():
        return None

    db = open_db(exam_path)
    try:
        exam_row = db.execute(
            "SELECT id, name FROM exams LIMIT 1"
        ).fetchone()
        if exam_row is None:
            return None
        exam_id = str(exam_row["id"])
        exam_name = str(exam_row["name"])

        session = get_session(db, session_id)
        if session is None:
            return None

        # Pre-index the exam's questions by id so per-answer lookups are
        # O(1). list_questions already hydrates choices + hotspots.
        questions_by_id = {q.id: q for q in list_questions(db, exam_id)}
    finally:
        db.close()

    reviews: list[QuestionReview] = []
    for pos, answer in enumerate(session.answers, start=1):
        question = questions_by_id.get(answer.question_id)
        if question is None:
            # Question was deleted after the session was recorded; skip
            # so the view does not crash. A warning note could surface
            # in the UI later.
            continue
        reviews.append(
            QuestionReview(
                index=pos,
                question=question,
                answer=answer,
                given_payload=_decode_payload_safely(answer.given_json),
            )
        )

    score = compute_session_score(session)
    # Unanswered = snapshotted count minus what was actually persisted.
    # When the snapshot is missing (legacy sessions) we report 0 so the
    # view doesn't show a negative number.
    if session.question_count is not None:
        unanswered = max(session.question_count - len(reviews), 0)
    else:
        unanswered = 0

    return SessionDetail(
        exam_path=exam_path,
        exam_id=exam_id,
        exam_name=exam_name,
        session=session,
        score=score,
        reviews=reviews,
        unanswered_count=unanswered,
    )


__all__ = [
    "QuestionReview",
    "SessionDetail",
    "load_session_detail",
]
