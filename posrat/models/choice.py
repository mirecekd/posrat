"""Choice model — one option inside a single_choice or multi_choice question.

Step 1.2: minimal shape (id, text, is_correct). Ordering is managed
by the parent Question via list order (mirrored into SQLite as
`order_index` in step 2.3).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Choice(BaseModel):
    """A single answer option in a single/multi choice question.

    Attributes:
        id: Stable identifier for round-trip and for storing user answers
            as option ids (not by index — indexes can shift).
        text: What the user sees.
        is_correct: Whether picking this option counts toward a correct
            answer. For ``single_choice`` exactly one choice must be
            ``True``; for ``multi_choice`` one or more. Validation of
            these rules lives on the Question model.
    """

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    is_correct: bool = False
