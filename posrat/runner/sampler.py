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
