"""First-run administrator bootstrap helpers.

POSRAT deliberately ships without a "dev bypass" — the admin UI is
gated behind a real :class:`~posrat.models.User` row in the system
database. This module provides the two call sites that let operators
populate the very first admin:

- :func:`bootstrap_admin_from_env` — consulted from :mod:`posrat.app`
  on startup. Reads :data:`ADMIN_USERNAME_ENV` /
  :data:`ADMIN_PASSWORD_ENV`, and provisions an admin only when the
  ``users`` table is empty. On subsequent starts the helper is a
  cheap no-op.
- :func:`reset_admin_password_cli` — the ``python -m posrat
  create-admin`` subcommand. Used for recovery when the operator
  forgot the password or the admin row was accidentally deleted.

Both helpers return a structured outcome so callers can log / exit
with the right status code.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from posrat.models import User
from posrat.system.auth import hash_password
from posrat.system.system_db import open_system_db, resolve_system_db_path
from posrat.system.users_repo import (
    count_admins,
    create_user,
    get_user,
    list_users,
    update_user_password,
    update_user_roles,
)


#: Environment variable holding the bootstrap admin username. Read once
#: on first-run. Operators typically set this in ``docker-compose.yml``
#: or a systemd unit file so the first container start provisions the
#: account without manual CLI work.
ADMIN_USERNAME_ENV = "POSRAT_ADMIN_USERNAME"

#: Environment variable holding the bootstrap admin password (plain
#: text, read once and hashed immediately). Deliberately separate from
#: :data:`ADMIN_USERNAME_ENV` so secrets managers (systemd
#: ``LoadCredentialEncrypted``, Docker secrets) can mount the password
#: under a tighter scope than the username.
ADMIN_PASSWORD_ENV = "POSRAT_ADMIN_PASSWORD"

#: Optional display-name override for the bootstrap admin. When unset
#: the row's ``display_name`` is ``None`` and the UI falls back to the
#: username (see :attr:`User.effective_display_name`).
ADMIN_DISPLAY_NAME_ENV = "POSRAT_ADMIN_DISPLAY_NAME"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of :func:`bootstrap_admin_from_env`.

    Attributes:
        action: One of:

            * ``"skipped"`` — at least one admin already exists, nothing
              to do.
            * ``"created"`` — a fresh admin row was inserted.
            * ``"env_missing"`` — no admin exists *and* the env vars
              were not set. Production startup should log a WARNING
              so the operator knows the instance is not admin-able
              until they add the vars (or run the CLI).
            * ``"invalid"`` — env vars were set but the password was
              empty / username was blank. Treated as a misconfiguration.

        user: The admin row when ``action == "created"``, otherwise
            ``None``. Surface lets callers log ``user.username`` in
            production startup banners.
        message: Human-readable summary for log output.
    """

    action: str
    user: Optional[User]
    message: str


def _resolve_env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return env if env is not None else os.environ


def bootstrap_admin_from_env(
    data_dir: Path,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> BootstrapResult:
    """Seed the first admin account from env vars when the DB is empty.

    Opens the system database at ``data_dir / system.sqlite`` (creating
    it if necessary), checks :func:`count_admins`, and only if it is
    zero does it consult the env vars. When an admin already exists we
    do *not* overwrite anything — resetting the password is a separate
    operation (see :func:`reset_admin_password_cli`).

    Safe to call on every startup: idempotent after the first success.
    """

    env_map = _resolve_env(env)
    db = open_system_db(resolve_system_db_path(data_dir))
    try:
        if count_admins(db) > 0 or list_users(db):
            return BootstrapResult(
                action="skipped",
                user=None,
                message=(
                    "system database already has users — bootstrap skipped"
                ),
            )

        username = (env_map.get(ADMIN_USERNAME_ENV) or "").strip()
        password = env_map.get(ADMIN_PASSWORD_ENV) or ""
        display_name = (
            env_map.get(ADMIN_DISPLAY_NAME_ENV) or ""
        ).strip() or None

        if not username or not password:
            return BootstrapResult(
                action="env_missing",
                user=None,
                message=(
                    "no admin exists; set "
                    f"{ADMIN_USERNAME_ENV} and {ADMIN_PASSWORD_ENV}"
                    " to seed the first admin (or run "
                    "'python -m posrat create-admin')"
                ),
            )

        try:
            user = create_user(
                db,
                username=username,
                auth_source="internal",
                password_hash=hash_password(password),
                display_name=display_name,
                is_admin=True,
                can_use_designer=True,
            )
        except ValueError as exc:
            return BootstrapResult(
                action="invalid",
                user=None,
                message=f"bootstrap admin rejected: {exc}",
            )

        return BootstrapResult(
            action="created",
            user=user,
            message=f"created bootstrap admin {user.username!r}",
        )
    finally:
        db.close()


@dataclass(frozen=True)
class ResetResult:
    """Outcome of :func:`reset_admin_password_cli`.

    Attributes:
        action: ``"created"`` when a fresh admin row was inserted,
            ``"updated"`` when an existing row had its password /
            role flags refreshed, ``"aborted"`` when interactive input
            was cancelled.
        username: The account that was touched.
        message: Human-readable summary.
    """

    action: str
    username: str
    message: str


def reset_admin_password_cli(
    data_dir: Path,
    username: str,
    *,
    prompt_password: Callable[[], str] = lambda: getpass.getpass(
        "New password: "
    ),
    confirm_password: Callable[[], str] = lambda: getpass.getpass(
        "Confirm password: "
    ),
    display_name: Optional[str] = None,
) -> ResetResult:
    """Create or reset an admin account interactively.

    Used by ``python -m posrat create-admin <username>`` — the CLI
    wrapper (in :mod:`posrat.__main__`) handles argument parsing and
    forwards here. The prompts are pluggable so tests can drive the
    flow with deterministic strings instead of hijacking stdin.

    Behaviour:

    - If the user does not exist → insert a fresh admin row.
    - If the user exists and is *internal* → replace the hash and
      force ``is_admin=True`` + ``can_use_designer=True``.
    - If the user exists as a *proxy* account → refuse with
      :class:`ValueError` (proxy accounts never carry passwords;
      promoting them would silently change the auth source).

    Raises:
        ValueError: when passwords mismatch, are empty, or the
            existing row is a proxy account.
    """

    if not username.strip():
        raise ValueError("username must not be empty")

    password = prompt_password()
    if not password:
        raise ValueError("password must not be empty")
    confirm = confirm_password()
    if confirm != password:
        raise ValueError("passwords do not match")

    db = open_system_db(resolve_system_db_path(data_dir))
    try:
        existing = get_user(db, username)
        if existing is None:
            create_user(
                db,
                username=username,
                auth_source="internal",
                password_hash=hash_password(password),
                display_name=display_name,
                is_admin=True,
                can_use_designer=True,
            )
            return ResetResult(
                action="created",
                username=username,
                message=f"admin account {username!r} created",
            )

        if existing.auth_source != "internal":
            raise ValueError(
                f"user {username!r} is a proxy account;"
                " passwords are managed upstream"
            )

        update_user_password(db, username, hash_password(password))
        update_user_roles(
            db, username, is_admin=True, can_use_designer=True
        )
        return ResetResult(
            action="updated",
            username=username,
            message=f"admin account {username!r} updated",
        )
    finally:
        db.close()
