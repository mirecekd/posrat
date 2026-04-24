"""NiceGUI login page and auth guard helpers.

Three responsibilities, kept in one module so the guard and the page
handler share the same constants and storage key without a circular
import dance:

- :func:`render_login` — the ``/login`` body. Username + password
  inputs, a submit button, and an error surface below them.
- :func:`logout_current_user` — called from the header "Log out"
  button. Clears the auth stash and navigates home; the root page's
  guard then redirects to ``/login``.
- :func:`require_auth` — guard helper invoked at the top of every
  ``@ui.page`` handler that expects a signed-in user. Redirects to
  ``/login`` when no session is active and returns ``None``; otherwise
  returns the hydrated :class:`~posrat.models.User`.

The guard uses :func:`resolve_effective_user` so proxy-auth deployments
(nginx ``X-Remote-User`` / ALB Cognito) bypass the form automatically
and sign in on the first request. The form only matters for internal
accounts or for local dev behind no proxy at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import app, ui

from posrat.designer import resolve_data_dir
from posrat.models import User
from posrat.runner.view_helpers import request_headers
from posrat.system.auth_service import (
    authenticate_internal,
    resolve_effective_user,
)
from posrat.system.auth_session import (
    AUTH_STORAGE_KEY,
    build_auth_stash,
    read_username_from_stash,
)
from posrat.system.system_db import open_system_db, resolve_system_db_path


#: URL the login page lives on. Kept as a module-level constant so
#: both :mod:`posrat.app` (route registration) and the guard redirect
#: pull from the same string.
LOGIN_ROUTE = "/login"


def _open_system_db():
    """Open the system database sitting next to the exam data dir."""

    return open_system_db(
        resolve_system_db_path(Path(resolve_data_dir()))
    )


def require_auth() -> Optional[User]:
    """Return the current :class:`User` or redirect to the login form.

    Resolution order (delegated to
    :func:`resolve_effective_user`):

    1. Signed-in ``app.storage.user[AUTH_STORAGE_KEY]`` — internal
       or previously-provisioned proxy user.
    2. Trusted reverse-proxy header — auto-provisions a proxy
       account on first sight.

    When neither fires, the helper triggers a client-side navigation
    to :data:`LOGIN_ROUTE` and returns ``None``. Callers typically
    guard their body with ``if user is None: return`` so NiceGUI
    skips rendering half-initialised widgets.
    """

    stash = app.storage.user.get(AUTH_STORAGE_KEY)
    session_username = read_username_from_stash(stash)

    db = _open_system_db()
    try:
        user = resolve_effective_user(
            db,
            session_username=session_username,
            headers=request_headers(),
        )
    finally:
        db.close()

    if user is None:
        # Someone with a stale cookie for a deleted user would loop
        # between pages — clear the stash defensively so the form
        # below gets a fresh start.
        app.storage.user.pop(AUTH_STORAGE_KEY, None)
        ui.navigate.to(LOGIN_ROUTE)
        return None

    # Keep the cookie in lock-step with the DB — when a proxy header
    # just provisioned a row, the stash was empty a moment ago.
    if session_username != user.username:
        app.storage.user[AUTH_STORAGE_KEY] = build_auth_stash(user)

    return user


def logout_current_user() -> None:
    """Clear the auth stash and go home (where the guard redirects)."""

    app.storage.user.pop(AUTH_STORAGE_KEY, None)
    ui.navigate.to("/")


def render_login() -> None:
    """Render the ``/login`` page body.

    A minimal card with username + password inputs. Submission calls
    :func:`authenticate_internal`; on success we stash the auth blob
    and navigate home, on failure a red caption surfaces "Invalid
    credentials." without leaking whether the username exists.

    If the user already has a valid session (or the proxy header is
    set), we skip the form and navigate home straight away —
    re-visiting ``/login`` when already signed in is almost always a
    typo.
    """

    stash = app.storage.user.get(AUTH_STORAGE_KEY)
    if read_username_from_stash(stash):
        ui.navigate.to("/")
        return

    # Also bypass the form when a trusted proxy header is present —
    # ``resolve_effective_user`` will auto-provision on the next
    # navigation.
    headers = request_headers()
    if headers:
        db = _open_system_db()
        try:
            user = resolve_effective_user(db, headers=headers)
        finally:
            db.close()
        if user is not None:
            app.storage.user[AUTH_STORAGE_KEY] = build_auth_stash(user)
            ui.navigate.to("/")
            return

    with ui.card().classes("q-pa-md").style("min-width: 320px"):
        ui.label("Sign in").classes("text-h5 q-mb-md")
        username_input = ui.input("Username").props("autofocus").classes("w-full")
        password_input = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props("type=password")
            .classes("w-full q-mb-sm")
        )
        error_label = (
            ui.label("").classes("text-negative q-mb-sm").style(
                "min-height: 1.25rem"
            )
        )

        def _attempt_login() -> None:
            username = (username_input.value or "").strip()
            password = password_input.value or ""
            if not username or not password:
                error_label.text = "Enter username and password."
                return

            db = _open_system_db()
            try:
                user = authenticate_internal(db, username, password)
            finally:
                db.close()

            if user is None:
                error_label.text = "Invalid credentials."
                # Clear the password field so a retry doesn't double-submit
                # the same value on Enter.
                password_input.value = ""
                return

            app.storage.user[AUTH_STORAGE_KEY] = build_auth_stash(user)
            ui.navigate.to("/")

        # Pressing Enter in either input triggers the submit — matches
        # user expectations for a minimal login form.
        password_input.on("keydown.enter", _attempt_login)
        username_input.on("keydown.enter", _attempt_login)

        ui.button("Sign in", on_click=_attempt_login).props(
            "color=primary"
        ).classes("full-width q-mt-md")

        ui.separator().classes("q-my-md")
        ui.button(
            "Request access", on_click=_show_access_request_info
        ).props("flat color=secondary").classes("full-width")


def _show_access_request_info() -> None:
    """Explain the access-request flow to a not-yet-signed-in visitor.

    The actual per-exam "Request access" button lives on the Runner
    picker after login (step 10.15). Users who hit the login form
    without an account need a lightweight contact channel instead —
    this placeholder dialog tells them what to do until an in-app
    self-service signup lands. Kept deliberately simple: real email
    routing / signup queue is future work.
    """

    with ui.dialog() as dialog, ui.card():
        ui.label("Request access").classes("text-h6")
        ui.label(
            "If you don't have a POSRAT account yet, please contact the"
            " system administrator."
        ).classes("text-body2")
        with ui.row().classes("justify-end q-mt-md"):
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


__all__ = [
    "LOGIN_ROUTE",
    "logout_current_user",
    "render_login",
    "require_auth",
]
