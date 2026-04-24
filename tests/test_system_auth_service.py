"""Tests for :mod:`posrat.system.auth_service`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from posrat.system import (
    SYSTEM_DB_FILENAME,
    authenticate_internal,
    create_user,
    get_user,
    hash_password,
    open_system_db,
    provision_proxy_user,
    resolve_effective_user,
)

FAST_ROUNDS = 4


def _db(tmp_path: Path) -> sqlite3.Connection:
    return open_system_db(tmp_path / SYSTEM_DB_FILENAME)


# ---------- authenticate_internal ----------


def test_authenticate_internal_happy_path(tmp_path: Path) -> None:
    """Correct password returns the user and bumps last_login_at."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash=hash_password("s3cret", rounds=FAST_ROUNDS),
        )
        user = authenticate_internal(
            db, "alice", "s3cret", timestamp="2026-04-23T15:00:00Z"
        )
        assert user is not None
        assert user.username == "alice"
        assert user.last_login_at == "2026-04-23T15:00:00Z"
    finally:
        db.close()


def test_authenticate_internal_wrong_password(tmp_path: Path) -> None:
    """Bad password returns None without stamping last_login_at."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash=hash_password("s3cret", rounds=FAST_ROUNDS),
        )
        assert authenticate_internal(db, "alice", "w0rng") is None
        loaded = get_user(db, "alice")
        assert loaded is not None
        assert loaded.last_login_at is None
    finally:
        db.close()


def test_authenticate_internal_unknown_user(tmp_path: Path) -> None:
    """Unknown username → None."""

    db = _db(tmp_path)
    try:
        assert authenticate_internal(db, "ghost", "anything") is None
    finally:
        db.close()


def test_authenticate_internal_refuses_proxy_account(tmp_path: Path) -> None:
    """Proxy accounts have NULL password_hash — never authenticate."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="bob",
            auth_source="proxy",
            password_hash=None,
        )
        assert authenticate_internal(db, "bob", "whatever") is None
    finally:
        db.close()


# ---------- provision_proxy_user ----------


def test_provision_proxy_user_creates_on_first_sight(tmp_path: Path) -> None:
    """First call creates the row as a proxy account."""

    db = _db(tmp_path)
    try:
        user = provision_proxy_user(
            db,
            "carol",
            display_name="Carol Danvers",
            timestamp="2026-04-23T15:00:00Z",
        )
        assert user.auth_source == "proxy"
        assert user.password_hash is None
        assert user.display_name == "Carol Danvers"
        assert user.last_login_at == "2026-04-23T15:00:00Z"
        assert user.created_at == "2026-04-23T15:00:00Z"
    finally:
        db.close()


def test_provision_proxy_user_is_idempotent(tmp_path: Path) -> None:
    """Second call returns the existing row and only updates last_login_at."""

    db = _db(tmp_path)
    try:
        first = provision_proxy_user(
            db, "carol", timestamp="2026-04-23T15:00:00Z"
        )
        second = provision_proxy_user(
            db, "carol", timestamp="2026-04-23T15:05:00Z"
        )
        assert second.created_at == first.created_at
        assert second.last_login_at == "2026-04-23T15:05:00Z"
    finally:
        db.close()


def test_provision_proxy_user_keeps_display_name_on_revisit(
    tmp_path: Path,
) -> None:
    """Second call does NOT overwrite the admin-edited display name."""

    db = _db(tmp_path)
    try:
        provision_proxy_user(
            db, "carol", display_name="Original"
        )
        # Second call supplies a different display name; it must be
        # ignored because the row already exists.
        refreshed = provision_proxy_user(
            db, "carol", display_name="Something Else"
        )
        assert refreshed.display_name == "Original"
    finally:
        db.close()


# ---------- resolve_effective_user ----------


def test_resolve_effective_user_session_stash_wins(tmp_path: Path) -> None:
    """Session-stored username beats everything else."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash=hash_password("pw", rounds=FAST_ROUNDS),
        )
        user = resolve_effective_user(
            db,
            session_username="alice",
            headers={"X-Remote-User": "proxy-wins-normally"},
        )
        assert user is not None
        assert user.username == "alice"
    finally:
        db.close()


def test_resolve_effective_user_stale_session_falls_through_to_header(
    tmp_path: Path,
) -> None:
    """Session cookie for a deleted user must not shadow a valid proxy header."""

    db = _db(tmp_path)
    try:
        user = resolve_effective_user(
            db,
            session_username="deleted-admin",
            headers={"X-Remote-User": "bob"},
        )
        assert user is not None
        assert user.username == "bob"
        assert user.auth_source == "proxy"
    finally:
        db.close()


def test_resolve_effective_user_proxy_header(tmp_path: Path) -> None:
    """Header-only (no session) triggers provision_proxy_user."""

    db = _db(tmp_path)
    try:
        user = resolve_effective_user(
            db, headers={"X-Remote-User": "proxy-sam"}
        )
        assert user is not None
        assert user.username == "proxy-sam"
        assert user.auth_source == "proxy"
    finally:
        db.close()


def test_resolve_effective_user_custom_header_name(tmp_path: Path) -> None:
    """Alternative header names (X-Forwarded-User etc.) honoured via kwarg."""

    db = _db(tmp_path)
    try:
        user = resolve_effective_user(
            db,
            headers={"X-Forwarded-User": "cognito-sam"},
            header_name="X-Forwarded-User",
        )
        assert user is not None
        assert user.username == "cognito-sam"
    finally:
        db.close()


def test_resolve_effective_user_ignores_whitespace_header(
    tmp_path: Path,
) -> None:
    """A blank header (``""`` / ``"   "``) must not create empty accounts."""

    db = _db(tmp_path)
    try:
        assert (
            resolve_effective_user(db, headers={"X-Remote-User": "   "})
            is None
        )
        assert (
            resolve_effective_user(db, headers={"X-Remote-User": ""})
            is None
        )
    finally:
        db.close()


def test_resolve_effective_user_returns_none_without_auth(
    tmp_path: Path,
) -> None:
    """No session, no header → None. Caller redirects to /login."""

    db = _db(tmp_path)
    try:
        assert resolve_effective_user(db) is None
        assert resolve_effective_user(db, headers={}) is None
    finally:
        db.close()


def test_resolve_effective_user_does_not_use_env_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 10 decision: no dev-bypass via ``USER`` env var.

    The runner's ``resolve_username`` helper still consults ``USER`` for
    candidate-name prefill in session dialogs — but the auth service
    deliberately does not, so a production box without proxy auth can't
    be tricked into auto-signing you in just because someone is logged
    into the OS.
    """

    monkeypatch.setenv("USER", "os-user")
    db = _db(tmp_path)
    try:
        assert resolve_effective_user(db) is None
    finally:
        db.close()
