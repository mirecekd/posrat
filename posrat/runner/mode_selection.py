"""Pure helpers for the Runner's Exam Mode dialog.

Extracted from :mod:`posrat.runner.mode_dialog` so the radio-group +
per-option input → :data:`QuestionSelection` conversion can be unit
tested without booting NiceGUI. The heavy side-effect (``ui.notify``)
is injected as a callable so tests can capture messages; production
callers pass ``ui.notify`` directly.

The dialog has two question-selection radios (Take N / Take incorrect)
plus an optional **range modifier** checkbox that only applies to the
Take N mode. When the checkbox is on, the resolver narrows the exam
pool to a 1-based inclusive ``[start, end]`` slice *before* the
random sample, so the candidate can ask for "65 random questions
from questions 300..500". The range modifier is silently ignored
when the active mode is Take incorrect — the user explicitly opted
out of combining incorrect-only filtering with author-order ranges
during planning (2026-05-20).
"""

from __future__ import annotations

from typing import Callable, Optional

from posrat.runner.orchestrator import (
    QuestionSelection,
    SelectAll,
    SelectIncorrect,
)


#: Radio-group option keys. Shared with :mod:`posrat.runner.mode_dialog`
#: so both the widget binding and the resolver agree on the exact
#: string literal. Keeping them together prevents a refactor that
#: renames one side from silently breaking the dialog.
OPT_ALL = "all"
OPT_INCORRECT = "incorrect"


NotifyFn = Callable[[str], None]


def resolve_selection_from_dialog(
    *,
    mode: str,
    count_value,
    wrong_value,
    pool_size: int,
    notify: NotifyFn,
    range_enabled: bool = False,
    range_start_value=None,
    range_end_value=None,
) -> Optional[QuestionSelection]:
    """Convert dialog widget values into a :data:`QuestionSelection`.

    Returns ``None`` after calling ``notify(message)`` whenever the
    user's input is invalid (non-numeric, out of range, reversed
    bounds, …). Callers that get ``None`` must abort the start flow
    without closing the dialog so the user can correct the typo.

    ``pool_size`` is the total number of questions in the exam —
    needed to bound-check the range modifier without re-reading the DB.

    The ``range_*`` arguments encode the optional "Limit to question
    range from X to Y" modifier (only applied when ``mode == OPT_ALL``
    and ``range_enabled is True``). When the active mode is something
    else, the range modifier is silently dropped — the dialog UI also
    disables the checkbox in that case so the user cannot accidentally
    request an unsupported combination.
    """

    if mode == OPT_ALL:
        try:
            count = int(count_value or 0)
        except (TypeError, ValueError):
            notify("Invalid question count.")
            return None
        if count <= 0:
            notify("Question count must be positive.")
            return None

        range_start: Optional[int] = None
        range_end: Optional[int] = None
        if range_enabled:
            try:
                range_start = int(range_start_value or 0)
                range_end = int(range_end_value or 0)
            except (TypeError, ValueError):
                notify("Invalid range.")
                return None
            if range_start < 1 or range_end < 1 or range_end > pool_size:
                notify(f"Range must lie within 1..{pool_size}.")
                return None
            if range_end < range_start:
                notify("Range end must be >= range start.")
                return None

        return SelectAll(
            count=count,
            range_start=range_start,
            range_end=range_end,
        )

    if mode == OPT_INCORRECT:
        try:
            threshold = int(wrong_value or 0)
        except (TypeError, ValueError):
            notify("Invalid wrong-count threshold.")
            return None
        if threshold < 1:
            notify("Wrong-count threshold must be >= 1.")
            return None
        # Range modifier is silently ignored for incorrect-only mode —
        # the dialog disables the checkbox in that case so the user
        # cannot fall into this branch with range_enabled=True via UI.
        return SelectIncorrect(min_wrong_count=threshold)

    notify("Pick one question-selection mode.")
    return None


__all__ = [
    "NotifyFn",
    "OPT_ALL",
    "OPT_INCORRECT",
    "resolve_selection_from_dialog",
]
