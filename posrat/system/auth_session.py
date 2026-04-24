"""Cookie-scoped storage helpers for the authenticated username.

The NiceGUI ``app.storage.user`` dict is a signed cookie that travels
with every request. Phase 10 uses it to remember *who* is signed in so
the auth guard (step 10.7) can short-circuit the login form on
subsequent visits.

Shape stashed under :data:`AUTH_STORAGE_KEY`:

* ``username`` — string identifying the signed-in user. Must match
  ``users.username`` in the system database.
* ``login_at`` — ISO-8601 UTC timestamp of the successful
  authentication. Surfaced by the admin audit view in step 10.13.
* ``auth_source`` — mirror of ``User.auth_source`` so the UI header
  can render a small "via nginx" badge for proxy-auth sessions
  without an extra DB round trip.

Kept in a dedicated module (rather than inlined into the login view)
so the pure stash shape can be exercised by pytest without spinning up
NiceGUI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from posrat.models import AuthSource, User


#: Key under which :func:`build_auth_stash` stores the signed-in user
#: inside ``app.storage.user``. Deliberately distinct from the Runner
#: / Designer / detail-view keys so the auth layer cannot clobber any
#: in-progress session or editor state when the user logs out.
AUTH_STORAGE_KEY = "auth_user"


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 (``...Z``) form."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_auth_stash(
    user: User,
    *,
    login_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build the JSON-serialisable auth stash for ``user``.

    ``login_at`` defaults to the current UTC timestamp. Tests pass an
    explicit value for determinism.
    """

    return {
        "username": user.username,
        "login_at": login_at or _utc_now_iso(),
        "auth_source": user.auth_source,
    }


def read_username_from_stash(stash: Any) -> Optional[str]:
    """Extract ``username`` from a stash payload.

    Returns ``None`` for:

    - anything that isn't a plain ``dict`` (the signed cookie was
      tampered with or this is a first visit),
    - dicts without the ``username`` key,
    - dicts where ``username`` is not a non-empty string.

    Callers pass the value straight into
    :func:`posrat.system.auth_service.resolve_effective_user`, which
    tolerates ``None`` and falls through to the proxy-header branch.
    """

    if not isinstance(stash, dict):
        return None
    value = stash.get("username")
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def read_auth_source_from_stash(stash: Any) -> Optional[AuthSource]:
    """Extract ``auth_source`` from a stash payload.

    Returns ``None`` when the value is missing or not one of the
    expected literals. Used by the header rendering helper to show
    the "via nginx" badge only for proxy sessions.
    """

    if not isinstance(stash, dict):
        return None
    value = stash.get("auth_source")
    if value in ("internal", "proxy"):
        return value  # type: ignore[return-value]
    return None


__all__ = [
    "AUTH_STORAGE_KEY",
    "build_auth_stash",
    "read_auth_source_from_stash",
    "read_username_from_stash",
]
