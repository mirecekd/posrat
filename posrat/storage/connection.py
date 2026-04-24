"""Database connection helpers for POSRAT.

The only public entry point is :func:`open_db`, which takes a filesystem
path, opens a SQLite connection, and automatically brings the schema up
to the current version via :func:`posrat.storage.migrations.apply_migrations`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from posrat.storage.migrations import apply_migrations

PathLike = Union[str, Path]


def open_db(path: PathLike) -> sqlite3.Connection:
    """Open (or create) a POSRAT SQLite database at ``path`` and migrate it.

    - Parent directory is created on demand so callers can pass a fresh path.
    - ``PRAGMA foreign_keys = ON`` is enabled through ``apply_migrations``.
    - Row factory is set to :class:`sqlite3.Row` for ergonomic access.

    Note: the helper will happily create a new empty database on a
    missing path — this matches ``sqlite3.connect`` semantics. Callers
    that render stale UI state (e.g. the Designer with a path cached
    in ``app.storage.user``) must validate the path exists **before**
    opening the DB; see :func:`posrat.designer.browser._prune_stale_open_exam`
    for the helper that guards the Designer against resurrecting a
    just-deleted exam as an empty skeleton.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    apply_migrations(connection)
    return connection


