"""Tests for :mod:`posrat.runner.mode_selection`.

The resolver is the pure half of the Exam Mode dialog: it turns raw
widget values (``None`` / strings / ints) into a
:data:`QuestionSelection` or notifies the user and returns ``None``.
Having it separated from the NiceGUI layer means every branch is
testable without booting a browser.

Phase 2026-05-20: dropped the standalone ``OPT_RANGE`` mode and
replaced it with a range *modifier* on top of the Take-N mode (still
random sample, just from a 1-based inclusive slice). The Take-incorrect
mode silently ignores the range modifier — the dialog UI also greys
out the checkbox in that mode so this branch is defence in depth.
"""

from __future__ import annotations

from posrat.runner.mode_selection import (
    OPT_ALL,
    OPT_INCORRECT,
    resolve_selection_from_dialog,
)
from posrat.runner.orchestrator import (
    SelectAll,
    SelectIncorrect,
)


def _call(mode: str, **overrides):
    """Shorthand: call the resolver with record-to-list notify."""

    messages: list[str] = []
    defaults = {
        "mode": mode,
        "count_value": 10,
        "wrong_value": 1,
        "pool_size": 20,
        "range_enabled": False,
        "range_start_value": 1,
        "range_end_value": 20,
    }
    defaults.update(overrides)
    result = resolve_selection_from_dialog(
        **defaults,
        notify=messages.append,
    )
    return result, messages


def test_resolve_all_builds_select_all() -> None:
    result, messages = _call(OPT_ALL, count_value=5)
    assert result == SelectAll(count=5, range_start=None, range_end=None)
    assert messages == []


def test_resolve_all_rejects_zero_count() -> None:
    result, messages = _call(OPT_ALL, count_value=0)
    assert result is None
    assert messages == ["Question count must be positive."]


def test_resolve_all_rejects_non_numeric() -> None:
    result, messages = _call(OPT_ALL, count_value="abc")
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


# --------------------------------------------------------------------------- #
# Range modifier (2026-05-20) — applies only to OPT_ALL                       #
# --------------------------------------------------------------------------- #


def test_range_disabled_yields_no_modifier_on_select_all() -> None:
    """range_enabled=False means range_start / range_end stay None."""

    result, messages = _call(
        OPT_ALL,
        count_value=10,
        range_enabled=False,
        range_start_value=3,  # ignored when disabled
        range_end_value=7,
    )
    assert result == SelectAll(count=10, range_start=None, range_end=None)
    assert messages == []


def test_range_enabled_attaches_modifier_to_select_all() -> None:
    """range_enabled=True with valid bounds → SelectAll carries the slice."""

    result, messages = _call(
        OPT_ALL,
        count_value=5,
        range_enabled=True,
        range_start_value=3,
        range_end_value=12,
    )
    assert result == SelectAll(count=5, range_start=3, range_end=12)
    assert messages == []


def test_range_enabled_rejects_zero_start() -> None:
    result, messages = _call(
        OPT_ALL,
        count_value=5,
        range_enabled=True,
        range_start_value=0,
        range_end_value=5,
    )
    assert result is None
    assert messages == ["Range must lie within 1..20."]


def test_range_enabled_rejects_end_beyond_pool() -> None:
    result, messages = _call(
        OPT_ALL,
        count_value=5,
        range_enabled=True,
        range_start_value=1,
        range_end_value=50,
        pool_size=20,
    )
    assert result is None
    assert messages == ["Range must lie within 1..20."]


def test_range_enabled_rejects_reversed_bounds() -> None:
    result, messages = _call(
        OPT_ALL,
        count_value=5,
        range_enabled=True,
        range_start_value=9,
        range_end_value=3,
    )
    assert result is None
    assert messages == ["Range end must be >= range start."]


def test_range_enabled_rejects_non_numeric() -> None:
    result, messages = _call(
        OPT_ALL,
        count_value=5,
        range_enabled=True,
        range_start_value="abc",
        range_end_value=10,
    )
    assert result is None
    assert messages == ["Invalid range."]


def test_range_modifier_ignored_for_incorrect_mode() -> None:
    """Range modifier is silently dropped when the active mode is incorrect.

    The dialog UI also greys out the checkbox in that case, but the
    pure resolver guards defensively in case a caller forgets to
    coordinate the radio + checkbox state.
    """

    result, messages = _call(
        OPT_INCORRECT,
        wrong_value=2,
        range_enabled=True,
        range_start_value=3,
        range_end_value=7,
    )
    assert result == SelectIncorrect(min_wrong_count=2)
    assert messages == []
