"""User account model for the POSRAT system database.

Part of Phase 10 (Multi-user + admin). A :class:`User` row lives in the
global ``system.sqlite`` — it is *not* scoped to a single exam. Auth,
ACL, and admin UI layers all consume this shape.

The model captures the smallest useful surface area: a unique username,
an optional password hash (``None`` for users provisioned from an
external reverse-proxy header), a human-readable display name, the
provenance of the account (``internal`` vs ``proxy``), two role flags
(``is_admin``, ``can_use_designer``), and timestamp bookkeeping. Exam-
level ACLs are *not* part of this model — they live in their own table
introduced in step 10.8.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

#: Provenance of a user account. ``internal`` accounts authenticate via
#: the POSRAT login page (password stored as a bcrypt hash), ``proxy``
#: accounts are auto-provisioned on first sight of a trusted reverse-
#: proxy header (nginx ``X-Remote-User`` / ALB Cognito) and never carry
#: a password hash of their own.
AuthSource = Literal["internal", "proxy"]


class User(BaseModel):
    """A single POSRAT account.

    Attributes:
        username: Stable lowercase-safe identifier. Matches the value
            the reverse proxy forwards for proxy-auth accounts and the
            login input for internal accounts. Must be unique across the
            system database (enforced at the SQL layer as the PK).
        password_hash: Bcrypt hash (passlib format) for internal
            accounts; ``None`` for proxy accounts. The auth service
            (10.4) refuses to verify against a ``None`` hash, which
            means proxy users *must* come in through the trusted header.
        display_name: Optional human-readable name shown in the header
            greeting and session snapshots. Falls back to ``username``
            when missing.
        auth_source: See :data:`AuthSource`.
        is_admin: Whether the user can reach ``/admin``.
        can_use_designer: Whether the user can reach ``/designer``.
            Runner access is implicit — every authenticated user can
            see the picker; per-exam access is gated later by ACL.
        created_at: ISO-8601 UTC timestamp of account creation.
        last_login_at: ISO-8601 UTC timestamp of the most recent
            successful login, or ``None`` for accounts that have never
            signed in. Updated by the auth service (10.4) on each
            successful ``authenticate_internal`` / ``provision_proxy_user``
            call.
    """

    username: str = Field(..., min_length=1, max_length=128)
    password_hash: Optional[str] = Field(default=None, min_length=1)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    auth_source: AuthSource
    is_admin: bool = False
    can_use_designer: bool = False
    created_at: str = Field(..., min_length=1)
    last_login_at: Optional[str] = None

    @property
    def effective_display_name(self) -> str:
        """Return the display name, falling back to ``username``.

        UI code (header greeting, admin tables) should reach for this
        rather than branching on ``display_name is None`` itself.
        """

        return self.display_name or self.username
