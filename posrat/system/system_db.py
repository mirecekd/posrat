"""Connection helpers and migration runner for ``system.sqlite``.

This module is the system-database counterpart to
:mod:`posrat.storage.connection` and :mod:`posrat.storage.migrations`.
Splitting the two means the per-exam schema and the cross-cutting
admin/auth schema can evolve independently without their version
counters stepping on each other.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from posrat.system.schema import MIGRATIONS

PathLike = Union[str, Path]


#: Filename of the global system database inside the POSRAT data
#: directory. Designer / Runner per-exam files live alongside it as
#: ``<exam_id>.sqlite``; this single ``system.sqlite`` holds the cross-
#: cutting state (users, ACLs, access requests, audit logs).
SYSTEM_DB_FILENAME = "system.sqlite"


#: Current target version of the system database. Derived from
#: :data:`MIGRATIONS` so adding a new migration automatically bumps the
#: expected version — callers compare the post-migration return value of
#: :func:`apply_system_migrations` against this constant in tests.
CURRENT_SYSTEM_SCHEMA_VERSION: int = max(MIGRATIONS) if MIGRATIONS else 0


def resolve_system_db_path(data_dir: PathLike) -> Path:
    """Return the canonical path to ``system.sqlite`` inside ``data_dir``.

    Mirrors :func:`posrat.designer.browser.resolve_assets_dir` in spirit:
    a pure string transformation with no filesystem side effects. The
    caller is responsible for passing the result into
    :func:`open_system_db`, which performs the ``mkdir`` / ``connect``
    dance.
    """

    return Path(data_dir) / SYSTEM_DB_FILENAME


def _current_version(db: sqlite3.Connection) -> int:
    """Return the current schema version or 0 when the table is missing."""

    try:
        row = db.execute("SELECT version FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row is not None else 0


#: Tables owned by the system database (listed in migration order).
#: Used by :func:`apply_system_migrations` to detect a corrupted file
#: — typically a ``system.sqlite`` that was mistakenly opened by the
#: per-exam migration runner in older builds (Runner picker prior to
#: the ``list_exam_files`` filter). Such a file carries per-exam
#: tables (``questions``, ``choices``, ``sessions``, ...) which don't
#: belong here; bailing loudly beats crashing later with "no such
#: table: user_exam_access".
_SYSTEM_DB_TABLES = frozenset(
    {
        "schema_version",
        "users",
        "user_exam_access",
        "exam_access_requests",
    }
)


def apply_system_migrations(db: sqlite3.Connection) -> int:
    """Bring ``db`` up to :data:`CURRENT_SYSTEM_SCHEMA_VERSION`.

    Enables ``PRAGMA foreign_keys = ON`` so downstream DAO code can rely
    on ``ON DELETE CASCADE`` semantics (the ACL tables introduced in
    step 10.8 depend on this). Returns the schema version the database
    is on after migrations have been applied — safe to call on an
    already up-to-date database (no-op).

    Raises :class:`RuntimeError` when the file on disk carries tables
    that don't belong to the system database (e.g. ``questions``,
    ``sessions``). That state originates from pre-Phase-10 builds
    where the Runner picker accidentally ran per-exam migrations on
    ``system.sqlite``. The operator should remove / rename the file
    and let the next start recreate a clean system DB.
    """

    db.execute("PRAGMA foreign_keys = ON")
    current = _current_version(db)

    # Detect stray per-exam tables on the system DB before running any
    # migrations — a loud error beats a downstream "no such table"
    # crash once the real ACL queries fire.
    existing = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    stray = existing - _SYSTEM_DB_TABLES
    if stray and current > CURRENT_SYSTEM_SCHEMA_VERSION:
        # Only flag when we also see an out-of-range version — a fresh
        # install with extra tables from a future migration is a bug
        # worth surfacing too. A partial migration that just hasn't
        # seen v3 yet is normal and should proceed.
        raise RuntimeError(
            "system.sqlite contains unexpected tables "
            f"{sorted(stray)!r}; the file was likely written by an "
            "older build's per-exam migration runner. Move it aside "
            "(e.g. ``mv system.sqlite system.sqlite.bak``) and "
            "restart to recreate a clean one."
        )

    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        db.executescript(MIGRATIONS[version])
        db.execute("UPDATE schema_version SET version = ?", (version,))
        current = version
    db.commit()
    return current


def open_system_db(path: PathLike) -> sqlite3.Connection:
    """Open (or create) the POSRAT system database at ``path``.

    Behaviour mirrors :func:`posrat.storage.connection.open_db`:

    - Parent directory is created on demand so callers can hand over a
      fresh path inside a temporary test directory.
    - ``PRAGMA foreign_keys = ON`` is enabled through
      :func:`apply_system_migrations`.
    - Row factory is set to :class:`sqlite3.Row` for ergonomic access.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    apply_system_migrations(connection)
    return connection
