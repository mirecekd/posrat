"""Tests for :func:`posrat.storage.open_db` (step 2.4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from posrat.storage import CURRENT_SCHEMA_VERSION, open_db


def test_open_db_creates_file_and_migrates(tmp_path: Path) -> None:
    """open_db must create the DB file, apply migrations, set Row factory."""
    db_path = tmp_path / "nested" / "exam.sqlite"
    assert not db_path.exists()

    db = open_db(db_path)
    try:
        assert db_path.exists()
        assert isinstance(db, sqlite3.Connection)
        assert db.row_factory is sqlite3.Row

        version_row = db.execute("SELECT version FROM schema_version").fetchone()
        assert version_row["version"] == CURRENT_SCHEMA_VERSION

        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"schema_version", "exams", "questions", "choices"} <= tables

        pragma = db.execute("PRAGMA foreign_keys").fetchone()
        assert pragma[0] == 1
    finally:
        db.close()


def test_open_db_reopen_is_idempotent(tmp_path: Path) -> None:
    """Opening the same file twice must not re-run migrations destructively."""
    db_path = tmp_path / "exam.sqlite"

    first = open_db(db_path)
    try:
        first.execute(
            "INSERT INTO exams (id, name, description, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("exam-1", "Seed", None, "2025-01-01T00:00:00Z"),
        )
        first.commit()
    finally:
        first.close()

    second = open_db(db_path)
    try:
        rows = second.execute("SELECT id FROM exams").fetchall()
        assert [row["id"] for row in rows] == ["exam-1"]

        version_row = second.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        assert version_row["version"] == CURRENT_SCHEMA_VERSION
    finally:
        second.close()
