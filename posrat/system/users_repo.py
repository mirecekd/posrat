"""DAO for :class:`posrat.models.User` rows in ``system.sqlite``.

All mutations go through the ``with db:`` context manager so a failure
half-way through rolls back cleanly — identical pattern to
:mod:`posrat.storage.session_repo`.

The repo deliberately does *not* hash passwords itself. Callers pass a
pre-hashed ``password_hash`` (or ``None`` for proxy accounts). The
hashing helper lives in :mod:`posrat.system.auth` (step 10.3) so the
repo stays free of the passlib dependency and tests can swap hashes
with plain sentinels like ``"pbkdf2-stub"``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from posrat.models import AuthSource, User


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 (``...Z``) form."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


#: SELECT clause used in every user-row load. Extracted as a module
#: constant so :func:`get_user` / :func:`list_users` stay in lock-step —
#: forgetting to update one of them would silently drop columns.
_USER_SELECT_COLUMNS = (
    "username, password_hash, display_name, auth_source,"
    " is_admin, can_use_designer, created_at, last_login_at"
)


def _row_to_user(row: sqlite3.Row) -> User:
    """Hydrate a ``users`` row into a :class:`User` model instance."""

    return User(
        username=row["username"],
        password_hash=row["password_hash"],
        display_name=row["display_name"],
        auth_source=row["auth_source"],
        is_admin=bool(row["is_admin"]),
        can_use_designer=bool(row["can_use_designer"]),
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def create_user(
    db: sqlite3.Connection,
    *,
    username: str,
    auth_source: AuthSource,
    password_hash: Optional[str] = None,
    display_name: Optional[str] = None,
    is_admin: bool = False,
    can_use_designer: bool = False,
    created_at: Optional[str] = None,
) -> User:
    """Insert a new user row and return the hydrated :class:`User`.

    ``created_at`` is accepted for deterministic tests; production code
    should omit it and let the DAO stamp ``datetime.utcnow()``.

    Raises:
        ValueError: when required cross-field invariants fail (e.g. an
            ``internal`` account without a ``password_hash``, or a
            ``proxy`` account *with* one). The checks live here rather
            than on the Pydantic model so the model stays useful when
            hydrating rows that were historically unchecked.
        sqlite3.IntegrityError: when ``username`` is already taken (the
            PK constraint rejects the INSERT).
    """

    if auth_source == "internal" and not password_hash:
        raise ValueError(
            "internal accounts require a non-empty password_hash"
        )
    if auth_source == "proxy" and password_hash is not None:
        raise ValueError(
            "proxy accounts must not carry a password_hash"
            " (they authenticate via the trusted header)"
        )

    user = User(
        username=username,
        password_hash=password_hash,
        display_name=display_name,
        auth_source=auth_source,
        is_admin=is_admin,
        can_use_designer=can_use_designer,
        created_at=created_at or _utc_now_iso(),
        last_login_at=None,
    )

    with db:
        db.execute(
            "INSERT INTO users (username, password_hash, display_name,"
            " auth_source, is_admin, can_use_designer, created_at,"
            " last_login_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                user.username,
                user.password_hash,
                user.display_name,
                user.auth_source,
                1 if user.is_admin else 0,
                1 if user.can_use_designer else 0,
                user.created_at,
            ),
        )
    return user


def get_user(db: sqlite3.Connection, username: str) -> Optional[User]:
    """Return the user with ``username`` or ``None`` when missing."""

    row = db.execute(
        f"SELECT {_USER_SELECT_COLUMNS} FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_user(row)


def list_users(db: sqlite3.Connection) -> list[User]:
    """Return every user row ordered by ``username`` ascending.

    Admin UI uses this for the user management table. For large
    deployments a paginated variant can be added later without touching
    callers — the default ordering is a stable alphabetical sort.
    """

    rows = db.execute(
        f"SELECT {_USER_SELECT_COLUMNS} FROM users ORDER BY username ASC"
    ).fetchall()
    return [_row_to_user(row) for row in rows]


def update_user_roles(
    db: sqlite3.Connection,
    username: str,
    *,
    is_admin: bool,
    can_use_designer: bool,
) -> bool:
    """Flip the role flags on an existing user.

    Returns ``True`` when a row was updated, ``False`` when the user
    does not exist (idempotent no-op — same pattern as
    :func:`posrat.storage.question_repo.delete_question`).
    """

    row = db.execute(
        "SELECT username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        return False

    with db:
        db.execute(
            "UPDATE users SET is_admin = ?, can_use_designer = ?"
            " WHERE username = ?",
            (
                1 if is_admin else 0,
                1 if can_use_designer else 0,
                username,
            ),
        )
    return True


def update_user_password(
    db: sqlite3.Connection,
    username: str,
    new_password_hash: str,
) -> bool:
    """Replace the password hash on an *internal* account.

    Passing a proxy account's username raises :class:`ValueError` —
    proxy accounts must not carry passwords (enforced by
    :func:`create_user` at insertion time; this is the symmetric guard
    for updates).

    Returns ``True`` when a row was updated, ``False`` when the user
    does not exist.
    """

    if not new_password_hash:
        raise ValueError("new_password_hash must not be empty")

    row = db.execute(
        "SELECT auth_source FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        return False
    if row["auth_source"] != "internal":
        raise ValueError(
            f"cannot set password on non-internal account: {username!r}"
        )

    with db:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (new_password_hash, username),
        )
    return True


def touch_last_login(
    db: sqlite3.Connection,
    username: str,
    timestamp: Optional[str] = None,
) -> bool:
    """Stamp ``last_login_at`` with ``timestamp`` (or current UTC).

    Called by the auth service after each successful authentication.
    Returns ``True`` when a row was updated, ``False`` otherwise.
    """

    row = db.execute(
        "SELECT username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        return False

    with db:
        db.execute(
            "UPDATE users SET last_login_at = ? WHERE username = ?",
            (timestamp or _utc_now_iso(), username),
        )
    return True


def delete_user(db: sqlite3.Connection, username: str) -> bool:
    """Drop the user row.

    Returns ``True`` when a row was removed, ``False`` when the user
    did not exist (idempotent). The admin UI layer (10.11) is
    responsible for preventing deletion of the last admin / of the
    currently signed-in account — the DAO itself is intentionally
    dumb so other callers (tests, future CLI commands) can wipe state
    without jumping through policy hoops.
    """

    cursor = db.execute(
        "DELETE FROM users WHERE username = ?", (username,)
    )
    db.commit()
    return cursor.rowcount > 0


def count_admins(db: sqlite3.Connection) -> int:
    """Return how many users currently hold ``is_admin = 1``.

    Used by the admin UI (10.11) to refuse deletion / role revocation
    that would leave the system without a single admin. Kept in the
    DAO (rather than derived client-side from :func:`list_users`) so
    the SQL engine handles it for us — cheaper on large deployments
    even if the MVP only has a handful of users.
    """

    row = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE is_admin = 1"
    ).fetchone()
    return int(row["c"]) if row is not None else 0
