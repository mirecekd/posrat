"""POSRAT system-level storage and services.

This subpackage hosts data and helpers that are *not* scoped to a single
exam database. Unlike :mod:`posrat.storage` (which owns one SQLite file
per exam), :mod:`posrat.system` manages a single global ``system.sqlite``
file sitting next to the exam bundles inside the configured data
directory. The system database is the home for cross-exam concerns such
as user accounts, exam access control lists, and audit trails.

The package is introduced in Phase 10 (Multi-user + admin). Fresh
installations will create ``system.sqlite`` on first launch just like
:mod:`posrat.storage.open_db` creates per-exam databases.
"""

from __future__ import annotations

from posrat.system.acl_repo import (
    AccessRequestStatus,
    ExamAccessGrant,
    ExamAccessRequest,
    approve_access_request,
    get_access_request,
    grant_exam_access,
    has_exam_access,
    list_accessible_exam_ids,
    list_grants_for_exam,
    list_pending_requests,
    list_requests_for_user,
    purge_acl_for_exam,
    reject_access_request,
    request_exam_access,
    revoke_exam_access,
)
from posrat.system.auth import (
    BCRYPT_MAX_PASSWORD_BYTES,
    BCRYPT_ROUNDS,
    hash_password,
    verify_password,
)
from posrat.system.auth_service import (
    authenticate_internal,
    provision_proxy_user,
    resolve_effective_user,
)
from posrat.system.auth_session import (
    AUTH_STORAGE_KEY,
    build_auth_stash,
    read_auth_source_from_stash,
    read_username_from_stash,
)
from posrat.system.bootstrap import (
    ADMIN_DISPLAY_NAME_ENV,
    ADMIN_PASSWORD_ENV,
    ADMIN_USERNAME_ENV,
    BootstrapResult,
    ResetResult,
    bootstrap_admin_from_env,
    reset_admin_password_cli,
)
from posrat.system.schema import MIGRATIONS
from posrat.system.system_db import (
    CURRENT_SYSTEM_SCHEMA_VERSION,
    SYSTEM_DB_FILENAME,
    apply_system_migrations,
    open_system_db,
    resolve_system_db_path,
)
from posrat.system.users_repo import (
    count_admins,
    create_user,
    delete_user,
    get_user,
    list_users,
    touch_last_login,
    update_user_password,
    update_user_roles,
)

__all__ = [
    "ADMIN_DISPLAY_NAME_ENV",
    "ADMIN_PASSWORD_ENV",
    "ADMIN_USERNAME_ENV",
    "AUTH_STORAGE_KEY",
    "AccessRequestStatus",
    "BCRYPT_MAX_PASSWORD_BYTES",
    "BCRYPT_ROUNDS",
    "BootstrapResult",
    "CURRENT_SYSTEM_SCHEMA_VERSION",
    "ExamAccessGrant",
    "ExamAccessRequest",
    "MIGRATIONS",
    "ResetResult",
    "SYSTEM_DB_FILENAME",
    "apply_system_migrations",
    "approve_access_request",
    "authenticate_internal",
    "bootstrap_admin_from_env",
    "build_auth_stash",
    "count_admins",
    "create_user",
    "delete_user",
    "get_access_request",
    "get_user",
    "grant_exam_access",
    "has_exam_access",
    "hash_password",
    "list_accessible_exam_ids",
    "list_grants_for_exam",
    "list_pending_requests",
    "list_requests_for_user",
    "list_users",
    "open_system_db",
    "provision_proxy_user",
    "purge_acl_for_exam",
    "read_auth_source_from_stash",
    "read_username_from_stash",
    "reject_access_request",
    "request_exam_access",
    "reset_admin_password_cli",
    "resolve_effective_user",
    "resolve_system_db_path",
    "revoke_exam_access",
    "touch_last_login",
    "update_user_password",
    "update_user_roles",
    "verify_password",
]
