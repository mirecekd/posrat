"""Tests for :mod:`posrat.runner.grading`."""

from __future__ import annotations

import json

import pytest

from posrat.models import Choice, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep
from posrat.runner.grading import (
    decode_given_json,
    encode_answer_payload,
    grade_answer,
)


def _single_choice() -> Question:
    return Question(
        id="q-sc",
        type="single_choice",
        text="Pick one",
        choices=[
            Choice(id="c-a", text="A", is_correct=False),
            Choice(id="c-b", text="B", is_correct=True),
            Choice(id="c-c", text="C", is_correct=False),
        ],
    )


def _multi_choice() -> Question:
    return Question(
        id="q-mc",
        type="multi_choice",
        text="Pick many",
        choices=[
            Choice(id="c-a", text="A", is_correct=True),
            Choice(id="c-b", text="B", is_correct=False),
            Choice(id="c-c", text="C", is_correct=True),
        ],
    )


def _hotspot() -> Question:
    return Question(
        id="q-hs",
        type="hotspot",
        text="Pick per step",
        choices=[],
        hotspot=Hotspot(
            options=[
                HotspotOption(id="o-1", text="X"),
                HotspotOption(id="o-2", text="Y"),
            ],
            steps=[
                HotspotStep(
                    id="s-1", prompt="Step 1", correct_option_id="o-1"
                ),
                HotspotStep(
                    id="s-2", prompt="Step 2", correct_option_id="o-2"
                ),
            ],
        ),
    )


def test_encode_answer_payload_is_canonical() -> None:
    """Same content → identical JSON regardless of dict ordering."""

    a = encode_answer_payload({"choice_id": "c-a"})
    b = encode_answer_payload({"choice_id": "c-a"})
    assert a == b == '{"choice_id":"c-a"}'


def test_encode_answer_payload_sorts_keys() -> None:
    """Keys must be alphabetical to keep diffs stable across runs."""

    encoded = encode_answer_payload({"z": 1, "a": 2})
    assert encoded == '{"a":2,"z":1}'


def test_grade_single_choice_happy_path() -> None:
    """Correct choice id → ``is_correct=True`` and canonical JSON."""

    q = _single_choice()
    is_correct, given_json = grade_answer(q, {"choice_id": "c-b"})
    assert is_correct is True
    assert given_json == '{"choice_id":"c-b"}'


def test_grade_single_choice_wrong_answer() -> None:
    """Wrong choice → ``is_correct=False``, json still written."""

    q = _single_choice()
    is_correct, given_json = grade_answer(q, {"choice_id": "c-a"})
    assert is_correct is False
    assert given_json == '{"choice_id":"c-a"}'


def test_grade_single_choice_unanswered_is_false() -> None:
    """``choice_id=None`` / empty string → ``is_correct=False``."""

    q = _single_choice()
    is_correct, _ = grade_answer(q, {"choice_id": None})
    assert is_correct is False


def test_grade_single_choice_rejects_missing_key() -> None:
    """Payload without the expected key must raise, not silently pass."""

    q = _single_choice()
    with pytest.raises(ValueError):
        grade_answer(q, {"choice_ids": ["c-a"]})


def test_grade_multi_choice_set_equality() -> None:
    """Multi_choice requires exactly the set of correct ids — no more, no less."""

    q = _multi_choice()

    # Full correct set — pass.
    is_correct, given_json = grade_answer(
        q, {"choice_ids": ["c-a", "c-c"]}
    )
    assert is_correct is True
    # Canonical JSON preserves list order as given; set equality is
    # applied inside the grader, not the encoder.
    assert "c-a" in given_json and "c-c" in given_json

    # Partial correct — fail (missing one).
    is_correct, _ = grade_answer(q, {"choice_ids": ["c-a"]})
    assert is_correct is False

    # Superset with one wrong — fail.
    is_correct, _ = grade_answer(
        q, {"choice_ids": ["c-a", "c-b", "c-c"]}
    )
    assert is_correct is False


def test_grade_multi_choice_empty_list_is_false() -> None:
    """Zero selections → ``is_correct=False`` (mismatch with non-empty correct set)."""

    q = _multi_choice()
    is_correct, _ = grade_answer(q, {"choice_ids": []})
    assert is_correct is False


def test_grade_multi_choice_rejects_non_list() -> None:
    """``choice_ids`` of wrong type must raise ValueError."""

    q = _multi_choice()
    with pytest.raises(ValueError):
        grade_answer(q, {"choice_ids": "c-a"})


def test_grade_hotspot_all_steps_correct() -> None:
    """Hotspot passes only when every step's pick matches the correct option."""

    q = _hotspot()
    is_correct, given_json = grade_answer(
        q,
        {"step_option_ids": {"s-1": "o-1", "s-2": "o-2"}},
    )
    assert is_correct is True
    # Inner dict keys sorted too.
    parsed = json.loads(given_json)
    assert parsed == {"step_option_ids": {"s-1": "o-1", "s-2": "o-2"}}


def test_grade_hotspot_one_wrong_step_fails() -> None:
    """Single wrong pick fails the whole question — strict VCE semantics."""

    q = _hotspot()
    is_correct, _ = grade_answer(
        q,
        {"step_option_ids": {"s-1": "o-1", "s-2": "o-1"}},
    )
    assert is_correct is False


def test_grade_hotspot_missing_step_fails() -> None:
    """Omitting a step's pick is treated as wrong."""

    q = _hotspot()
    is_correct, _ = grade_answer(
        q,
        {"step_option_ids": {"s-1": "o-1"}},
    )
    assert is_correct is False


def test_grade_rejects_unknown_question_type() -> None:
    """Grader must refuse a question with an unsupported type."""

    # Pydantic does not let us construct a Question with an unknown
    # type, so we monkey-patch after construction to simulate the bug.
    q = _single_choice()
    object.__setattr__(q, "type", "bogus")
    with pytest.raises(ValueError):
        grade_answer(q, {"choice_id": "c-b"})


def test_decode_given_json_roundtrips_encode() -> None:
    """``decode_given_json`` is the inverse of ``encode_answer_payload``."""

    payload = {"choice_ids": ["c-a", "c-c"]}
    encoded = encode_answer_payload(payload)
    assert decode_given_json(encoded) == payload


def test_decode_given_json_raises_on_garbage() -> None:
    """Malformed JSON must surface as ValueError with a friendly message."""

    with pytest.raises(ValueError):
        decode_given_json("{not-json")
