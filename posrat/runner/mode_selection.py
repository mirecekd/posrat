"""Pure helpers for the Runner's Exam Mode dialog.

Extracted from :mod:`posrat.runner.mode_dialog` so the radio-group +
per-option input → :data:`QuestionSelection` conversion can be unit
tested without booting NiceGUI. The heavy side-effect (``ui.notify``)
is injected as a callable so tests can capture messages; production
callers pass ``ui.notify`` directly.
"""

from __future__ import annotations

from typing import Callable, Optional

from posrat.runner.orchestrator import (
    QuestionSelection,
    SelectAll,
    SelectIncorrect,
    SelectRange,
)


#: Radio-group option keys. Shared with :mod:`posrat.runner.mode_dialog`
#: so both the widget binding and the resolver agree on the exact
#: string literal. Keeping them together prevents a refactor that
#: renames one side from silently breaking the dialog.
OPT_ALL = "all"
OPT_RANGE = "range"
OPT_INCORRECT = "incorrect"


NotifyFn = Callable[[str], None]


def resolve_selection_from_dialog(
    *,
    mode: str,
    count_value,
    range_start_value,
    range_end_value,
    wrong_value,
    pool_size: int,
    notify: NotifyFn,
) -> Optional[QuestionSelection]:
    """Convert dialog widget values into a :data:`QuestionSelection`.

    Returns ``None`` after calling ``notify(message)`` whenever the
    user's input is invalid (non-numeric, out of range, reversed
    bounds, …). Callers that get ``None`` must abort the start flow
    without closing the dialog so the user can correct the typo.

    ``pool_size`` is the total number of questions in the exam —
    needed to bound-check the "Take range" option without re-reading
    the DB.
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
        return SelectAll(count=count)

    if mode == OPT_RANGE:
        try:
            start = int(range_start_value or 0)
            end = int(range_end_value or 0)
        except (TypeError, ValueError):
            notify("Invalid range.")
            return None
        if start < 1 or end < 1 or end > pool_size:
            notify(f"Range must lie within 1..{pool_size}.")
            return None
        if end < start:
            notify("Range end must be >= range start.")
            return None
        return SelectRange(start=start, end=end)

    if mode == OPT_INCORRECT:
        try:
            threshold = int(wrong_value or 0)
        except (TypeError, ValueError):
            notify("Invalid wrong-count threshold.")
            return None
        if threshold < 1:
            notify("Wrong-count threshold must be >= 1.")
            return None
        return SelectIncorrect(min_wrong_count=threshold)

    notify("Pick one question-selection mode.")
    return None


__all__ = [
    "NotifyFn",
    "OPT_ALL",
    "OPT_INCORRECT",
    "OPT_RANGE",
    "resolve_selection_from_dialog",
]
