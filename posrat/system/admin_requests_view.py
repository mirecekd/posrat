"""Admin → **Access requests** tab.

Extracted from the original monolithic ``admin_view.py`` as part of
the Phase 10 stretch refactor. Responsibility is the pending
:class:`ExamAccessRequest` queue:

- list all ``status='pending'`` rows,
- Approve → flip to approved + insert matching :class:`ExamAccessGrant`
  via :func:`approve_access_request`,
- Reject → flip to rejected via :func:`reject_access_request`.

The approve / reject atomicity (flip + grant in one transaction for
approvals) lives in :mod:`posrat.system.acl_repo`. This view is pure
UI choreography around it.
"""

from __future__ import annotations

from nicegui import ui

from posrat.models import User
from posrat.system.acl_repo import (
    ExamAccessRequest,
    approve_access_request,
    list_pending_requests,
    reject_access_request,
)
from posrat.system.admin_common import open_admin_system_db


@ui.refreshable
def render_requests_tab(current_admin: User) -> None:
    ui.label("Access requests").classes("text-h6 q-mt-md")

    db = open_admin_system_db()
    try:
        pending = list_pending_requests(db)
    finally:
        db.close()

    if not pending:
        ui.label("No pending requests.").classes("q-mt-md")
        return

    with ui.column().classes("q-gutter-sm w-full"):
        for req in pending:
            _render_request_row(req, current_admin)


def _render_request_row(
    req: ExamAccessRequest, current_admin: User
) -> None:
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center q-gutter-md w-full"):
            with ui.column().classes("col-grow"):
                ui.label(f"{req.username} → {req.exam_id}").classes(
                    "text-subtitle1"
                )
                ui.label(f"Requested: {req.requested_at}").classes(
                    "text-caption text-grey-7"
                )
            ui.button(
                "Approve",
                on_click=lambda _evt=None, r=req: _handle_decide(
                    r, approve=True, admin=current_admin
                ),
            ).props("color=positive size=sm")
            ui.button(
                "Reject",
                on_click=lambda _evt=None, r=req: _handle_decide(
                    r, approve=False, admin=current_admin
                ),
            ).props("color=negative size=sm flat")


def _handle_decide(
    req: ExamAccessRequest, *, approve: bool, admin: User
) -> None:
    db = open_admin_system_db()
    try:
        if approve:
            approve_access_request(
                db,
                username=req.username,
                exam_id=req.exam_id,
                approved_by=admin.username,
            )
            ui.notify(
                f"Approved: {req.username} → {req.exam_id}"
            )
        else:
            reject_access_request(
                db,
                username=req.username,
                exam_id=req.exam_id,
                rejected_by=admin.username,
            )
            ui.notify(
                f"Rejected: {req.username} → {req.exam_id}"
            )
    finally:
        db.close()
    render_requests_tab.refresh()


__all__ = ["render_requests_tab"]
