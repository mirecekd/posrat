"""POSRAT NiceGUI application bootstrap.

This module wires the NiceGUI UI layer. It intentionally avoids calling
:func:`nicegui.ui.run` at import time so unit tests can import ``posrat.app``
without side effects.

Current scope (Phase 3):

* Root route ``/`` renders a welcome screen with links to Designer and Runner.
* ``/designer`` and ``/runner`` routes render placeholder screens that later
  phases will fill with the real Designer browser/properties UI and the
  Runner question view.
* :func:`_render_header` paints a shared top navigation bar used by every
  page (includes a dark/light mode toggle that is persisted per user via
  :mod:`nicegui.app.storage`).
* :func:`main` starts the NiceGUI server in a browser tab.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from nicegui import app, ui

from posrat import __version__
from posrat.designer import render_designer, resolve_assets_dir, resolve_data_dir
from posrat.runner import render_runner as _render_runner_body_from_runner
from posrat.system import bootstrap_admin_from_env
from posrat.system.admin_view import ADMIN_ROUTE, render_admin
from posrat.system.login_view import (
    LOGIN_ROUTE,
    logout_current_user,
    render_login,
    require_auth,
)

_LOGGER = logging.getLogger(__name__)


APP_TITLE = "POSRAT"
DESIGNER_ROUTE = "/designer"
RUNNER_ROUTE = "/runner"

#: URL prefix under which the NiceGUI media-files route serves the
#: ``data/assets/<exam_id>/<filename>`` attachments. Wired in
#: :func:`main` via :func:`nicegui.app.add_media_files`, so the
#: Designer Editor can render question images with
#: ``ui.image(f"{ASSETS_URL_PREFIX}/<exam_id>/<filename>")`` without
#: having to base64-encode the bytes or expose the filesystem directly.
#: Kept as a module constant so the Designer helpers can build URLs
#: from ``Question.image_path`` consistently.
ASSETS_URL_PREFIX = "/media/assets"


#: Key used inside ``app.storage.user`` to persist the dark/light preference
#: across page visits and server restarts for the same browser / user.
STORAGE_KEY_DARK_MODE = "dark_mode"

#: Environment variable overriding the storage secret. Useful for tests and
#: for users who want to pin a stable secret across restarts; otherwise we
#: generate a random one at startup (storage is then per-process only).
STORAGE_SECRET_ENV = "POSRAT_STORAGE_SECRET"

#: Environment variable that suppresses the automatic browser tab that
#: NiceGUI opens on server start. Set to ``1``/``true``/``yes`` to keep
#: the server headless — useful for dev workflows where a browser tab
#: is already open or for running under a process manager. The HTTP
#: server itself still listens on port 8080; the flag only affects the
#: ``show`` argument passed to :func:`ui.run`.
NO_BROWSER_ENV = "POSRAT_NO_BROWSER"

#: Values that :func:`_resolve_show_browser` treats as "don't open the
#: browser". Everything else — including unset / empty string — means
#: "keep NiceGUI's default auto-open behaviour". Case-insensitive.
_FALSY_NO_BROWSER_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _resolve_show_browser() -> bool:
    """Return ``False`` when :data:`NO_BROWSER_ENV` opts out of auto-open.

    The default is ``True`` (match upstream NiceGUI behaviour: open a
    browser tab at startup) so existing users who never set the env var
    see no change. An explicit falsy value in the env var flips the
    behaviour without requiring a CLI flag — keeps ``python -m posrat``
    a zero-argument entry point.
    """

    raw = os.environ.get(NO_BROWSER_ENV, "")
    return raw.strip().lower() not in _FALSY_NO_BROWSER_VALUES



def _navigate_to_designer() -> None:
    """Switch the current tab to the Designer route."""

    ui.navigate.to(DESIGNER_ROUTE)


def _navigate_to_runner() -> None:
    """Switch the current tab to the Runner route."""

    ui.navigate.to(RUNNER_ROUTE)


def _show_about_dialog() -> None:
    """Show a small About dialog describing the current version."""

    with ui.dialog() as dialog, ui.card():
        ui.label(APP_TITLE).classes("text-h5")
        ui.label("Personal Online Study, Review & Assessment Tool.")
        ui.label(f"Version: {__version__}").classes("text-caption")
        ui.button("Close", on_click=dialog.close)
    dialog.open()


def _resolve_storage_secret() -> str:
    """Return the secret used to sign the persistent user-storage cookie.

    Prefers the ``POSRAT_STORAGE_SECRET`` environment variable so the user can
    pin a stable value (then the stored preferences survive restarts). Falls
    back to a freshly generated random secret — in that case the persistence
    scope is just "per running server process", which is still enough to
    keep the dark/light toggle consistent while the app is up.
    """

    secret = os.environ.get(STORAGE_SECRET_ENV)
    if secret:
        return secret
    return secrets.token_urlsafe(32)


def _render_header(
    current_user_display: Optional[str] = None,
    *,
    show_admin_link: bool = False,
) -> None:
    """Render the shared top navigation menu.

    Kept standalone so every page registered via :func:`ui.page` reuses the
    same look. Menu entries navigate with :func:`ui.navigate.to` so the
    browser URL reflects the selected section. The dark-mode switch is bound
    to ``app.storage.user`` so the preference sticks across pages and tab
    reloads for the current user.

    Phase 10 adds:

    - A "Signed in: <display_name>" caption + "Log out" button when
      ``current_user_display`` is provided. The caption stays hidden
      on public routes (``/login``) where the caller passes ``None``.
    - A Designer button visibility rule belongs to individual route
      guards, not here — the shared header stays uniform across
      sections.

    Phase 10.17 hides the Designer/Runner/Admin buttons, the dark-mode
    switch and the About button for unauthenticated visitors
    (``current_user_display`` is falsy). The rationale: on the public
    ``/login`` route nobody should learn anything about the system
    beyond the product name and the login form — no hints that a
    Designer exists, no theming chrome, no About dialog exposing the
    version. Authenticated users get the full top bar as before.
    """

    authenticated = bool(current_user_display)
    dark = ui.dark_mode().bind_value(app.storage.user, STORAGE_KEY_DARK_MODE)
    with ui.header().classes("items-center"):
        ui.label(APP_TITLE).classes("text-h6 q-mr-md")
        if authenticated:
            ui.button("Designer", on_click=_navigate_to_designer).props(
                "flat color=white"
            )
            ui.button("Runner", on_click=_navigate_to_runner).props(
                "flat color=white"
            )
            if show_admin_link:
                ui.button(
                    "Admin",
                    on_click=lambda: ui.navigate.to(ADMIN_ROUTE),
                ).props("flat color=white")
        ui.space()
        if authenticated:
            ui.label(f"Signed in: {current_user_display}").classes(
                "text-caption q-mr-sm"
            )
            ui.button("Log out", on_click=logout_current_user).props(
                "flat color=white"
            )
            ui.switch("Dark mode").bind_value(dark, "value").props(
                "color=white"
            )
            ui.button("About", on_click=_show_about_dialog).props(
                "flat color=white"
            )


def _render_home() -> None:
    """Render the placeholder content of the root page (``/``)."""

    ui.label(APP_TITLE).classes("text-h4")
    ui.label(f"Personal Online Study, Review & Assessment Tool — v{__version__}")
    ui.label(
        "Choose a section. Designer is used to create and edit exams, "
        "Runner runs a training or exam session."
    ).classes("text-caption q-mt-md")
    with ui.row().classes("q-mt-md"):
        ui.button("Open Designer", on_click=_navigate_to_designer).props("color=primary")
        ui.button("Start Runner", on_click=_navigate_to_runner).props("color=secondary")


def _render_designer() -> None:
    """Render the Designer body by delegating to :mod:`posrat.designer`.

    Kept as a thin wrapper so the ``/designer`` page handler stays uniform
    with ``/`` and ``/runner`` and so existing smoke tests keep working.
    """

    render_designer()


def _render_runner() -> None:
    """Render the Runner body by delegating to :mod:`posrat.runner`.

    Kept as a thin wrapper so the ``/runner`` page handler stays uniform
    with ``/`` and ``/designer``. The real UI (picker, mode dialog,
    question view, results screen) lives under
    :mod:`posrat.runner.page`. Phase 7 wired the real implementation in
    place of the 3.3 placeholder.
    """

    _render_runner_body_from_runner()



def _header_kwargs(user) -> dict:
    """Build the keyword args shared by every authenticated page."""

    return {
        "current_user_display": user.effective_display_name,
        "show_admin_link": bool(user.is_admin),
    }


@ui.page("/")
def _home_page() -> None:
    """Root route handler."""

    user = require_auth()
    if user is None:
        return
    _render_header(**_header_kwargs(user))
    _render_home()


@ui.page(DESIGNER_ROUTE)
def _designer_page() -> None:
    """Designer route handler.

    Phase 10 gates the Designer behind the ``can_use_designer`` role
    flag so casual Runner users don't accidentally wander in. Users
    without the flag get a friendly "permission denied" caption rather
    than a flat 403 — matches the vibe of the Runner picker's
    "Request access" card.
    """

    user = require_auth()
    if user is None:
        return
    _render_header(**_header_kwargs(user))
    if not user.can_use_designer:
        ui.label("You don't have Designer access.").classes(
            "text-h5 text-negative q-mt-lg"
        )
        ui.label(
            "Ask an administrator to grant the 'Designer' role."
        ).classes("text-caption q-mt-sm")
        return
    _render_designer()


@ui.page(RUNNER_ROUTE)
def _runner_page() -> None:
    """Runner route handler."""

    user = require_auth()
    if user is None:
        return
    _render_header(**_header_kwargs(user))
    _render_runner()


@ui.page(ADMIN_ROUTE)
def _admin_page() -> None:
    """Admin panel route — gated by ``is_admin``.

    Phase 10 step 10.10/10.11/10.12/10.13 in one: three tabs
    (users, exams, pending requests) rendered by
    :func:`posrat.system.admin_view.render_admin`.
    """

    user = require_auth()
    if user is None:
        return
    _render_header(**_header_kwargs(user))
    if not user.is_admin:
        ui.label("The admin panel requires the Admin role.").classes(
            "text-h5 text-negative q-mt-lg"
        )
        return
    render_admin(user)


@ui.page(LOGIN_ROUTE)
def _login_page() -> None:
    """Login form route.

    Renders the header *without* the login-specific caption (the user
    is not authenticated yet, by definition) and hands off to the
    form body. Stays publicly accessible — guard would cause a
    redirect loop.
    """

    _render_header()
    render_login()


def _register_assets_route() -> None:
    """Serve ``data/assets/<exam_id>/<filename>`` under :data:`ASSETS_URL_PREFIX`.

    NiceGUI 3 does not infer that ``ui.image("/absolute/fs/path.png")``
    should stream a local file over HTTP, so we register an explicit
    ``add_media_files`` route that maps the URL prefix onto the
    resolved assets directory. The directory itself is created on
    demand by :func:`resolve_assets_dir`, which also guarantees
    ``data/assets/`` exists before the route handler tries to stat
    anything inside.

    Idempotent: called once at :func:`main` before :func:`ui.run`.
    Tests that import :mod:`posrat.app` without running the server
    must not trigger it (otherwise repeated test-collection would
    stack duplicate routes), hence the separate helper.
    """

    assets_dir = resolve_assets_dir(resolve_data_dir())
    # ``add_static_files`` serves raw file contents with a sensible default
    # cache-control; ``add_media_files`` is geared towards streaming
    # (range requests, long-lived videos). For PNG/JPG/SVG thumbnails
    # the static variant loads faster and avoids a browser console
    # error seen with ``add_media_files`` on NiceGUI 3.10.
    app.add_static_files(ASSETS_URL_PREFIX, str(assets_dir))



def main() -> int:
    """Launch the NiceGUI application in a browser tab.

    Returns ``0`` after ``ui.run`` exits. ``ui.run`` blocks, so in practice
    this function only returns when the user stops the server.

    Registers the ``/media/assets`` route before ``ui.run`` so question
    images attached in the Designer (see :mod:`posrat.designer.editor`)
    are reachable via stable HTTP URLs — required because NiceGUI 3
    does not auto-serve absolute filesystem paths through
    :func:`ui.image`.

    Respects :data:`NO_BROWSER_ENV` (``POSRAT_NO_BROWSER=1``) to keep the
    server headless — useful when a browser tab is already open from a
    previous session or when the app runs under a process manager.
    """

    _register_assets_route()
    _bootstrap_admin()
    ui.run(
        title=APP_TITLE,
        reload=False,
        storage_secret=_resolve_storage_secret(),
        show=_resolve_show_browser(),
    )
    return 0


def _bootstrap_admin() -> None:
    """Provision the first admin account from env vars when applicable.

    Runs inside :func:`main` right before ``ui.run`` so the outcome is
    visible in the startup banner. Phase 10 deliberately refuses to
    give unauthenticated users a dev bypass — the resulting WARNING on
    ``env_missing`` signals to the operator that no admin is able to
    reach ``/admin`` yet. See :mod:`posrat.system.bootstrap` for the
    full decision tree.

    Any unexpected exception is caught and logged as a warning rather
    than aborting the server — a misbehaving bootstrap must never
    prevent legacy deployments (which have their own admin rows) from
    starting up.
    """

    data_dir = Path(resolve_data_dir())
    try:
        result = bootstrap_admin_from_env(data_dir)
    except Exception as exc:  # noqa: BLE001 — surface everything as warning
        _LOGGER.warning(
            "admin bootstrap crashed: %s — continuing without it", exc
        )
        return

    if result.action == "created":
        _LOGGER.info(result.message)
    elif result.action in ("env_missing", "invalid"):
        _LOGGER.warning(result.message)
    else:
        # "skipped" — at least one admin exists, no banner needed.
        _LOGGER.debug(result.message)


if __name__ == "__main__":  # pragma: no cover - manual launch path
    raise SystemExit(main())
