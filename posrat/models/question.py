"""Question model — the base unit of an exam.

Step 1.1: id, type, text, explanation.
Step 1.2: choices list + single_choice validation (exactly one correct).
Step 1.3: multi_choice validation (at least one correct).
Step 1.4: optional image_path (relative to the exam's assets/ dir).
Step 1.6: optional hotspot payload (options pool + ordered steps).
Step 8.5: optional complexity (1..5) and section (free-text tag)
          surfaced in the Designer Properties panel.
Step 8.5b: allow_shuffle bool (default False) — enables per-question
          choice shuffling in the Runner.
"""


from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from posrat.models.choice import Choice
from posrat.models.hotspot import Hotspot

QuestionType = Literal["single_choice", "multi_choice", "hotspot"]

#: Allowed range (inclusive) for :attr:`Question.complexity`. Matches the
#: 1–5 scale used by most exam banks — 1 = trivial, 5 = very hard. Pinned
#: here (not on the field) so tests and UI widgets can reference the same
#: constants without hard-coding magic numbers.
MIN_COMPLEXITY = 1
MAX_COMPLEXITY = 5



class Question(BaseModel):
    """A single exam question.

    Attributes:
        id: Stable identifier (UUID-ish string). Used for JSON ↔ SQLite
            round-trip and for linking answers to questions across sessions.
        type: Question kind — drives which fields are meaningful and how
            the Runner renders and evaluates the answer.
        text: Question body shown to the user (plain text for MVP).
        explanation: Optional rationale shown in Training mode after a
            wrong answer. First-class field, not an afterthought.
        choices: Answer options for ``single_choice`` / ``multi_choice``
            questions. For ``hotspot`` this list stays empty — the
            hotspot-specific structure is attached in step 1.6.
        image_path: Optional path to an illustrative image, relative to
            the exam's ``assets/`` directory (e.g. ``"abc123.png"``).
            Stored as a plain string here; physical file handling lives
            in the bundle / storage layer. ``None`` means "no image".
        hotspot: Hotspot-specific payload (options pool + ordered steps).
            Must be present iff ``type == "hotspot"``; must be ``None``
            otherwise.
        complexity: Optional difficulty rating on the ``MIN_COMPLEXITY``..
            ``MAX_COMPLEXITY`` scale (1..5). ``None`` means "unrated" —
            the Designer Properties panel shows "(none)" and exports
            omit the field. Used later by the Runner to filter questions
            ("drill only hard ones").
        section: Optional free-text section / topic tag (e.g. ``"Compute"``
            or ``"IAM"``) used to group related questions. Stored verbatim;
            whitespace is trimmed and an empty string is coerced to
            ``None`` so "(none)" and ``""`` cannot both represent unset.
        allow_shuffle: When ``True`` the Runner may randomise the order
            of this question's choices at presentation time. Default
            ``False`` so existing exams keep the deterministic A/B/C/D
            order authors crafted. Ignored for ``hotspot`` questions
            (the hotspot step order is authored and not shuffled).
    """

    id: str = Field(..., min_length=1)
    type: QuestionType
    text: str = Field(..., min_length=1)
    explanation: Optional[str] = None
    choices: List[Choice] = Field(default_factory=list)
    image_path: Optional[str] = None
    hotspot: Optional[Hotspot] = None
    complexity: Optional[int] = Field(
        default=None, ge=MIN_COMPLEXITY, le=MAX_COMPLEXITY
    )
    section: Optional[str] = None
    allow_shuffle: bool = False


    @model_validator(mode="after")
    def _normalize_section(self) -> "Question":
        """Trim ``section`` and coerce empty strings to ``None``.

        Keeps the "unset" state single-valued (``None``) so Designer
        rendering, JSON export and DB round-trip do not have to special
        case ``""`` vs ``None`` as two flavours of the same concept.
        """

        if isinstance(self.section, str):
            trimmed = self.section.strip()
            self.section = trimmed or None
        return self

    @model_validator(mode="after")
    def _validate_choices(self) -> "Question":

        """Type-specific consistency checks for ``choices``.

        - ``single_choice``: at least 2 options, exactly 1 correct;
          ``hotspot`` must be ``None``.
        - ``multi_choice``: at least 2 options, at least 1 correct
          (upper bound is "all correct" — deliberately allowed);
          ``hotspot`` must be ``None``.
        - ``hotspot``: ``choices`` must be empty; ``hotspot`` payload
          must be present (its internals are checked on ``Hotspot``).
        """

        if self.type == "single_choice":
            if self.hotspot is not None:
                raise ValueError(
                    "single_choice question must not have a hotspot payload"
                )
            if len(self.choices) < 2:
                raise ValueError(
                    "single_choice question must have at least 2 choices"
                )
            correct_count = sum(1 for c in self.choices if c.is_correct)
            if correct_count != 1:
                raise ValueError(
                    "single_choice question must have exactly 1 correct choice, "
                    f"got {correct_count}"
                )
        elif self.type == "multi_choice":
            if self.hotspot is not None:
                raise ValueError(
                    "multi_choice question must not have a hotspot payload"
                )
            if len(self.choices) < 2:
                raise ValueError(
                    "multi_choice question must have at least 2 choices"
                )
            correct_count = sum(1 for c in self.choices if c.is_correct)
            if correct_count < 1:
                raise ValueError(
                    "multi_choice question must have at least 1 correct choice, "
                    f"got {correct_count}"
                )
        elif self.type == "hotspot":
            if self.hotspot is None:
                raise ValueError(
                    "hotspot question must have a hotspot payload"
                )
            if self.choices:
                raise ValueError(
                    "hotspot question must not have choices "
                    "(answers live in the hotspot payload)"
                )
        return self
