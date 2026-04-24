"""Tiny shared helpers for the :mod:`posrat.system.admin_*_view` modules.

Kept separate so that splitting ``admin_view.py`` into per-tab modules
does not spawn three copies of the same ``open_system_db(resolve_…)``
incantation. Nothing in this module imports NiceGUI — it is pure
SQLite plumbing.
"""

from __future__ import annotations

from pathlib import Path

from posrat.designer.browser import resolve_data_dir
from posrat.system.system_db import open_system_db, resolve_system_db_path


def open_admin_system_db():
    """Open the global ``system.sqlite`` using the resolved data dir.

    Admin views always operate on the live data directory (never a
    scoped test fixture), so centralising the lookup keeps the intent
    explicit and avoids drift if ``resolve_data_dir`` ever grows new
    behaviour.
    """

    return open_system_db(
        resolve_system_db_path(Path(resolve_data_dir()))
    )


__all__ = ["open_admin_system_db"]
