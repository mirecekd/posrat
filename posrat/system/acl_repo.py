"""DAO for per-exam access control lists.

Two tables live in ``system.sqlite`` from migration v3:

* ``user_exam_access`` — who has been granted access to which exam.
  Used by :func:`list_accessible_exam_ids` in Phase 10.9 to filter the
  Runner picker.
* ``exam_access_requests`` — pending (or already decided) requests
  the candidate has filed. The admin panel (10.13) displays the
  pending queue and flips the status.

These helpers are deliberately free of NiceGUI imports — they are
plain pytest-exercisable SQL wrappers. Policy (e.g. "cannot reject
your own request") lives in the UI layer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional


#: Status of an :class:`ExamAccessRequest`. Mirrors the CHECK constraint
#: on ``exam_access_requests.status``.
AccessRequestStatus = Literal["pending", "approved", "rejected"]


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class ExamAccessGrant:
    """A single row of ``user_exam_access``.

    Attributes:
        username: Who has access.
        exam_id: Which exam (matches ``Exam.id``).
        granted_at: When the admin (or the system auto-grant) flipped
            the bit. ISO-8601 UTC.
        is_paid: Placeholder for the future per-exam paywall.
            ``False`` for every row inserted today; the admin UI
            (10.11) ignores it.
    """

    username: str
    exam_id: str
    granted_at: str
    is_paid: bool


@dataclass(frozen=True)
class ExamAccessRequest:
    """A single row of ``exam_access_requests``.

    Attributes:
        username: The candidate who filed the request.
        exam_id: Which exam they want access to.
        requested_at: ISO-8601 UTC of the ``POST``.
        status: ``pending`` / ``approved`` / ``rejected``.
        decided_at: ISO-8601 UTC when the admin flipped the status,
            or ``None`` while ``status == "pending"``.
        decided_by: Username of the admin who made the decision, or
            ``None`` for pending requests. Captured as plain text so
            deleting the admin account later does not orphan the
            audit trail.
    """

    username: str
    exam_id: str
    requested_at: str
    status: AccessRequestStatus
    decided_at: Optional[str]
    decided_by: Optional[str]


def _row_to_grant(row: sqlite3.Row) -> ExamAccessGrant:
    return ExamAccessGrant(
        username=row["username"],
        exam_id=row["exam_id"],
        granted_at=row["granted_at"],
        is_paid=bool(row["is_paid"]),
    )


def _row_to_request(row: sqlite3.Row) -> ExamAccessRequest:
    return ExamAccessRequest(
        username=row["username"],
        exam_id=row["exam_id"],
        requested_at=row["requested_at"],
        status=row["status"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
    )


# -------------------- access grants --------------------


def grant_exam_access(
    db: sqlite3.Connection,
    *,
    username: str,
    exam_id: str,
    granted_at: Optional[str] = None,
    is_paid: bool = False,
) -> ExamAccessGrant:
    """Insert or refresh a ``user_exam_access`` row.

    Idempotent: re-granting access to an already-approved user is a
    no-op that bumps ``granted_at`` / ``is_paid`` to the latest
    values. Raises :class:`sqlite3.IntegrityError` if the username is
    not present in ``users`` (FK guard).
    """

    stamp = granted_at or _utc_now_iso()
    with db:
        db.execute(
            "INSERT INTO user_exam_access"
            " (username, exam_id, granted_at, is_paid)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(username, exam_id) DO UPDATE SET"
            "   granted_at = excluded.granted_at,"
            "   is_paid = excluded.is_paid",
            (username, exam_id, stamp, 1 if is_paid else 0),
        )
    return ExamAccessGrant(
        username=username,
        exam_id=exam_id,
        granted_at=stamp,
        is_paid=is_paid,
    )


def revoke_exam_access(
    db: sqlite3.Connection, *, username: str, exam_id: str
) -> bool:
    """Drop the ``user_exam_access`` row. ``True`` when a row was removed.

    Admin panel surface for "revoke access" button; idempotent when
    the grant never existed.
    """

    cursor = db.execute(
        "DELETE FROM user_exam_access"
        " WHERE username = ? AND exam_id = ?",
        (username, exam_id),
    )
    db.commit()
    return cursor.rowcount > 0


def has_exam_access(
    db: sqlite3.Connection, *, username: str, exam_id: str
) -> bool:
    """Boolean shortcut used by the Runner picker's "start" button.

    Kept as a dedicated helper (rather than inlining a SELECT) so
    call sites stay readable and tests don't need to assert against
    a list length.
    """

    row = db.execute(
        "SELECT 1 FROM user_exam_access"
        " WHERE username = ? AND exam_id = ?",
        (username, exam_id),
    ).fetchone()
    return row is not None


def list_accessible_exam_ids(
    db: sqlite3.Connection, *, username: str
) -> list[str]:
    """Return every ``exam_id`` the user currently has access to.

    Ordered alphabetically so the Runner picker's sort stays stable
    between restarts. Empty list when the user is new / has not been
    granted access to anything.
    """

    rows = db.execute(
        "SELECT exam_id FROM user_exam_access"
        " WHERE username = ? ORDER BY exam_id ASC",
        (username,),
    ).fetchall()
    return [row["exam_id"] for row in rows]


def list_grants_for_exam(
    db: sqlite3.Connection, exam_id: str
) -> list[ExamAccessGrant]:
    """Admin view: every user with access to ``exam_id``."""

    rows = db.execute(
        "SELECT username, exam_id, granted_at, is_paid"
        " FROM user_exam_access WHERE exam_id = ?"
        " ORDER BY username ASC",
        (exam_id,),
    ).fetchall()
    return [_row_to_grant(row) for row in rows]


def purge_acl_for_exam(db: sqlite3.Connection, exam_id: str) -> int:
    """Drop every ACL row (grants + requests) for ``exam_id``.

    Called from :mod:`posrat.system.admin_exams` (step 10.12) when
    the admin deletes the source ``.sqlite`` file — the ACL tables
    cannot cascade automatically because exams live in per-exam DBs
    rather than the system DB.

    Returns the total number of rows deleted across both tables.
    """

    with db:
        grants = db.execute(
            "DELETE FROM user_exam_access WHERE exam_id = ?",
            (exam_id,),
        )
        requests = db.execute(
            "DELETE FROM exam_access_requests WHERE exam_id = ?",
            (exam_id,),
        )
    return (grants.rowcount or 0) + (requests.rowcount or 0)


# -------------------- access requests --------------------


def request_exam_access(
    db: sqlite3.Connection,
    *,
    username: str,
    exam_id: str,
    requested_at: Optional[str] = None,
) -> ExamAccessRequest:
    """File a fresh pending request for ``(username, exam_id)``.

    Idempotent for pending requests (re-submits just refresh the
    ``requested_at`` stamp). When the previous request was
    ``approved`` / ``rejected`` the status flips back to
    ``pending`` — the candidate is explicitly asking again, admin
    can re-decide.

    Raises :class:`ValueError` if the user already has an active
    grant in ``user_exam_access``; requesting access you already
    have is almost always a UI bug worth surfacing.
    """

    if has_exam_access(db, username=username, exam_id=exam_id):
        raise ValueError(
            f"user {username!r} already has access to {exam_id!r};"
            " revoke first if you want to reset the request flow"
        )

    stamp = requested_at or _utc_now_iso()
    with db:
        db.execute(
            "INSERT INTO exam_access_requests"
            " (username, exam_id, requested_at, status,"
            "  decided_at, decided_by)"
            " VALUES (?, ?, ?, 'pending', NULL, NULL)"
            " ON CONFLICT(username, exam_id) DO UPDATE SET"
            "   requested_at = excluded.requested_at,"
            "   status = 'pending',"
            "   decided_at = NULL,"
            "   decided_by = NULL",
            (username, exam_id, stamp),
        )
    return ExamAccessRequest(
        username=username,
        exam_id=exam_id,
        requested_at=stamp,
        status="pending",
        decided_at=None,
        decided_by=None,
    )


def _decide_request(
    db: sqlite3.Connection,
    *,
    username: str,
    exam_id: str,
    decision: AccessRequestStatus,
    decided_by: str,
    decided_at: Optional[str] = None,
) -> bool:
    """Flip a pending request's status to ``approved`` / ``rejected``.

    Returns ``True`` when a row was actually transitioned, ``False``
    when no pending row was found (already decided or never existed).
    """

    stamp = decided_at or _utc_now_iso()
    cursor = db.execute(
        "UPDATE exam_access_requests"
        " SET status = ?, decided_at = ?, decided_by = ?"
        " WHERE username = ? AND exam_id = ? AND status = 'pending'",
        (decision, stamp, decided_by, username, exam_id),
    )
    db.commit()
    return cursor.rowcount > 0


def approve_access_request(
    db: sqlite3.Connection,
    *,
    username: str,
    exam_id: str,
    approved_by: str,
    decided_at: Optional[str] = None,
) -> bool:
    """Approve a pending request and grant the associated access.

    Composite operation: flips the request row to ``approved`` and
    inserts the matching ``user_exam_access`` grant in the same
    transaction. Already-approved / rejected / missing requests
    return ``False`` without side effects.
    """

    stamp = decided_at or _utc_now_iso()
    with db:
        cursor = db.execute(
            "UPDATE exam_access_requests"
            " SET status = 'approved', decided_at = ?, decided_by = ?"
            " WHERE username = ? AND exam_id = ? AND status = 'pending'",
            (stamp, approved_by, username, exam_id),
        )
        if cursor.rowcount == 0:
            return False
        db.execute(
            "INSERT INTO user_exam_access"
            " (username, exam_id, granted_at, is_paid)"
            " VALUES (?, ?, ?, 0)"
            " ON CONFLICT(username, exam_id) DO UPDATE SET"
            "   granted_at = excluded.granted_at",
            (username, exam_id, stamp),
        )
    return True


def reject_access_request(
    db: sqlite3.Connection,
    *,
    username: str,
    exam_id: str,
    rejected_by: str,
    decided_at: Optional[str] = None,
) -> bool:
    """Mark a pending request as ``rejected``.

    Does not touch ``user_exam_access``. Returns ``False`` when the
    request was already decided or never existed.
    """

    return _decide_request(
        db,
        username=username,
        exam_id=exam_id,
        decision="rejected",
        decided_by=rejected_by,
        decided_at=decided_at,
    )


def get_access_request(
    db: sqlite3.Connection, *, username: str, exam_id: str
) -> Optional[ExamAccessRequest]:
    """Return the latest request row (any status) or ``None``."""

    row = db.execute(
        "SELECT username, exam_id, requested_at, status,"
        " decided_at, decided_by"
        " FROM exam_access_requests"
        " WHERE username = ? AND exam_id = ?",
        (username, exam_id),
    ).fetchone()
    return _row_to_request(row) if row is not None else None


def list_pending_requests(
    db: sqlite3.Connection,
) -> list[ExamAccessRequest]:
    """All pending requests, oldest first.

    Admin panel view. Ordering by ``requested_at`` means admins see
    the longest-waiting candidates up top.
    """

    rows = db.execute(
        "SELECT username, exam_id, requested_at, status,"
        " decided_at, decided_by"
        " FROM exam_access_requests"
        " WHERE status = 'pending'"
        " ORDER BY requested_at ASC, username ASC",
        (),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def list_requests_for_user(
    db: sqlite3.Connection, username: str
) -> list[ExamAccessRequest]:
    """All requests (any status) filed by ``username``.

    Runner picker uses this to label disabled cards as "Requested"
    without a per-exam ``get_access_request`` lookup.
    """

    rows = db.execute(
        "SELECT username, exam_id, requested_at, status,"
        " decided_at, decided_by"
        " FROM exam_access_requests"
        " WHERE username = ?"
        " ORDER BY requested_at DESC",
        (username,),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


__all__ = [
    "AccessRequestStatus",
    "ExamAccessGrant",
    "ExamAccessRequest",
    "approve_access_request",
    "get_access_request",
    "grant_exam_access",
    "has_exam_access",
    "list_accessible_exam_ids",
    "list_grants_for_exam",
    "list_pending_requests",
    "list_requests_for_user",
    "purge_acl_for_exam",
    "reject_access_request",
    "request_exam_access",
    "revoke_exam_access",
]
