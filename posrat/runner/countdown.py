"""Pure countdown helpers — compute remaining seconds / expiry from a stash.

The Runner's question view pins the session's ``started_at`` (ISO-8601
UTC) and snapshotted ``time_limit_minutes`` on the stash at session
start. Every second a ``ui.timer`` tick re-reads the stash and calls
into these helpers to decide how to render the countdown label and
whether the "time is up" modal should open.

Isolating the maths in a pure module gives us three benefits:

1. Testable without NiceGUI — we just pass in a fake ``now`` and
   assert the returned seconds.
2. No silent clock drift: every call re-computes from the pinned
   ISO timestamp, so a paused ``ui.timer`` (e.g. tab backgrounded)
   cannot lose ticks; when the user comes back the countdown jumps
   straight to the correct value.
3. Defensive — malformed ``started_at`` or a ``None`` limit returns
   "no countdown" rather than raising, keeping timer-less exams
   working untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _parse_iso_utc(raw: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp that may end in ``Z`` into a tz-aware dt.

    Returns ``None`` when the string is empty or cannot be parsed.
    Session ``started_at`` values are produced by
    :func:`posrat.storage.session_repo._utc_now_iso` and always look
    like ``2026-04-23T10:00:00Z``, but we still guard so a hand-edited
    DB does not crash the timer.
    """

    if not raw:
        return None
    # ``datetime.fromisoformat`` understands ``+00:00`` but not a
    # trailing ``Z`` until Python 3.11+. Normalising explicitly
    # keeps the helper portable for older interpreters that might
    # host a legacy install.
    normalised = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Assume UTC when the string carries no offset — session rows
        # always do, but be forgiving.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def remaining_seconds(
    *,
    started_at: Optional[str],
    time_limit_minutes: Optional[int],
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Return whole seconds left in the session, or ``None`` for no-timer.

    * ``None`` — the session has no timer (``time_limit_minutes`` is
      ``None`` or non-positive), or ``started_at`` could not be parsed.
      Callers render no countdown in this case.
    * ``0`` — the deadline has already passed; the UI should show
      "00:00" and lock inputs (handled by the caller).
    * A positive integer — whole seconds still on the clock. The
      caller can divmod by 60 for the ``MM:SS`` display.

    ``now`` is injected as a seam so tests can pass a fixed timestamp
    without patching ``datetime.now``. Production callers leave it at
    the default.
    """

    if time_limit_minutes is None or time_limit_minutes <= 0:
        return None
    started = _parse_iso_utc(started_at or "")
    if started is None:
        return None

    current = now if now is not None else datetime.now(timezone.utc)
    # Normalise a naive ``now`` injected by tests to UTC so arithmetic
    # matches the timezone-aware ``started`` without raising.
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    elapsed = (current - started).total_seconds()
    remaining = time_limit_minutes * 60 - elapsed
    if remaining <= 0:
        return 0
    return int(remaining)


def is_expired(
    *,
    started_at: Optional[str],
    time_limit_minutes: Optional[int],
    now: Optional[datetime] = None,
) -> bool:
    """Return ``True`` when the session's timer has run out.

    Thin convenience wrapper over :func:`remaining_seconds`:
    ``None`` (no timer) is never "expired" — a timer-less session
    runs indefinitely. Any positive remainder is still live; only
    a clean ``0`` maps to expired.
    """

    remaining = remaining_seconds(
        started_at=started_at,
        time_limit_minutes=time_limit_minutes,
        now=now,
    )
    if remaining is None:
        return False
    return remaining <= 0


def format_mm_ss(seconds: Optional[int]) -> Optional[str]:
    """Return a ``MM:SS`` string for a second count, or ``None`` for no-timer.

    Negative / ``None`` input returns ``None`` so the caller can
    short-circuit the label rendering. ``0`` formats as ``00:00``
    (the "time is up" state). Minutes are *not* wrapped to 99 —
    a 180-minute exam shows ``180:00`` at the start, the digit
    count grows as needed.
    """

    if seconds is None:
        return None
    if seconds < 0:
        return "00:00"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


__all__ = [
    "format_mm_ss",
    "is_expired",
    "remaining_seconds",
]
