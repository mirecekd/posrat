"""Admin panel orchestrator — ``/admin`` route + three-tab layout.

Accessible at ``/admin`` for signed-in users with ``is_admin=True``.
Each tab's body lives in its own module so that no single file grows
past the Baby Steps™ 8 KB soft-limit:

- :mod:`posrat.system.admin_users_view` — users tab body,
- :mod:`posrat.system.admin_exams_view` — exams tab body,
- :mod:`posrat.system.admin_requests_view` — pending access requests.

This module is responsible for nothing more than wiring the three
renderers into a :class:`ui.tabs` / :class:`ui.tab_panels` pair plus
re-exporting the route constant and page entry point.

The admin-only gate (``user.is_admin``) is owned by the page layer in
:mod:`posrat.app` — :func:`render_admin` trusts its caller.
"""

from __future__ import annotations

from nicegui import ui

from posrat.models import User
from posrat.system.admin_exams_view import render_exams_tab
from posrat.system.admin_requests_view import render_requests_tab
from posrat.system.admin_users_view import render_users_tab


ADMIN_ROUTE = "/admin"


def render_admin(current_admin: User) -> None:
    """Render the ``/admin`` body — three :class:`ui.tab` s.

    Expected to be called *after* the auth guard has confirmed the
    caller holds ``is_admin``. The page layer (``posrat.app``) owns
    the gate.
    """

    ui.label("Administration").classes("text-h4")
    with ui.tabs() as tabs:
        users_tab = ui.tab("Users")
        exams_tab = ui.tab("Exams")
        requests_tab = ui.tab("Access requests")
    with ui.tab_panels(tabs, value=users_tab).classes("w-full"):
        with ui.tab_panel(users_tab):
            render_users_tab(current_admin)
        with ui.tab_panel(exams_tab):
            render_exams_tab()
        with ui.tab_panel(requests_tab):
            render_requests_tab(current_admin)


__all__ = [
    "ADMIN_ROUTE",
    "render_admin",
]
