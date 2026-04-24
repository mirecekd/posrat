"""Tests for :mod:`posrat.runner.countdown`.

Pure module, so every test just passes an injected ``now`` datetime
and asserts the returned seconds / expiry bool / formatted string.
No NiceGUI, no SQLite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from posrat.runner.countdown import (
    format_mm_ss,
    is_expired,
    remaining_seconds,
)


START = "2026-04-23T10:00:00Z"
START_DT = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# remaining_seconds                                                            #
# --------------------------------------------------------------------------- #


def test_remaining_seconds_none_when_no_time_limit() -> None:
    """``time_limit_minutes=None`` → ``None`` (no countdown rendered)."""

    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=None, now=START_DT
        )
        is None
    )


def test_remaining_seconds_none_for_zero_or_negative_limit() -> None:
    """Non-positive limits are treated as "no timer" (defensive)."""

    for bad in (0, -5):
        assert (
            remaining_seconds(
                started_at=START, time_limit_minutes=bad, now=START_DT
            )
            is None
        )


def test_remaining_seconds_none_for_missing_started_at() -> None:
    """Empty / unparseable ``started_at`` → ``None`` rather than an exception."""

    assert (
        remaining_seconds(
            started_at="", time_limit_minutes=60, now=START_DT
        )
        is None
    )
    assert (
        remaining_seconds(
            started_at="not-an-iso-string",
            time_limit_minutes=60,
            now=START_DT,
        )
        is None
    )


def test_remaining_seconds_at_start_returns_full_budget() -> None:
    """``now == started_at`` → full minute budget in seconds."""

    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=10, now=START_DT
        )
        == 10 * 60
    )


def test_remaining_seconds_after_partial_elapse() -> None:
    """Elapsed 2m30s of a 10m budget → 450 remaining."""

    later = START_DT + timedelta(minutes=2, seconds=30)
    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=10, now=later
        )
        == 7 * 60 + 30
    )


def test_remaining_seconds_zero_at_deadline() -> None:
    """``now`` exactly at the deadline → 0 (clamped, no negative)."""

    at_limit = START_DT + timedelta(minutes=10)
    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=10, now=at_limit
        )
        == 0
    )


def test_remaining_seconds_zero_past_deadline() -> None:
    """``now`` past the deadline → clamped to 0, never negative."""

    past = START_DT + timedelta(minutes=30)
    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=10, now=past
        )
        == 0
    )


def test_remaining_seconds_accepts_naive_now() -> None:
    """A naive ``now`` is interpreted as UTC (defensive — tests often pass one)."""

    naive = datetime(2026, 4, 23, 10, 5, 0)  # 5 minutes in
    assert (
        remaining_seconds(
            started_at=START, time_limit_minutes=10, now=naive
        )
        == 5 * 60
    )


# --------------------------------------------------------------------------- #
# is_expired                                                                   #
# --------------------------------------------------------------------------- #


def test_is_expired_false_without_timer() -> None:
    """No timer → never expired, the session runs indefinitely."""

    assert is_expired(
        started_at=START, time_limit_minutes=None, now=START_DT
    ) is False


def test_is_expired_false_while_time_left() -> None:
    """Mid-session with time still on the clock → not expired."""

    mid = START_DT + timedelta(minutes=5)
    assert is_expired(
        started_at=START, time_limit_minutes=10, now=mid
    ) is False


def test_is_expired_true_at_and_past_deadline() -> None:
    """``now`` at or past the deadline → expired."""

    at_limit = START_DT + timedelta(minutes=10)
    past = START_DT + timedelta(minutes=30)
    assert is_expired(
        started_at=START, time_limit_minutes=10, now=at_limit
    ) is True
    assert is_expired(
        started_at=START, time_limit_minutes=10, now=past
    ) is True


# --------------------------------------------------------------------------- #
# format_mm_ss                                                                 #
# --------------------------------------------------------------------------- #


def test_format_mm_ss_none_passthrough() -> None:
    """``None`` input → ``None`` output (caller skips label rendering)."""

    assert format_mm_ss(None) is None


def test_format_mm_ss_zero_is_double_zero() -> None:
    """``0`` formats as ``00:00`` — the "time is up" state."""

    assert format_mm_ss(0) == "00:00"


def test_format_mm_ss_pads_to_two_digits_each() -> None:
    """Regular values are zero-padded to two digits in both slots."""

    assert format_mm_ss(9) == "00:09"
    assert format_mm_ss(59) == "00:59"
    assert format_mm_ss(60) == "01:00"
    assert format_mm_ss(9 * 60 + 7) == "09:07"


def test_format_mm_ss_long_exams_keep_all_minute_digits() -> None:
    """A 3h exam shows ``180:00`` at start — no wrap to 99."""

    assert format_mm_ss(180 * 60) == "180:00"


def test_format_mm_ss_negative_clamps_to_zero() -> None:
    """Negative input still renders as ``00:00`` (defensive)."""

    assert format_mm_ss(-5) == "00:00"
