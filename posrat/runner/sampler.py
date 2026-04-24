"""Pure helpers for Runner question selection and choice shuffling.

The Runner's mode dialog lets the user pick ``N`` of all available
questions (VCE-style "Take N questions from entire exam file"). We do
the sampling in a pure helper that accepts an explicit :class:`random.Random`
so tests can pin the seed and assert deterministic output.

Similarly, per-question ``allow_shuffle`` controls whether choice order
gets randomised at render time. The shuffle helper takes the same
:class:`random.Random` instance so a single seeded RNG drives both the
question sample and every choice shuffle for a given session — useful
when exporting a result bundle that needs to be reproducible.
"""

from __future__ import annotations

import random
from typing import Iterable, Optional, TypeVar

from posrat.models import Choice, Question


_T = TypeVar("_T")


def sample_question_ids(
    questions: Iterable[Question],
    n: Optional[int] = None,
    *,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Pick ``n`` question ids from ``questions`` in a randomly shuffled order.

    * ``n = None`` (the default) means "take every question" — useful
      when the caller just wants the full list shuffled.
    * ``n > len(questions)`` gets clamped to ``len(questions)`` so the
      Runner can pass the user-supplied "Take N" value without first
      worrying about exam size.
    * ``n == 0`` raises :class:`ValueError` because a zero-question
      session is nonsensical and would stall the Runner mid-start.
    * ``n < 0`` raises :class:`ValueError`.

    The ``rng`` parameter is a dependency-injection seam for tests —
    pass ``random.Random(seed)`` to get a deterministic sample. Production
    callers omit it and pick up a fresh ``random.Random()`` seeded from
    the OS entropy source.

    Returns **question ids**, not :class:`Question` objects, because
    the session layer only persists ids (the actual Question bodies are
    re-read from the exam DB on demand for each rendered question).
    """

    pool = list(questions)
    if not pool:
        if n is None or n == 0:
            return []
        raise ValueError(
            "cannot sample from an empty question list (n > 0)"
        )

    if n is None:
        effective_n = len(pool)
    elif n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    elif n == 0:
        raise ValueError(
            "cannot start a session with zero questions"
        )
    else:
        effective_n = min(n, len(pool))

    rng = rng or random.Random()
    # ``random.sample`` returns a list of the right size in uniform
    # random order; we extract ids to satisfy the persistence
    # contract. ``sample`` is cheaper than ``shuffle`` + slicing
    # because it returns a fresh list without copying ``pool``.
    sampled = rng.sample(pool, effective_n)
    return [q.id for q in sampled]


def select_questions_by_range(
    questions: Iterable[Question],
    start: int,
    end: int,
) -> list[str]:
    """Return question ids for the inclusive 1-based range ``start..end``.

    The Runner's "Take question range from X to Y" mode (screenshot
    from the planning session mirrors Visual CertExam) lets the
    candidate drill into a specific slice of the question bank — e.g.
    "questions 300-310" when reviewing a recent weak spot.

    ``questions`` must already be in the author-specified order (the
    :func:`posrat.storage.question_repo.list_questions` DAO sorts by
    ``order_index`` ascending, which is what the Runner passes in).
    Indices are 1-based and **inclusive** so the dialog's spinners
    match the numbering the candidate sees in the question view
    ("Question 300 of 334"). ``start = end`` is allowed — that picks
    a single question.

    Raises:

    * :class:`ValueError` — ``start < 1``, ``end < start``, or
      ``end > len(questions)``. We never silently clamp because the
      candidate explicitly typed both numbers and a clamp would hide
      off-by-one mistakes on short exams.
    * :class:`ValueError` — the pool is empty (no questions at all).

    Unlike :func:`sample_question_ids` this helper keeps the
    author-specified order: the candidate asked for questions
    300-310, not a shuffle of them. Choice-level shuffling is
    independent and still honoured at render time via
    :func:`shuffle_choices`.
    """

    pool = list(questions)
    if not pool:
        raise ValueError(
            "cannot select a range from an empty question list"
        )
    if start < 1:
        raise ValueError(f"start must be >= 1, got {start}")
    if end < start:
        raise ValueError(
            f"end ({end}) must be >= start ({start})"
        )
    if end > len(pool):
        raise ValueError(
            f"end ({end}) exceeds pool size ({len(pool)})"
        )

    # 1-based inclusive → Python 0-based exclusive slice.
    return [q.id for q in pool[start - 1 : end]]


def shuffle_choices(
    choices: Iterable[Choice],
    *,
    allow_shuffle: bool,
    rng: Optional[random.Random] = None,
) -> list[Choice]:
    """Return ``choices`` either as-authored or in a random permutation.

    ``allow_shuffle=False`` returns a shallow copy of the input so
    callers can mutate the returned list without touching the
    :class:`Question`'s stored ordering. ``allow_shuffle=True`` applies
    a fresh permutation via ``rng.sample`` (or a default RNG when
    ``rng`` is ``None``). The helper never modifies the caller-supplied
    list in place.

    Typically invoked once per rendered question with the session-wide
    RNG so the same seed reproduces the entire user-facing experience.
    """

    pool = list(choices)
    if not allow_shuffle or len(pool) <= 1:
        return pool

    rng = rng or random.Random()
    return rng.sample(pool, len(pool))
