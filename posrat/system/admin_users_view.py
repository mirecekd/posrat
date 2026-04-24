"""Admin → **Users** tab.

Extracted from the original monolithic ``admin_view.py`` as part of
the Phase 10 stretch refactor. Responsibility is limited to the users
tab of the ``/admin`` panel:

- list users with role flags and last-login timestamp,
- create new internal users,
- toggle admin / designer flags,
- reset an internal user's password,
- delete a user.

Safety invariants (cannot self-delete, cannot demote / delete the last
admin) live here in the UI layer — the DAO deliberately stays dumb.
"""

from __future__ import annotations

from typing import Optional

from nicegui import ui

from posrat.models import User
from posrat.system.admin_common import open_admin_system_db
from posrat.system.auth import hash_password
from posrat.system.users_repo import (
    count_admins,
    create_user,
    delete_user,
    list_users,
    update_user_password,
    update_user_roles,
)


@ui.refreshable
def render_users_tab(current_admin: User) -> None:
    """Render the Users tab body (refreshable after each mutation)."""

    db = open_admin_system_db()
    try:
        users = list_users(db)
        admin_total = count_admins(db)
    finally:
        db.close()

    ui.label("Users").classes("text-h6 q-mt-md")
    with ui.row().classes("q-mb-sm"):
        ui.button(
            "Add user",
            on_click=lambda: _open_create_user_dialog(current_admin),
        ).props("color=primary")

    if not users:
        ui.label("No users yet.").classes("q-mt-md")
        return

    with ui.column().classes("q-gutter-sm w-full"):
        for user in users:
            _render_user_row(user, current_admin, admin_total)


def _render_user_row(
    user: User, current_admin: User, admin_total: int
) -> None:
    """One card per user with inline action buttons."""

    is_self = user.username == current_admin.username
    last_admin = user.is_admin and admin_total <= 1

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center q-gutter-md w-full"):
            with ui.column().classes("col-grow"):
                ui.label(user.effective_display_name).classes("text-subtitle1")
                ui.label(
                    f"{user.username} · {user.auth_source}"
                    + (" · admin" if user.is_admin else "")
                    + (" · designer" if user.can_use_designer else "")
                ).classes("text-caption text-grey-7")
                if user.last_login_at:
                    ui.label(f"Last login: {user.last_login_at}").classes(
                        "text-caption text-grey"
                    )

            admin_toggle = ui.switch(
                "Admin", value=user.is_admin
            ).props("dense")
            admin_toggle.on(
                "update:model-value",
                lambda _evt=None, u=user: _toggle_roles(
                    u, admin=admin_toggle.value, designer=None
                ),
            )
            if is_self or last_admin:
                admin_toggle.props("disable")

            designer_toggle = ui.switch(
                "Designer", value=user.can_use_designer
            ).props("dense")
            designer_toggle.on(
                "update:model-value",
                lambda _evt=None, u=user: _toggle_roles(
                    u, admin=None, designer=designer_toggle.value
                ),
            )

            if user.auth_source == "internal":
                ui.button(
                    "Reset password",
                    on_click=lambda _evt=None, u=user: _open_reset_password_dialog(u),
                ).props("flat size=sm")

            delete_btn = ui.button(
                "Delete",
                on_click=lambda _evt=None, u=user: _confirm_delete_user(
                    u, current_admin
                ),
            ).props("flat size=sm color=negative")
            if is_self or last_admin:
                delete_btn.props("disable")


def _toggle_roles(
    user: User, *, admin: Optional[bool], designer: Optional[bool]
) -> None:
    """Flip ``is_admin`` / ``can_use_designer`` via the DAO and refresh."""

    new_admin = user.is_admin if admin is None else admin
    new_designer = (
        user.can_use_designer if designer is None else designer
    )

    db = open_admin_system_db()
    try:
        # Safety: never let the UI demote the last admin.
        if user.is_admin and not new_admin and count_admins(db) <= 1:
            ui.notify(
                "Cannot remove Admin role from the last admin.",
                type="negative",
            )
            render_users_tab.refresh()
            return
        update_user_roles(
            db,
            user.username,
            is_admin=new_admin,
            can_use_designer=new_designer,
        )
    finally:
        db.close()
    ui.notify(f"Roles for user {user.username!r} updated.")
    render_users_tab.refresh()


def _open_create_user_dialog(current_admin: User) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label("New user").classes("text-h6")
        username_input = ui.input("Username").props("autofocus").classes(
            "w-full"
        )
        display_input = ui.input("Display name (optional)").classes("w-full")
        password_input = (
            ui.input("Password", password=True, password_toggle_button=True)
            .classes("w-full")
        )
        is_admin_chk = ui.checkbox("Admin", value=False)
        can_designer_chk = ui.checkbox("Designer", value=False)

        def _on_create() -> None:
            username = (username_input.value or "").strip()
            password = password_input.value or ""
            display_name = (display_input.value or "").strip() or None
            if not username or not password:
                ui.notify("Enter username and password.", type="negative")
                return
            db = open_admin_system_db()
            try:
                try:
                    create_user(
                        db,
                        username=username,
                        auth_source="internal",
                        password_hash=hash_password(password),
                        display_name=display_name,
                        is_admin=is_admin_chk.value,
                        can_use_designer=can_designer_chk.value,
                    )
                except Exception as exc:  # noqa: BLE001 — surface anything
                    ui.notify(
                        f"Cannot create: {exc}", type="negative"
                    )
                    return
            finally:
                db.close()
            ui.notify(f"User {username!r} created.")
            dialog.close()
            render_users_tab.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Create", on_click=_on_create).props(
                "color=primary"
            )

    dialog.open()


def _open_reset_password_dialog(user: User) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label(f"Reset password — {user.username}").classes("text-h6")
        password_input = (
            ui.input(
                "New password",
                password=True,
                password_toggle_button=True,
            )
            .props("autofocus")
            .classes("w-full")
        )

        def _on_save() -> None:
            password = password_input.value or ""
            if not password:
                ui.notify("Password cannot be empty.", type="negative")
                return
            db = open_admin_system_db()
            try:
                try:
                    update_user_password(
                        db, user.username, hash_password(password)
                    )
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"Cannot set password: {exc}", type="negative"
                    )
                    return
            finally:
                db.close()
            ui.notify(f"Password for user {user.username!r} updated.")
            dialog.close()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=_on_save).props("color=primary")

    dialog.open()


def _confirm_delete_user(user: User, current_admin: User) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full"):
        ui.label(f"Delete user {user.username}?").classes("text-h6")
        ui.label(
            "All access grants and requests are removed as well"
            " (CASCADE). This action cannot be undone."
        ).classes("text-caption text-grey")

        def _do_delete() -> None:
            db = open_admin_system_db()
            try:
                if user.username == current_admin.username:
                    ui.notify(
                        "You cannot delete yourself.", type="negative"
                    )
                    return
                if user.is_admin and count_admins(db) <= 1:
                    ui.notify(
                        "Cannot delete the last admin.", type="negative"
                    )
                    return
                delete_user(db, user.username)
            finally:
                db.close()
            ui.notify(f"User {user.username!r} deleted.")
            dialog.close()
            render_users_tab.refresh()

        with ui.row().classes("justify-end q-gutter-sm q-mt-md"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=_do_delete).props(
                "color=negative"
            )

    dialog.open()


__all__ = ["render_users_tab"]
