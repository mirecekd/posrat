"""Tests for :mod:`posrat.system.system_db` — system DB bootstrap."""

from __future__ import annotations

from pathlib import Path

from posrat.system import (
    CURRENT_SYSTEM_SCHEMA_VERSION,
    MIGRATIONS,
    SYSTEM_DB_FILENAME,
    apply_system_migrations,
    open_system_db,
    resolve_system_db_path,
)


def test_system_db_filename_constant() -> None:
    """Filename is pinned so Docker/nginx setups can reason about it."""

    assert SYSTEM_DB_FILENAME == "system.sqlite"


def test_current_system_schema_version_matches_migrations() -> None:
    """``CURRENT_SYSTEM_SCHEMA_VERSION`` tracks the highest migration key.

    Guards against drift when a new migration is appended without a
    paired constant bump — the next test in :mod:`test_system_db` (and
    10.2 users DAO tests) relies on this equality to detect "missing
    migration step" bugs early.
    """

    assert CURRENT_SYSTEM_SCHEMA_VERSION == max(MIGRATIONS)


def test_resolve_system_db_path_joins_filename_to_data_dir(
    tmp_path: Path,
) -> None:
    """Pure helper is just ``Path(data_dir) / SYSTEM_DB_FILENAME``."""

    data_dir = tmp_path / "posrat-data"
    resolved = resolve_system_db_path(data_dir)
    assert resolved == data_dir / SYSTEM_DB_FILENAME
    # Pure string transformation: no mkdir side effect.
    assert not data_dir.exists()


def test_resolve_system_db_path_accepts_str(tmp_path: Path) -> None:
    """``Path`` coercion works for plain strings (common in env var paths)."""

    data_dir = str(tmp_path / "via-string")
    resolved = resolve_system_db_path(data_dir)
    assert resolved == Path(data_dir) / SYSTEM_DB_FILENAME


def test_open_system_db_creates_parent_directory(tmp_path: Path) -> None:
    """Matches :func:`posrat.storage.connection.open_db` behaviour."""

    db_path = tmp_path / "nested" / "parent" / SYSTEM_DB_FILENAME
    assert not db_path.parent.exists()
    connection = open_system_db(db_path)
    try:
        assert db_path.parent.is_dir()
        assert db_path.is_file()
    finally:
        connection.close()


def test_open_system_db_brings_schema_to_current_version(
    tmp_path: Path,
) -> None:
    """Fresh DB should end at ``CURRENT_SYSTEM_SCHEMA_VERSION``."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    connection = open_system_db(db_path)
    try:
        row = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        assert row is not None
        assert row["version"] == CURRENT_SYSTEM_SCHEMA_VERSION
    finally:
        connection.close()


def test_open_system_db_enables_foreign_keys(tmp_path: Path) -> None:
    """ACL tables added in step 10.8 rely on cascade deletes."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    connection = open_system_db(db_path)
    try:
        row = connection.execute("PRAGMA foreign_keys").fetchone()
        # Return column is an integer 0/1 regardless of row factory.
        assert tuple(row)[0] == 1
    finally:
        connection.close()


def test_apply_system_migrations_is_idempotent(tmp_path: Path) -> None:
    """Re-running on an up-to-date DB is a no-op and keeps version pinned."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    connection = open_system_db(db_path)
    try:
        first = apply_system_migrations(connection)
        second = apply_system_migrations(connection)
        third = apply_system_migrations(connection)
        assert first == second == third == CURRENT_SYSTEM_SCHEMA_VERSION
        # Only a single row should exist in schema_version (no duplicate
        # INSERTs from the bootstrap migration re-executing).
        rows = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["version"] == CURRENT_SYSTEM_SCHEMA_VERSION
    finally:
        connection.close()


def test_apply_system_migrations_reopens_existing_file(tmp_path: Path) -> None:
    """Closing + reopening must not roll the version back to 0."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    first = open_system_db(db_path)
    first.close()

    second = open_system_db(db_path)
    try:
        row = second.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        assert row["version"] == CURRENT_SYSTEM_SCHEMA_VERSION
    finally:
        second.close()


def test_schema_version_table_always_present(tmp_path: Path) -> None:
    """The bookkeeping table survives every migration.

    Originally this test locked the v1 surface area ("schema_version
    only") — after v2 adds ``users`` the assertion was widened to just
    confirm the bookkeeping row is there regardless of what subsequent
    migrations pile on. The stricter per-migration surface is covered
    by the migration-specific tests in :mod:`tests.test_users_repo`.
    """

    db_path = tmp_path / SYSTEM_DB_FILENAME
    connection = open_system_db(db_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = [row["name"] for row in rows]
        assert "schema_version" in tables
    finally:
        connection.close()


def test_migration_v1_seeds_version_zero_row(tmp_path: Path) -> None:
    """Sanity: migration runner bumps 0 → 1 after inserting the seed row."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    # Open the raw connection *before* calling open_system_db so we can
    # exercise the runner against an empty file.
    import sqlite3

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        final = apply_system_migrations(connection)
        assert final == CURRENT_SYSTEM_SCHEMA_VERSION
        rows = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
        assert [row["version"] for row in rows] == [
            CURRENT_SYSTEM_SCHEMA_VERSION
        ]
    finally:
        connection.close()


def test_apply_system_migrations_commits_between_steps(
    tmp_path: Path,
) -> None:
    """A second fresh connection sees the migrated state without explicit commit."""

    db_path = tmp_path / SYSTEM_DB_FILENAME
    writer = open_system_db(db_path)
    writer.close()

    import sqlite3

    reader = sqlite3.connect(db_path)
    reader.row_factory = sqlite3.Row
    try:
        row = reader.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        assert row is not None
        assert row["version"] == CURRENT_SYSTEM_SCHEMA_VERSION
    finally:
        reader.close()


def test_migrations_dict_starts_at_version_one() -> None:
    """Lowest migration key should be 1 (0 is the implicit empty DB)."""

    assert min(MIGRATIONS) == 1
