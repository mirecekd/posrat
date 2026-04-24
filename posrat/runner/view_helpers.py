"""Shared utility helpers used by multiple Runner view modules.

Extracted so the picker / question / results sub-modules can import
them without pulling in the whole flow graph. Every helper is a pure
function (no NiceGUI side-effects) — :func:`current_runner_username`
is the only soft exception, it reads ``app.storage.user`` via a
try/except fence so unit tests can still invoke it outside a request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from posrat.runner.identity import resolve_username
from posrat.system.auth_session import (
    AUTH_STORAGE_KEY,
    read_username_from_stash,
)


def letter_for(index: int) -> str:
    """Return ``A``/``B``/``C``/… prefix for a choice at display index.

    Keeps the VCE habit of labelling each choice with a fixed letter
    regardless of shuffle: users can say "answer B" out loud and we
    always know which row they mean. Wraps past ``Z`` into ``AA`` /
    ``AB`` / … by chained ``chr(ord('A') + remainder)`` — Pydantic's
    ``Choice`` list has no explicit upper bound so defensive wrapping
    costs nothing. In practice exams never pass 26 choices.
    """

    if index < 0:
        raise ValueError(f"letter index must be non-negative, got {index}")
    if index < 26:
        return chr(ord("A") + index)
    # Two-letter extension (AA, AB, …). Unlikely in practice but cheap.
    outer, inner = divmod(index, 26)
    return chr(ord("A") + outer - 1) + chr(ord("A") + inner)


def utc_now_iso() -> str:
    """Compact ISO-8601 UTC timestamp with trailing ``Z`` (session rows)."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def request_headers() -> Optional[dict[str, str]]:
    """Return the current request's headers, or ``None`` outside a request.

    The NiceGUI context gives us access to the Starlette request via
    ``context.client.request``; outside a request (e.g. early init)
    this returns ``None`` so the identity resolver falls back to the
    environment variable.
    """

    try:
        # Local import keeps the module importable from unit tests that
        # never touch NiceGUI's request context machinery.
        from nicegui import context
        request = context.client.request  # type: ignore[attr-defined]
    except Exception:
        return None
    if request is None:
        return None
    try:
        return dict(request.headers)
    except Exception:
        return None


def choice_row_classes(
    *,
    is_correct: bool,
    picked: bool,
    feedback_pending: bool,
) -> str:
    """Return Quasar/Tailwind classes for a choice row in review mode.

    Colour rules when ``feedback_pending`` is true:

    * Correct choice → always green (regardless of whether the user
      picked it). Highlights "this is where the answer should have been".
    * Wrong choice the user picked → red background. Highlights the
      specific mistake.
    * Everything else → neutral (no extra background).

    When feedback is not pending we return an empty string so the
    row renders with default Quasar styling.
    """

    if not feedback_pending:
        return ""
    if is_correct:
        return "bg-green-2 q-pa-xs"
    if picked:
        return "bg-red-2 q-pa-xs"
    return "q-pa-xs"


def current_runner_username() -> str:
    """Return the best "who is running?" label for the current request.

    Resolution order:

    1. ``app.storage.user[AUTH_STORAGE_KEY]`` — the auth stash from
       Phase 10 (signed-in user). Matches the header greeting.
    2. :func:`posrat.runner.identity.resolve_username` — proxy header
       / ``USER`` env var / ``local_dev`` fallback. Lets Runner keep
       working when the auth layer is temporarily bypassed (e.g. a
       test harness that imports the runner alone).

    Wrapped in a try/except because ``app.storage.user`` raises when
    accessed outside a NiceGUI request — the pytest suite exercises
    several helpers that reach in here without a client, and we
    prefer a silent fall-through over a hard crash.
    """

    try:
        # Local import keeps this module importable without NiceGUI
        # during cold test collection.
        from nicegui import app

        stash = app.storage.user.get(AUTH_STORAGE_KEY)
    except Exception:
        stash = None
    name = read_username_from_stash(stash)
    if name:
        return name
    return resolve_username(request_headers())


__all__ = [
    "choice_row_classes",
    "current_runner_username",
    "letter_for",
    "request_headers",
    "utc_now_iso",
]
