"""Schema migration runner for POSRAT SQLite databases.

The runner is intentionally tiny: it looks at the ``schema_version`` table
(if it exists), applies every migration from :data:`schema.MIGRATIONS`
whose target version is greater than the current one, and bumps the stored
version after each script. Calling :func:`apply_migrations` on an already
up-to-date database is a no-op, so it is safe to invoke on every connect.
"""

from __future__ import annotations

import sqlite3

from posrat.storage.schema import MIGRATIONS

CURRENT_SCHEMA_VERSION: int = max(MIGRATIONS) if MIGRATIONS else 0


def _current_version(db: sqlite3.Connection) -> int:
    """Return the current schema version or 0 when the table is missing."""
    try:
        row = db.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row is not None else 0


def apply_migrations(db: sqlite3.Connection) -> int:
    """Bring ``db`` up to :data:`CURRENT_SCHEMA_VERSION`.

    Also enables ``PRAGMA foreign_keys = ON`` on the connection so downstream
    DAO code can rely on ``ON DELETE CASCADE`` semantics. Returns the schema
    version after migrations have been applied.
    """
    db.execute("PRAGMA foreign_keys = ON")
    current = _current_version(db)
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        db.executescript(MIGRATIONS[version])
        db.execute("UPDATE schema_version SET version = ?", (version,))
        current = version
    db.commit()
    return current
