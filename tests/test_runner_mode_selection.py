"""Tests for :mod:`posrat.runner.mode_selection`.

The resolver is the pure half of the Exam Mode dialog: it turns raw
widget values (``None`` / strings / ints) into a
:data:`QuestionSelection` or notifies the user and returns ``None``.
Having it separated from the NiceGUI layer means every branch is
testable without booting a browser.
"""

from __future__ import annotations

from posrat.runner.mode_selection import (
    OPT_ALL,
    OPT_INCORRECT,
    OPT_RANGE,
    resolve_selection_from_dialog,
)
from posrat.runner.orchestrator import (
    SelectAll,
    SelectIncorrect,
    SelectRange,
)


def _call(mode: str, **overrides):
    """Shorthand: call the resolver with record-to-list notify."""

    messages: list[str] = []
    defaults = {
        "mode": mode,
        "count_value": 10,
        "range_start_value": 1,
        "range_end_value": 10,
        "wrong_value": 1,
        "pool_size": 20,
    }
    defaults.update(overrides)
    result = resolve_selection_from_dialog(
        **defaults,
        notify=messages.append,
    )
    return result, messages


def test_resolve_all_builds_select_all() -> None:
    result, messages = _call(OPT_ALL, count_value=5)
    assert result == SelectAll(count=5)
    assert messages == []


def test_resolve_all_rejects_zero_count() -> None:
    result, messages = _call(OPT_ALL, count_value=0)
    assert result is None
    assert messages == ["Question count must be positive."]


def test_resolve_all_rejects_non_numeric() -> None:
    result, messages = _call(OPT_ALL, count_value="abc")
    assert result is None
    assert len(messages) == 1


def test_resolve_range_builds_select_range() -> None:
    result, messages = _call(
        OPT_RANGE, range_start_value=3, range_end_value=7
    )
    assert result == SelectRange(start=3, end=7)
    assert messages == []


def test_resolve_range_rejects_end_below_start() -> None:
    result, messages = _call(
        OPT_RANGE, range_start_value=9, range_end_value=3
    )
    assert result is None
    assert messages == ["Range end must be >= range start."]


def test_resolve_range_rejects_out_of_bounds() -> None:
    result, messages = _call(
        OPT_RANGE,
        range_start_value=1,
        range_end_value=50,
        pool_size=20,
    )
    assert result is None
    assert messages == ["Range must lie within 1..20."]


def test_resolve_range_rejects_zero_start() -> None:
    result, messages = _call(
        OPT_RANGE, range_start_value=0, range_end_value=5
    )
    assert result is None
    assert len(messages) == 1


def test_resolve_incorrect_builds_select_incorrect() -> None:
    result, messages = _call(OPT_INCORRECT, wrong_value=2)
    assert result == SelectIncorrect(min_wrong_count=2)
    assert messages == []


def test_resolve_incorrect_rejects_zero_threshold() -> None:
    result, messages = _call(OPT_INCORRECT, wrong_value=0)
    assert result is None
    assert messages == ["Wrong-count threshold must be >= 1."]


def test_resolve_unknown_mode_returns_none() -> None:
    result, messages = _call("bogus-option")
    assert result is None
    assert messages == ["Pick one question-selection mode."]
