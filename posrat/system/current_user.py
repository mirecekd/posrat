"""Request-scoped current-user resolver for visibility gating.

A thin wrapper around :func:`posrat.system.auth_service.resolve_effective_user`
tailored to UI-layer "should I render this widget?" checks. Unlike
:func:`posrat.system.login_view.require_auth`, this helper **never
redirects** — a missing session simply returns ``None`` and the caller
is expected to render nothing / a degraded surface.

Introduced in Phase 14 for the per-user AI chat + Runner-explanation
feature flags. Extracted into its own module so the AI widget and the
Runner views can share one lookup without duplicating the
``open_system_db`` boilerplate.

Imports from :mod:`posrat.system.auth_service` and
:mod:`posrat.runner.view_helpers` are **deferred to call time** — the
runner package pulls this module in through the session-detail view,
and ``auth_service`` itself transitively pulls the runner package in
through ``runner.identity``. Top-level imports would form a cycle
that aborts test collection on a cold start.
"""

from __future__ import annotations

from typing import Optional

from nicegui import app

from posrat.designer.browser import resolve_data_dir
from posrat.models import User
from posrat.system.auth_session import (
    AUTH_STORAGE_KEY,
    read_username_from_stash,
)
from posrat.system.system_db import open_system_db, resolve_system_db_path


def current_user_or_none() -> Optional[User]:
    """Return the :class:`User` for the current request, or ``None``.

    Mirrors :func:`posrat.system.login_view.require_auth` minus the
    ``ui.navigate.to(/login)`` side effect — callers use this strictly
    for visibility decisions. The helper tolerates being invoked
    outside a request scope (returns ``None`` when
    ``app.storage.user`` is unavailable), so tests exercising
    rendering functions directly don't need a full NiceGUI harness.
    """

    # Lazy imports — see module docstring for the cycle context.
    from posrat.runner.view_helpers import request_headers
    from posrat.system.auth_service import resolve_effective_user

    try:
        stash = app.storage.user.get(AUTH_STORAGE_KEY)
    except RuntimeError:
        # ``app.storage.user`` raises when there is no active
        # request — treat the same as "not signed in".
        return None

    session_username = read_username_from_stash(stash)
    db = open_system_db(resolve_system_db_path(resolve_data_dir()))
    try:
        return resolve_effective_user(
            db,
            session_username=session_username,
            headers=request_headers(),
        )
    finally:
        db.close()


__all__ = ["current_user_or_none"]
