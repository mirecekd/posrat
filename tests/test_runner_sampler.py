"""Tests for :mod:`posrat.runner.sampler`.

Scope:

* :func:`sample_question_ids` — determinism under a seeded RNG, clamps
  ``n`` above ``len(questions)``, supports ``n=None`` for "take all",
  rejects 0 / negative / empty pool with positive ``n``.
* :func:`shuffle_choices` — identity copy when ``allow_shuffle=False``,
  deterministic permutation under a seeded RNG when True.
"""

from __future__ import annotations

import random

import pytest

from posrat.models import Choice, Question
from posrat.runner.sampler import (
    sample_question_ids,
    select_questions_by_range,
    shuffle_choices,
)



def _make_questions(count: int) -> list[Question]:
    return [
        Question(
            id=f"q-{idx}",
            type="single_choice",
            text=f"Question {idx}",
            choices=[
                Choice(id=f"q-{idx}-a", text="A", is_correct=True),
                Choice(id=f"q-{idx}-b", text="B", is_correct=False),
            ],
        )
        for idx in range(count)
    ]


def test_sample_question_ids_is_deterministic_under_seeded_rng() -> None:
    """Same seed → same sample, independent of call site."""

    questions = _make_questions(10)

    first = sample_question_ids(questions, 4, rng=random.Random(42))
    second = sample_question_ids(questions, 4, rng=random.Random(42))
    assert first == second
    assert len(first) == 4
    # All ids must come from the input pool.
    pool_ids = {q.id for q in questions}
    assert all(qid in pool_ids for qid in first)


def test_sample_question_ids_clamps_n_above_pool_size() -> None:
    """Asking for more than all questions yields the full pool shuffled."""

    questions = _make_questions(5)

    sample = sample_question_ids(questions, 100, rng=random.Random(0))
    assert len(sample) == 5
    assert set(sample) == {q.id for q in questions}


def test_sample_question_ids_none_means_take_all() -> None:
    """``n=None`` shuffles the whole pool."""

    questions = _make_questions(6)
    sample = sample_question_ids(questions, n=None, rng=random.Random(1))
    assert len(sample) == 6
    assert set(sample) == {q.id for q in questions}


def test_sample_question_ids_returns_empty_list_for_empty_pool_with_none() -> None:
    """Empty pool + ``n=None`` → empty list (graceful no-op)."""

    assert sample_question_ids([], n=None) == []
    assert sample_question_ids([], n=0) == []


def test_sample_question_ids_rejects_zero_n() -> None:
    """Zero is rejected because a zero-question session is nonsensical."""

    questions = _make_questions(5)
    with pytest.raises(ValueError):
        sample_question_ids(questions, 0)


def test_sample_question_ids_rejects_negative_n() -> None:
    """Negative values are a programming error."""

    questions = _make_questions(5)
    with pytest.raises(ValueError):
        sample_question_ids(questions, -3)


def test_sample_question_ids_rejects_positive_n_with_empty_pool() -> None:
    """Empty pool + positive ``n`` → :class:`ValueError` (cannot satisfy)."""

    with pytest.raises(ValueError):
        sample_question_ids([], 5)


def test_shuffle_choices_returns_copy_when_allow_shuffle_false() -> None:
    """``allow_shuffle=False`` preserves author-specified order."""

    original = [
        Choice(id="c1", text="A", is_correct=True),
        Choice(id="c2", text="B", is_correct=False),
        Choice(id="c3", text="C", is_correct=False),
    ]
    result = shuffle_choices(original, allow_shuffle=False)
    assert [c.id for c in result] == ["c1", "c2", "c3"]
    # Mutating the returned list must not affect the input.
    result.pop()
    assert len(original) == 3


def test_shuffle_choices_is_deterministic_under_seeded_rng() -> None:
    """With a seeded RNG the permutation is reproducible."""

    original = [
        Choice(id=f"c{idx}", text=str(idx), is_correct=(idx == 0))
        for idx in range(6)
    ]

    first = shuffle_choices(
        original, allow_shuffle=True, rng=random.Random(7)
    )
    second = shuffle_choices(
        original, allow_shuffle=True, rng=random.Random(7)
    )
    assert [c.id for c in first] == [c.id for c in second]
    # Length invariant.
    assert len(first) == 6
    # Content invariant (permutation, not subset).
    assert {c.id for c in first} == {c.id for c in original}


def test_shuffle_choices_short_circuits_for_trivial_inputs() -> None:
    """Single-choice / empty lists return untouched even when allow_shuffle=True."""

    single = [Choice(id="c1", text="A", is_correct=True)]
    assert [c.id for c in shuffle_choices(single, allow_shuffle=True)] == ["c1"]
    assert shuffle_choices([], allow_shuffle=True) == []


# --------------------------------------------------------------------------- #
# select_questions_by_range — 1-based inclusive slice for "take range" mode  #
# --------------------------------------------------------------------------- #


def test_select_questions_by_range_returns_inclusive_slice() -> None:
    """Range 3..5 of 10 questions → q-2, q-3, q-4 (1-based inclusive)."""

    questions = _make_questions(10)
    ids = select_questions_by_range(questions, start=3, end=5)
    assert ids == ["q-2", "q-3", "q-4"]


def test_select_questions_by_range_single_question() -> None:
    """start == end is valid and returns exactly one question."""

    questions = _make_questions(10)
    assert select_questions_by_range(questions, start=7, end=7) == ["q-6"]


def test_select_questions_by_range_full_span() -> None:
    """1..len(pool) selects everything in order."""

    questions = _make_questions(5)
    ids = select_questions_by_range(questions, start=1, end=5)
    assert ids == [q.id for q in questions]


def test_select_questions_by_range_rejects_start_below_one() -> None:
    """0-based indices are a programming error (dialog is 1-based)."""

    questions = _make_questions(5)
    with pytest.raises(ValueError):
        select_questions_by_range(questions, start=0, end=3)


def test_select_questions_by_range_rejects_reversed_range() -> None:
    """end < start is a user typo we surface, not silently normalise."""

    questions = _make_questions(5)
    with pytest.raises(ValueError):
        select_questions_by_range(questions, start=4, end=2)


def test_select_questions_by_range_rejects_end_beyond_pool() -> None:
    """Asking for end=11 on a 5-question pool fails loudly."""

    questions = _make_questions(5)
    with pytest.raises(ValueError):
        select_questions_by_range(questions, start=1, end=11)


def test_select_questions_by_range_rejects_empty_pool() -> None:
    """Empty pool can never satisfy a range — surfaces ValueError."""

    with pytest.raises(ValueError):
        select_questions_by_range([], start=1, end=1)

