"""Session and Answer models — runtime records of exam attempts.

A :class:`Session` represents a single attempt (training or exam) at a
given exam. It owns an ordered collection of :class:`Answer` records
produced by the Runner as the user progresses.

The storage layer mirrors these models 1:1 to the ``sessions`` and
``answers`` SQLite tables (migrations v6 and v7). JSON export of exam
results in Phase 8 will reuse these same Pydantic models.
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SessionMode = Literal["training", "exam"]


class Answer(BaseModel):
    """A single answer recorded during a session.

    Attributes:
        id: Stable identifier of this answer record. Unique within the
            whole database (PK in ``answers``).
        session_id: Identifier of the owning :class:`Session`.
        question_id: Identifier of the answered question.
        given_json: Opaque JSON-encoded payload describing the user's
            answer. The storage layer only guarantees that this is a
            syntactically valid JSON document; the concrete per-type
            shape (single / multi / hotspot) is fixed by the Runner in
            Phase 7.
        is_correct: Whether the given answer matched the expected one.
        time_ms: Optional time spent on this question, in milliseconds.
    """

    id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    question_id: str = Field(..., min_length=1)
    given_json: str = Field(..., min_length=1)
    is_correct: bool
    time_ms: Optional[int] = Field(default=None, ge=0)

    @field_validator("given_json")
    @classmethod
    def _given_json_must_be_parsable(cls, value: str) -> str:
        """Reject payloads that are not syntactically valid JSON.

        We keep the value as an opaque string in storage but refuse to
        persist garbage. The concrete schema per question type is fixed
        later (Phase 7).
        """

        try:
            json.loads(value)
        except json.JSONDecodeError as exc:  # pragma: no cover - guarded
            raise ValueError(f"given_json is not valid JSON: {exc}") from exc
        return value


class Session(BaseModel):
    """A single exam attempt — training or exam mode.

    Attributes:
        id: Stable identifier of this session (PK in ``sessions``).
        exam_id: Identifier of the owning :class:`~posrat.models.Exam`.
        mode: Either ``'training'`` (immediate feedback) or ``'exam'``
            (feedback deferred to the final summary).
        started_at: ISO-8601 timestamp when the session was started.
        finished_at: ISO-8601 timestamp when the session was finished,
            or ``None`` for sessions still in progress (can be resumed).
        answers: Ordered list of :class:`Answer` records captured during
            this session. Empty for freshly started sessions.
        candidate_name: Free-text name of the test-taker (VCE-style
            "Candidate name" input on the exam mode dialog). Required
            because multi-user environments need something to attribute
            results to; for a local dev flow the Runner falls back to
            the ``USER`` environment variable.
        question_count: Snapshot of how many questions were sampled
            when the session started. Can be smaller than the owning
            exam's total (e.g. "Take 65 questions from entire exam").
            ``None`` for sessions imported from legacy exports that did
            not capture this — the Runner treats a missing snapshot as
            "all questions were taken".
        time_limit_minutes: Snapshot of the session's time budget (in
            minutes). ``None`` means no timer was active. Pinned at
            session start so later tweaks to
            :attr:`posrat.models.exam.Exam.time_limit_minutes` do not
            retroactively shorten running sessions.
        passing_score: Snapshot of the raw-points threshold required to
            pass. ``None`` when the exam had no pass/fail criterion.
        target_score: Snapshot of the raw-points "100 %" mark paired
            with ``passing_score``. ``None`` when the exam had no
            pass/fail criterion.
    """

    id: str = Field(..., min_length=1)
    exam_id: str = Field(..., min_length=1)
    mode: SessionMode
    started_at: str = Field(..., min_length=1)
    finished_at: Optional[str] = None
    answers: List[Answer] = Field(default_factory=list)
    candidate_name: Optional[str] = Field(default=None, min_length=1)
    question_count: Optional[int] = Field(default=None, ge=1)
    time_limit_minutes: Optional[int] = Field(default=None, ge=1)
    passing_score: Optional[int] = Field(default=None, ge=0)
    target_score: Optional[int] = Field(default=None, ge=1)

