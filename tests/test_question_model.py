"""Unit tests for :class:`posrat.models.Question` optional metadata.

Focused on the ``complexity`` and ``section`` fields added in step 8.5
for the Designer Properties panel. The question type / choice
invariants are already covered by existing tests (``test_question_repo``,
``test_json_roundtrip``) — here we exercise only the new surface so the
feedback loop stays tight.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from posrat.models import Choice, Question
from posrat.models.question import MAX_COMPLEXITY, MIN_COMPLEXITY


def _base_single_choice_kwargs() -> dict:
    """Minimal kwargs that build a valid ``single_choice`` ``Question``.

    Centralised so every test that only cares about the metadata under
    test can focus on the relevant field instead of re-declaring the
    same two-choice skeleton over and over.
    """

    return {
        "id": "q-metadata",
        "type": "single_choice",
        "text": "What is 2 + 2?",
        "choices": [
            Choice(id="q-metadata-a", text="3", is_correct=False),
            Choice(id="q-metadata-b", text="4", is_correct=True),
        ],
    }


def test_complexity_defaults_to_none() -> None:
    """Un-set ``complexity`` stays ``None`` so exports can omit the field."""

    question = Question(**_base_single_choice_kwargs())
    assert question.complexity is None


@pytest.mark.parametrize("value", [MIN_COMPLEXITY, 3, MAX_COMPLEXITY])
def test_complexity_accepts_in_range_values(value: int) -> None:
    """Every integer in the ``MIN..MAX`` range is accepted verbatim."""

    question = Question(**_base_single_choice_kwargs(), complexity=value)
    assert question.complexity == value


@pytest.mark.parametrize(
    "value", [MIN_COMPLEXITY - 1, MAX_COMPLEXITY + 1, 0, 10, -1]
)
def test_complexity_out_of_range_raises(value: int) -> None:
    """Pydantic enforces the ``ge``/``le`` constraint from the model."""

    with pytest.raises(ValidationError):
        Question(**_base_single_choice_kwargs(), complexity=value)


def test_section_defaults_to_none() -> None:
    """Un-set ``section`` stays ``None`` to keep "unset" single-valued."""

    question = Question(**_base_single_choice_kwargs())
    assert question.section is None


def test_section_preserves_free_text() -> None:
    """A non-empty ``section`` survives round-trip through the validator."""

    question = Question(**_base_single_choice_kwargs(), section="Compute")
    assert question.section == "Compute"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_section_empty_or_whitespace_normalises_to_none(value: str) -> None:
    """Empty / whitespace-only strings collapse to ``None``.

    This keeps the UI "(none)" display and the JSON ``null`` export
    synchronised — callers cannot smuggle a ``""`` that bypasses the
    Properties panel's empty-state treatment.
    """

    question = Question(**_base_single_choice_kwargs(), section=value)
    assert question.section is None


def test_section_trims_surrounding_whitespace() -> None:
    """Leading / trailing whitespace is stripped before persistence."""

    question = Question(
        **_base_single_choice_kwargs(), section="  IAM   "
    )
    assert question.section == "IAM"


def test_both_fields_coexist_with_hotspot_type() -> None:
    """``complexity`` and ``section`` apply to hotspot questions too."""

    from posrat.models import Hotspot, HotspotOption, HotspotStep

    question = Question(
        id="q-hot",
        type="hotspot",
        text="Pick the right service for each tier.",
        hotspot=Hotspot(
            options=[HotspotOption(id="o1", text="EC2")],
            steps=[
                HotspotStep(
                    id="s1", prompt="Compute tier?", correct_option_id="o1"
                )
            ],
        ),
        complexity=4,
        section="Compute",
    )
    assert question.complexity == 4
    assert question.section == "Compute"
