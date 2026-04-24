"""Tests for :mod:`posrat.system.bootstrap` — admin seeding + CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from posrat.system import (
    ADMIN_DISPLAY_NAME_ENV,
    ADMIN_PASSWORD_ENV,
    ADMIN_USERNAME_ENV,
    SYSTEM_DB_FILENAME,
    bootstrap_admin_from_env,
    count_admins,
    create_user,
    get_user,
    hash_password,
    open_system_db,
    reset_admin_password_cli,
    verify_password,
)


FAST_ROUNDS = 4


# ---------- bootstrap_admin_from_env ----------


def test_bootstrap_creates_admin_when_db_empty(tmp_path: Path) -> None:
    """Fresh install + env vars set → admin is provisioned."""

    env = {
        ADMIN_USERNAME_ENV: "alice",
        ADMIN_PASSWORD_ENV: "s3cret",
        ADMIN_DISPLAY_NAME_ENV: "Alice L.",
    }
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "created"
    assert result.user is not None
    assert result.user.username == "alice"
    assert result.user.display_name == "Alice L."
    assert result.user.is_admin is True
    assert result.user.can_use_designer is True

    # Verify the hash landed correctly — end-to-end round-trip.
    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        persisted = get_user(db, "alice")
        assert persisted is not None
        assert persisted.password_hash is not None
        assert verify_password("s3cret", persisted.password_hash)
    finally:
        db.close()


def test_bootstrap_display_name_optional(tmp_path: Path) -> None:
    """Absent display name env var → stored value is ``None``."""

    env = {
        ADMIN_USERNAME_ENV: "alice",
        ADMIN_PASSWORD_ENV: "s3cret",
    }
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "created"
    assert result.user is not None
    assert result.user.display_name is None


def test_bootstrap_skips_when_any_user_exists(tmp_path: Path) -> None:
    """Existing non-admin user is enough to short-circuit the bootstrap.

    Operators resetting a database by just dropping the admin row
    (leaving other accounts in place) should reach for the CLI, not
    the startup hook — refusing here prevents accidental admin leaks.
    """

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        create_user(
            db,
            username="regular",
            auth_source="internal",
            password_hash=hash_password("whatever", rounds=FAST_ROUNDS),
        )
    finally:
        db.close()

    env = {
        ADMIN_USERNAME_ENV: "alice",
        ADMIN_PASSWORD_ENV: "s3cret",
    }
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "skipped"
    assert result.user is None


def test_bootstrap_skips_when_admin_already_exists(tmp_path: Path) -> None:
    """Idempotent re-runs keep the existing admin untouched."""

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        create_user(
            db,
            username="root",
            auth_source="internal",
            password_hash=hash_password("original", rounds=FAST_ROUNDS),
            is_admin=True,
        )
    finally:
        db.close()

    env = {
        ADMIN_USERNAME_ENV: "alice",
        ADMIN_PASSWORD_ENV: "would-be-new",
    }
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "skipped"

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        assert get_user(db, "alice") is None
        root = get_user(db, "root")
        assert root is not None
        assert verify_password("original", root.password_hash or "")
    finally:
        db.close()


def test_bootstrap_reports_missing_env_vars(tmp_path: Path) -> None:
    """Empty DB + no env vars → ``env_missing`` with guidance message."""

    result = bootstrap_admin_from_env(tmp_path, env={})
    assert result.action == "env_missing"
    assert ADMIN_USERNAME_ENV in result.message
    assert ADMIN_PASSWORD_ENV in result.message
    # No row leaked through.
    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        assert count_admins(db) == 0
    finally:
        db.close()


def test_bootstrap_reports_env_password_missing(tmp_path: Path) -> None:
    """Username without password counts as env_missing."""

    env = {ADMIN_USERNAME_ENV: "alice"}
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "env_missing"


def test_bootstrap_rejects_blank_values(tmp_path: Path) -> None:
    """Whitespace-only env vars are treated as missing."""

    env = {
        ADMIN_USERNAME_ENV: "   ",
        ADMIN_PASSWORD_ENV: "s3cret",
    }
    result = bootstrap_admin_from_env(tmp_path, env=env)
    assert result.action == "env_missing"


# ---------- reset_admin_password_cli ----------


def test_reset_creates_new_admin(tmp_path: Path) -> None:
    """Unknown username → fresh admin row."""

    result = reset_admin_password_cli(
        tmp_path,
        "alice",
        prompt_password=lambda: "s3cret",
        confirm_password=lambda: "s3cret",
        display_name="Alice L.",
    )
    assert result.action == "created"

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        user = get_user(db, "alice")
        assert user is not None
        assert user.is_admin is True
        assert user.can_use_designer is True
        assert user.display_name == "Alice L."
        assert verify_password("s3cret", user.password_hash or "")
    finally:
        db.close()


def test_reset_updates_existing_internal_admin(tmp_path: Path) -> None:
    """Known internal user → password rotated, admin flags re-asserted."""

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash=hash_password("old", rounds=FAST_ROUNDS),
            is_admin=False,
            can_use_designer=False,
        )
    finally:
        db.close()

    result = reset_admin_password_cli(
        tmp_path,
        "alice",
        prompt_password=lambda: "n3w",
        confirm_password=lambda: "n3w",
    )
    assert result.action == "updated"

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        user = get_user(db, "alice")
        assert user is not None
        assert user.is_admin is True
        assert user.can_use_designer is True
        assert verify_password("n3w", user.password_hash or "")
        assert not verify_password("old", user.password_hash or "")
    finally:
        db.close()


def test_reset_rejects_mismatched_confirmation(tmp_path: Path) -> None:
    """Typo in confirm prompt → ValueError, no row touched."""

    with pytest.raises(ValueError, match="do not match"):
        reset_admin_password_cli(
            tmp_path,
            "alice",
            prompt_password=lambda: "abc",
            confirm_password=lambda: "xyz",
        )
    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        assert get_user(db, "alice") is None
    finally:
        db.close()


def test_reset_rejects_empty_password(tmp_path: Path) -> None:
    """Empty password → ValueError (cannot authenticate with empty hash)."""

    with pytest.raises(ValueError, match="not be empty"):
        reset_admin_password_cli(
            tmp_path,
            "alice",
            prompt_password=lambda: "",
            confirm_password=lambda: "",
        )


def test_reset_rejects_empty_username(tmp_path: Path) -> None:
    """Empty username → ValueError."""

    with pytest.raises(ValueError, match="username must not be empty"):
        reset_admin_password_cli(
            tmp_path,
            "   ",
            prompt_password=lambda: "abc",
            confirm_password=lambda: "abc",
        )


def test_reset_rejects_proxy_account(tmp_path: Path) -> None:
    """CLI must not promote proxy accounts to password-based auth."""

    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        create_user(
            db,
            username="bob",
            auth_source="proxy",
            password_hash=None,
        )
    finally:
        db.close()

    with pytest.raises(ValueError, match="proxy account"):
        reset_admin_password_cli(
            tmp_path,
            "bob",
            prompt_password=lambda: "abc",
            confirm_password=lambda: "abc",
        )

    # Bob's state is unchanged.
    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    try:
        user = get_user(db, "bob")
        assert user is not None
        assert user.password_hash is None
        assert user.auth_source == "proxy"
    finally:
        db.close()
