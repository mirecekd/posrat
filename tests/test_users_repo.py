"""Tests for :mod:`posrat.system.users_repo` — user CRUD DAO."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from posrat.system import (
    CURRENT_SYSTEM_SCHEMA_VERSION,
    SYSTEM_DB_FILENAME,
    count_admins,
    create_user,
    delete_user,
    get_user,
    list_users,
    open_system_db,
    touch_last_login,
    update_user_password,
    update_user_roles,
)


def _db(tmp_path: Path) -> sqlite3.Connection:
    return open_system_db(tmp_path / SYSTEM_DB_FILENAME)


def test_migration_v2_creates_users_table(tmp_path: Path) -> None:
    """Step 10.2 migration scope: ``users`` table added on top of v1."""

    db = _db(tmp_path)
    try:
        assert (
            db.execute("SELECT version FROM schema_version").fetchone()[
                "version"
            ]
            == CURRENT_SYSTEM_SCHEMA_VERSION
        )
        tables = [
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        # ``users`` was added in v2; later migrations pile on more
        # tables (ACL in v3, ...). The invariant we want here is
        # "after v2 applied, ``users`` exists next to the bookkeeping
        # table", not the full set — the broader surface is covered
        # by migration-specific tests.
        assert "schema_version" in tables
        assert "users" in tables
    finally:
        db.close()


def test_migration_v2_creates_auth_source_index(tmp_path: Path) -> None:
    """Index exists so admin screens can filter by source cheaply."""

    db = _db(tmp_path)
    try:
        rows = db.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='index' AND name='idx_users_auth_source'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        db.close()


def test_create_user_round_trip_internal(tmp_path: Path) -> None:
    """Happy path: insert + load back an internal admin."""

    db = _db(tmp_path)
    try:
        created = create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
            display_name="Alice Liddell",
            is_admin=True,
            can_use_designer=True,
            created_at="2026-04-23T12:00:00Z",
        )
        assert created.username == "alice"
        assert created.is_admin is True
        assert created.can_use_designer is True
        loaded = get_user(db, "alice")
        assert loaded == created
    finally:
        db.close()


def test_create_user_round_trip_proxy(tmp_path: Path) -> None:
    """Proxy users round-trip with ``password_hash=None``."""

    db = _db(tmp_path)
    try:
        created = create_user(
            db,
            username="bob",
            auth_source="proxy",
            password_hash=None,
            display_name=None,
            created_at="2026-04-23T12:00:00Z",
        )
        loaded = get_user(db, "bob")
        assert loaded is not None
        assert loaded.password_hash is None
        assert loaded.auth_source == "proxy"
        assert loaded.effective_display_name == "bob"
    finally:
        db.close()


def test_create_user_rejects_internal_without_password(tmp_path: Path) -> None:
    """Cross-field invariant raised before ``INSERT`` touches SQLite."""

    db = _db(tmp_path)
    try:
        with pytest.raises(ValueError, match="password_hash"):
            create_user(
                db,
                username="orphan",
                auth_source="internal",
                password_hash=None,
            )
        # Row must not leak to disk on the failure path.
        assert get_user(db, "orphan") is None
    finally:
        db.close()


def test_create_user_rejects_proxy_with_password(tmp_path: Path) -> None:
    """Proxy accounts authenticate via header — password would be dead weight."""

    db = _db(tmp_path)
    try:
        with pytest.raises(ValueError, match="proxy accounts"):
            create_user(
                db,
                username="paranoid",
                auth_source="proxy",
                password_hash="bcrypt$stub",
            )
        assert get_user(db, "paranoid") is None
    finally:
        db.close()


def test_create_user_rejects_duplicate_username(tmp_path: Path) -> None:
    """PK constraint surfaces as ``sqlite3.IntegrityError``."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        with pytest.raises(sqlite3.IntegrityError):
            create_user(
                db,
                username="alice",
                auth_source="internal",
                password_hash="bcrypt$other",
            )
    finally:
        db.close()


def test_get_user_returns_none_for_missing(tmp_path: Path) -> None:
    """Missing user is a plain ``None`` (no exception)."""

    db = _db(tmp_path)
    try:
        assert get_user(db, "ghost") is None
    finally:
        db.close()


def test_list_users_returns_alphabetical_order(tmp_path: Path) -> None:
    """Admin UI relies on the DAO's default stable sort."""

    db = _db(tmp_path)
    try:
        for name in ("charlie", "alice", "bob"):
            create_user(
                db,
                username=name,
                auth_source="internal",
                password_hash="bcrypt$stub",
            )
        names = [user.username for user in list_users(db)]
        assert names == ["alice", "bob", "charlie"]
    finally:
        db.close()


