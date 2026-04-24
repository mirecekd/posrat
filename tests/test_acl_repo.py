"""Tests for :mod:`posrat.system.acl_repo`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from posrat.system import (
    CURRENT_SYSTEM_SCHEMA_VERSION,
    SYSTEM_DB_FILENAME,
    approve_access_request,
    create_user,
    get_access_request,
    grant_exam_access,
    has_exam_access,
    hash_password,
    list_accessible_exam_ids,
    list_grants_for_exam,
    list_pending_requests,
    list_requests_for_user,
    open_system_db,
    purge_acl_for_exam,
    reject_access_request,
    request_exam_access,
    revoke_exam_access,
)


FAST_ROUNDS = 4


def _db(tmp_path: Path) -> sqlite3.Connection:
    db = open_system_db(tmp_path / SYSTEM_DB_FILENAME)
    create_user(
        db,
        username="alice",
        auth_source="internal",
        password_hash=hash_password("pw", rounds=FAST_ROUNDS),
    )
    create_user(
        db,
        username="bob",
        auth_source="internal",
        password_hash=hash_password("pw", rounds=FAST_ROUNDS),
    )
    create_user(
        db,
        username="admin",
        auth_source="internal",
        password_hash=hash_password("pw", rounds=FAST_ROUNDS),
        is_admin=True,
    )
    return db


def test_migration_v3_adds_acl_tables(tmp_path: Path) -> None:
    """Step 10.8 migration sets schema_version to at least 3."""

    db = _db(tmp_path)
    try:
        version = db.execute(
            "SELECT version FROM schema_version"
        ).fetchone()["version"]
        assert version == CURRENT_SYSTEM_SCHEMA_VERSION
        tables = [
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        assert "user_exam_access" in tables
        assert "exam_access_requests" in tables
    finally:
        db.close()


# ---------- grants ----------


def test_grant_exam_access_round_trip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        grant = grant_exam_access(
            db,
            username="alice",
            exam_id="aws-saa-c03",
            granted_at="2026-04-23T15:00:00Z",
        )
        assert grant.username == "alice"
        assert grant.exam_id == "aws-saa-c03"
        assert grant.granted_at == "2026-04-23T15:00:00Z"
        assert grant.is_paid is False
        assert has_exam_access(
            db, username="alice", exam_id="aws-saa-c03"
        )
    finally:
        db.close()


def test_grant_exam_access_is_idempotent(tmp_path: Path) -> None:
    """Re-granting updates granted_at / is_paid without duplicating rows."""

    db = _db(tmp_path)
    try:
        grant_exam_access(
            db,
            username="alice",
            exam_id="aws",
            granted_at="2026-04-23T10:00:00Z",
        )
        grant_exam_access(
            db,
            username="alice",
            exam_id="aws",
            granted_at="2026-04-23T12:00:00Z",
            is_paid=True,
        )
        rows = list_grants_for_exam(db, "aws")
        assert len(rows) == 1
        assert rows[0].granted_at == "2026-04-23T12:00:00Z"
        assert rows[0].is_paid is True
    finally:
        db.close()


def test_grant_exam_access_rejects_unknown_user(tmp_path: Path) -> None:
    """FK guard: granting access to a non-existent user → IntegrityError."""

    db = _db(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            grant_exam_access(
                db, username="ghost", exam_id="aws"
            )
    finally:
        db.close()


def test_revoke_exam_access(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        grant_exam_access(db, username="alice", exam_id="aws")
        assert (
            revoke_exam_access(
                db, username="alice", exam_id="aws"
            )
            is True
        )
        assert not has_exam_access(
            db, username="alice", exam_id="aws"
        )
    finally:
        db.close()


def test_revoke_exam_access_missing_is_no_op(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert (
            revoke_exam_access(db, username="alice", exam_id="unknown")
            is False
        )
    finally:
        db.close()


def test_list_accessible_exam_ids_sorts_alphabetically(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    try:
        for exam_id in ("charlie", "alpha", "bravo"):
            grant_exam_access(db, username="alice", exam_id=exam_id)
        assert list_accessible_exam_ids(db, username="alice") == [
            "alpha",
            "bravo",
            "charlie",
        ]
    finally:
        db.close()


def test_list_accessible_exam_ids_empty_for_new_user(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    try:
        assert list_accessible_exam_ids(db, username="alice") == []
    finally:
        db.close()


def test_deleting_user_cascades_grants(tmp_path: Path) -> None:
    """``ON DELETE CASCADE`` on ``users(username)`` drops grants."""

    db = _db(tmp_path)
    try:
        grant_exam_access(db, username="alice", exam_id="aws")
        db.execute("DELETE FROM users WHERE username = ?", ("alice",))
        db.commit()
        assert list_grants_for_exam(db, "aws") == []
    finally:
        db.close()


# ---------- requests ----------


def test_request_exam_access_creates_pending(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        req = request_exam_access(
            db,
            username="alice",
            exam_id="aws",
            requested_at="2026-04-23T15:00:00Z",
        )
        assert req.status == "pending"
        assert req.decided_at is None
        assert req.decided_by is None
    finally:
        db.close()


def test_request_exam_access_rejects_when_already_granted(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    try:
        grant_exam_access(db, username="alice", exam_id="aws")
        with pytest.raises(ValueError, match="already has access"):
            request_exam_access(db, username="alice", exam_id="aws")
    finally:
        db.close()


def test_request_exam_access_resubmit_keeps_pending(
    tmp_path: Path,
) -> None:
    """Filing the same request twice just refreshes the timestamp."""

    db = _db(tmp_path)
    try:
        request_exam_access(
            db,
            username="alice",
            exam_id="aws",
            requested_at="2026-04-23T10:00:00Z",
        )
        request_exam_access(
            db,
            username="alice",
            exam_id="aws",
            requested_at="2026-04-23T12:00:00Z",
        )
        pending = list_pending_requests(db)
        assert len(pending) == 1
        assert pending[0].requested_at == "2026-04-23T12:00:00Z"
    finally:
        db.close()


def test_request_exam_access_after_rejection_reopens(
    tmp_path: Path,
) -> None:
    """Re-requesting after rejection flips status back to pending."""

    db = _db(tmp_path)
    try:
        request_exam_access(db, username="alice", exam_id="aws")
        reject_access_request(
            db, username="alice", exam_id="aws", rejected_by="admin"
        )
        assert (
            get_access_request(
                db, username="alice", exam_id="aws"
            ).status  # type: ignore[union-attr]
            == "rejected"
        )

        request_exam_access(db, username="alice", exam_id="aws")
        refreshed = get_access_request(
            db, username="alice", exam_id="aws"
        )
        assert refreshed is not None
        assert refreshed.status == "pending"
        assert refreshed.decided_at is None
        assert refreshed.decided_by is None
    finally:
        db.close()


def test_approve_access_request_grants_and_flips_status(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    try:
        request_exam_access(db, username="alice", exam_id="aws")
        assert (
            approve_access_request(
                db,
                username="alice",
                exam_id="aws",
                approved_by="admin",
                decided_at="2026-04-23T15:00:00Z",
            )
            is True
        )
        assert has_exam_access(db, username="alice", exam_id="aws")
        decided = get_access_request(
            db, username="alice", exam_id="aws"
        )
        assert decided is not None
        assert decided.status == "approved"
        assert decided.decided_by == "admin"
        assert decided.decided_at == "2026-04-23T15:00:00Z"
    finally:
        db.close()


def test_approve_access_request_is_noop_when_missing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert (
            approve_access_request(
                db,
                username="alice",
                exam_id="aws",
                approved_by="admin",
            )
            is False
        )
        assert not has_exam_access(db, username="alice", exam_id="aws")
    finally:
        db.close()


def test_approve_access_request_is_noop_for_already_decided(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    try:
        request_exam_access(db, username="alice", exam_id="aws")
        approve_access_request(
            db,
            username="alice",
            exam_id="aws",
            approved_by="admin",
        )
        # Second approval call does nothing — request no longer pending.
        assert (
            approve_access_request(
                db,
                username="alice",
                exam_id="aws",
                approved_by="admin2",
            )
            is False
        )
    finally:
        db.close()


def test_reject_access_request_flips_status(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        request_exam_access(db, username="alice", exam_id="aws")
        assert (
            reject_access_request(
                db,
                username="alice",
                exam_id="aws",
                rejected_by="admin",
            )
            is True
        )
        assert not has_exam_access(db, username="alice", exam_id="aws")
        rejected = get_access_request(
            db, username="alice", exam_id="aws"
        )
        assert rejected is not None
        assert rejected.status == "rejected"
    finally:
        db.close()


def test_list_pending_requests_orders_by_timestamp(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        request_exam_access(
            db,
            username="alice",
            exam_id="aws",
            requested_at="2026-04-23T12:00:00Z",
        )
        request_exam_access(
            db,
            username="bob",
            exam_id="aws",
            requested_at="2026-04-23T10:00:00Z",
        )
        pending = list_pending_requests(db)
        assert [r.username for r in pending] == ["bob", "alice"]
    finally:
        db.close()


def test_list_requests_for_user_returns_all_statuses(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        request_exam_access(db, username="alice", exam_id="aws")
        reject_access_request(
            db, username="alice", exam_id="aws", rejected_by="admin"
        )
        request_exam_access(db, username="alice", exam_id="azure")
        requests = list_requests_for_user(db, "alice")
        assert len(requests) == 2
        exams = {r.exam_id: r.status for r in requests}
        assert exams == {"aws": "rejected", "azure": "pending"}
    finally:
        db.close()


def test_purge_acl_for_exam_drops_both_tables(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        grant_exam_access(db, username="alice", exam_id="aws")
        request_exam_access(db, username="bob", exam_id="aws")
        # Unrelated exam should stay intact.
        grant_exam_access(db, username="alice", exam_id="azure")

        count = purge_acl_for_exam(db, "aws")
        assert count == 2
        assert list_grants_for_exam(db, "aws") == []
        assert list_requests_for_user(db, "bob") == []
        # Untouched exam still has its grant.
        assert list_accessible_exam_ids(db, username="alice") == ["azure"]
    finally:
        db.close()


def test_purge_acl_for_exam_noop_when_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert purge_acl_for_exam(db, "nonexistent") == 0
    finally:
        db.close()
