"""High-level authentication orchestration for POSRAT.

Combines the :mod:`posrat.system.auth` hashing primitives and the
:mod:`posrat.system.users_repo` CRUD DAO into three call-site helpers:

- :func:`authenticate_internal` — verify a ``username`` + plain
  ``password`` pair against the stored bcrypt hash. Bumps
  ``last_login_at`` on success.
- :func:`provision_proxy_user` — idempotent "first sight" of a user
  forwarded by a trusted reverse-proxy header. Creates the row on
  first call, stamps the login timestamp on every call.
- :func:`resolve_effective_user` — bridge between the request layer
  (nginx header, session-stored login, env fallback) and a
  :class:`~posrat.models.User` instance. This is the single
  entry-point the NiceGUI ``@ui.page`` handlers will call in step 10.6
  — everything else branches off it.

The service has *no* direct knowledge of NiceGUI, request headers or
environment variables. Callers pass in plain mappings (``dict``-like)
so the logic can be exercised with pytest without a live NiceGUI
request context.

Design notes:

- All three helpers return :class:`User` instances hydrated through
  :mod:`posrat.system.users_repo`. Callers never see raw SQL rows.
- Failed authentication returns ``None`` (not raises). The UI layer
  differentiates between "user does not exist" and "wrong password"
  in the audit log but from the user-facing side both are surfaced
  as a generic "Invalid credentials" message.
- Successful authentication stamps ``last_login_at`` *after* any
  password check so a failed login cannot leak a timestamp side-effect
  that would help an attacker enumerate accounts.
"""

from __future__ import annotations

import sqlite3
from typing import Mapping, Optional

from posrat.models import User
from posrat.runner.identity import (
    DEFAULT_USERNAME_HEADER,
    resolve_username,
)
from posrat.system.auth import verify_password
from posrat.system.users_repo import (
    create_user,
    get_user,
    touch_last_login,
)


def authenticate_internal(
    db: sqlite3.Connection,
    username: str,
    password: str,
    *,
    timestamp: Optional[str] = None,
) -> Optional[User]:
    """Verify a ``username`` + ``password`` pair and return the user.

    Returns ``None`` when:

    - the user does not exist,
    - the user exists but is a *proxy* account (no stored hash),
    - the password does not match the stored hash.

    On success, :func:`touch_last_login` is called inside the same
    connection so the caller does not need a second round-trip.

    ``timestamp`` is accepted for deterministic tests. Production
    callers omit it.
    """

    user = get_user(db, username)
    if user is None:
        return None
    if user.password_hash is None:
        # Proxy account — hashing primitive would refuse anyway, but
        # the early-out is kinder to the caller than a False verify.
        return None
    if not verify_password(password, user.password_hash):
        return None

    touch_last_login(db, username, timestamp)
    # Refresh so the returned model reflects the new last_login_at.
    return get_user(db, username)


def provision_proxy_user(
    db: sqlite3.Connection,
    username: str,
    *,
    display_name: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> User:
    """Return the :class:`User` for ``username``, creating it on first sight.

    A trusted reverse proxy guarantees that the header is set only
    after the user has authenticated with the upstream identity
    provider. We therefore trust the name at face value and provision
    a barebones account (``auth_source='proxy'``, no password hash,
    no admin roles). Subsequent requests for the same name re-use the
    existing row.

    ``last_login_at`` is stamped on every call (first-seen and
    follow-ups alike) so the audit log always reflects activity.

    ``display_name`` is only honoured on first creation — established
    rows keep the value the admin may have edited through the admin
    panel (10.11).

    ``timestamp`` is accepted for deterministic tests.
    """

    existing = get_user(db, username)
    if existing is None:
        create_user(
            db,
            username=username,
            auth_source="proxy",
            password_hash=None,
            display_name=display_name,
            is_admin=False,
            can_use_designer=False,
            created_at=timestamp,
        )

    touch_last_login(db, username, timestamp)
    refreshed = get_user(db, username)
    assert refreshed is not None  # just inserted / already existed
    return refreshed


def resolve_effective_user(
    db: sqlite3.Connection,
    *,
    session_username: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
    header_name: str = DEFAULT_USERNAME_HEADER,
    timestamp: Optional[str] = None,
) -> Optional[User]:
    """Return the current :class:`User` or ``None`` when not signed in.

    Resolution order:

    1. **Session-stored login** — ``session_username`` is the value
       the UI stashed in ``app.storage.user`` after a successful
       ``/login`` submission (internal account) or after the first
       proxy-header provisioning. If the user still exists in the DB,
       return it. If the row has been deleted by the admin (10.11),
       fall through — the session cookie is stale.
    2. **Proxy header** — when ``headers[header_name]`` has a non-empty
       value, call :func:`provision_proxy_user` to get (or create) the
       matching row.
    3. **No match** — return ``None``. Callers (``require_auth``,
       10.7) redirect to ``/login``.

    The env-var / ``local_dev`` fallback of
    :func:`posrat.runner.identity.resolve_username` is deliberately
    *not* consulted here — Phase 10 decided to require an explicit
    admin account (no dev bypass). Keeping the two helpers separate
    means the Runner session stash (``candidate_name``) can still use
    the old env/default fallback later without touching the auth
    code path.
    """

    if session_username:
        existing = get_user(db, session_username)
        if existing is not None:
            return existing

    if headers is not None:
        raw = headers.get(header_name)
        if raw is not None:
            trimmed = str(raw).strip()
            if trimmed:
                return provision_proxy_user(
                    db, trimmed, timestamp=timestamp
                )

    return None


__all__ = [
    "authenticate_internal",
    "provision_proxy_user",
    "resolve_effective_user",
    # Re-exported for caller convenience so dispatchers don't have to
    # import from two system modules.
    "resolve_username",
]
