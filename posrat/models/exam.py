"""Exam model — the root object of the data model.

An Exam owns a list of Questions. This is the object that gets
serialized to ``exam.json`` inside a ``.posrat`` bundle and mirrored
to SQLite in Phase 2.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from posrat.models.question import Question


class Exam(BaseModel):
    """A complete exam — metadata plus the ordered list of questions.

    Attributes:
        id: Stable identifier for the exam. Used as a directory key
            under ``assets/<exam_id>/`` for images.
        name: Human-readable title (e.g. "AWS SAA-C03 practice set").
        description: Optional longer description shown in the picker.
        questions: Ordered list of questions. Order in the list is the
            canonical question order; SQLite mirrors it via
            ``order_index``.
        default_question_count: Optional default number of questions
            sampled per session (Runner picker prefill). ``None`` means
            the Runner will default to all available questions.
        time_limit_minutes: Optional default session time budget in
            minutes. ``None`` means no time limit.
        passing_score: Optional raw passing score threshold. Interpreted
            together with :attr:`target_score` — a session passes when
            ``raw_score >= passing_score``.
        target_score: Optional maximum raw score (e.g. ``1000``). Used
            to convert a percentage into a raw score. Defaults to
            ``1000`` when ``passing_score`` is set but ``target_score``
            is not.
    """

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    questions: List[Question] = Field(default_factory=list)
    default_question_count: Optional[int] = Field(default=None, ge=1)
    time_limit_minutes: Optional[int] = Field(default=None, ge=1)
    passing_score: Optional[int] = Field(default=None, ge=0)
    target_score: Optional[int] = Field(default=None, ge=1)


    @model_validator(mode="after")
    def _validate_question_ids_unique(self) -> "Exam":
        """Question ids must be unique within one exam.

        We catch this at the model layer so that JSON import fails fast
        and SQLite never has to deal with a collision at insert time.
        """

        seen: set[str] = set()
        for q in self.questions:
            if q.id in seen:
                raise ValueError(
                    f"duplicate question id within exam: {q.id!r}"
                )
            seen.add(q.id)
        return self

    @model_validator(mode="after")
    def _validate_scoring_thresholds(self) -> "Exam":
        """Passing score must not exceed target score.

        ``target_score`` is the "100 %" mark (raw points, e.g. ``1000``
        as in Visual CertExam). ``passing_score`` is the minimum raw
        points required to pass (e.g. ``700``). A passing_score greater
        than target_score would make passing impossible — reject it at
        the model level so Designer / JSON import can catch the mistake
        fast.
        """

        if (
            self.passing_score is not None
            and self.target_score is not None
            and self.passing_score > self.target_score
        ):
            raise ValueError(
                "passing_score must not exceed target_score "
                f"(got {self.passing_score} > {self.target_score})"
            )
        return self