def test_list_users_returns_empty_list_for_fresh_db(tmp_path: Path) -> None:
    """New DB → no rows."""

    db = _db(tmp_path)
    try:
        assert list_users(db) == []
    finally:
        db.close()


def test_update_user_roles_flips_flags(tmp_path: Path) -> None:
    """Promote a regular user to admin + designer."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        updated = update_user_roles(
            db, "alice", is_admin=True, can_use_designer=True
        )
        assert updated is True
        loaded = get_user(db, "alice")
        assert loaded is not None
        assert loaded.is_admin is True
        assert loaded.can_use_designer is True
    finally:
        db.close()


def test_update_user_roles_returns_false_for_unknown(tmp_path: Path) -> None:
    """Unknown user is a no-op, matches :func:`delete_question` semantics."""

    db = _db(tmp_path)
    try:
        assert update_user_roles(
            db, "ghost", is_admin=True, can_use_designer=True
        ) is False
    finally:
        db.close()


def test_update_user_password_happy_path(tmp_path: Path) -> None:
    """Replace hash on an internal account."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$old",
        )
        assert update_user_password(db, "alice", "bcrypt$new") is True
        loaded = get_user(db, "alice")
        assert loaded is not None
        assert loaded.password_hash == "bcrypt$new"
    finally:
        db.close()


def test_update_user_password_rejects_empty_hash(tmp_path: Path) -> None:
    """Empty hash is a bug in the caller — raise."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        with pytest.raises(ValueError, match="must not be empty"):
            update_user_password(db, "alice", "")
    finally:
        db.close()


def test_update_user_password_rejects_proxy_account(tmp_path: Path) -> None:
    """Proxy accounts cannot be handed a password — raises with name in message."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="bob",
            auth_source="proxy",
            password_hash=None,
        )
        with pytest.raises(ValueError, match="bob"):
            update_user_password(db, "bob", "bcrypt$illegal")
        loaded = get_user(db, "bob")
        assert loaded is not None
        # The bogus hash must not have landed in the DB.
        assert loaded.password_hash is None
    finally:
        db.close()


def test_update_user_password_returns_false_for_unknown(
    tmp_path: Path,
) -> None:
    """Unknown user → idempotent no-op."""

    db = _db(tmp_path)
    try:
        assert update_user_password(db, "ghost", "bcrypt$nope") is False
    finally:
        db.close()


def test_touch_last_login_stamps_provided_timestamp(tmp_path: Path) -> None:
    """Tests can pass a fixed timestamp for determinism."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        assert touch_last_login(db, "alice", "2026-04-23T13:37:00Z") is True
        loaded = get_user(db, "alice")
        assert loaded is not None
        assert loaded.last_login_at == "2026-04-23T13:37:00Z"
    finally:
        db.close()


def test_touch_last_login_returns_false_for_unknown(tmp_path: Path) -> None:
    """Unknown user → idempotent no-op."""

    db = _db(tmp_path)
    try:
        assert touch_last_login(db, "ghost", "2026-04-23T13:37:00Z") is False
    finally:
        db.close()


def test_delete_user_removes_row(tmp_path: Path) -> None:
    """Happy path: create + delete + verify gone."""

    db = _db(tmp_path)
    try:
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        assert delete_user(db, "alice") is True
        assert get_user(db, "alice") is None
    finally:
        db.close()


def test_delete_user_returns_false_when_missing(tmp_path: Path) -> None:
    """Unknown user → idempotent no-op."""

    db = _db(tmp_path)
    try:
        assert delete_user(db, "ghost") is False
    finally:
        db.close()


def test_count_admins_reflects_current_state(tmp_path: Path) -> None:
    """Admin UI uses this to prevent self-demotion to zero-admin."""

    db = _db(tmp_path)
    try:
        assert count_admins(db) == 0
        create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
            is_admin=True,
        )
        assert count_admins(db) == 1
        create_user(
            db,
            username="bob",
            auth_source="internal",
            password_hash="bcrypt$stub",
            is_admin=True,
        )
        assert count_admins(db) == 2
        update_user_roles(
            db, "bob", is_admin=False, can_use_designer=False
        )
        assert count_admins(db) == 1
    finally:
        db.close()


def test_created_at_defaults_to_current_utc(tmp_path: Path) -> None:
    """Omitting ``created_at`` stamps an ISO-8601 UTC ``...Z`` timestamp."""

    db = _db(tmp_path)
    try:
        user = create_user(
            db,
            username="alice",
            auth_source="internal",
            password_hash="bcrypt$stub",
        )
        assert user.created_at.endswith("Z")
        # ISO parseable as UTC (delegated to Python's parser).
        from datetime import datetime

        datetime.fromisoformat(user.created_at.replace("Z", "+00:00"))
    finally:
        db.close()
