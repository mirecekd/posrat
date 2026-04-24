"""Tests for :mod:`posrat.system.auth_session`."""

from __future__ import annotations

from posrat.models import User
from posrat.system import (
    AUTH_STORAGE_KEY,
    build_auth_stash,
    read_auth_source_from_stash,
    read_username_from_stash,
)


def _make_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "username": "alice",
        "password_hash": "bcrypt$stub",
        "display_name": "Alice L.",
        "auth_source": "internal",
        "is_admin": True,
        "can_use_designer": True,
        "created_at": "2026-04-23T12:00:00Z",
        "last_login_at": None,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def test_auth_storage_key_constant() -> None:
    """Key name is part of the cookie contract — pin it."""

    assert AUTH_STORAGE_KEY == "auth_user"


def test_build_auth_stash_captures_username_and_source() -> None:
    """Happy path: stash carries the three fields the guard needs."""

    stash = build_auth_stash(_make_user(), login_at="2026-04-23T15:00:00Z")
    assert stash["username"] == "alice"
    assert stash["login_at"] == "2026-04-23T15:00:00Z"
    assert stash["auth_source"] == "internal"


def test_build_auth_stash_defaults_login_at_to_utc_now() -> None:
    """Without a timestamp we stamp UTC now in ``...Z`` form."""

    stash = build_auth_stash(_make_user())
    assert stash["login_at"].endswith("Z")
    # Parseable as UTC (delegates to the stdlib parser).
    from datetime import datetime

    datetime.fromisoformat(stash["login_at"].replace("Z", "+00:00"))


def test_build_auth_stash_for_proxy_user() -> None:
    """Proxy users carry ``auth_source='proxy'`` in the stash."""

    user = _make_user(auth_source="proxy", password_hash=None)
    stash = build_auth_stash(user)
    assert stash["auth_source"] == "proxy"


def test_read_username_handles_missing_dict() -> None:
    """``None`` / non-dict payloads surface as ``None``."""

    assert read_username_from_stash(None) is None
    assert read_username_from_stash("not-a-dict") is None
    assert read_username_from_stash(42) is None


def test_read_username_handles_missing_key() -> None:
    """Dicts without the key → ``None``."""

    assert read_username_from_stash({}) is None


def test_read_username_rejects_empty_value() -> None:
    """Empty / whitespace-only usernames → ``None``."""

    assert read_username_from_stash({"username": ""}) is None
    assert read_username_from_stash({"username": "   "}) is None


def test_read_username_strips_surrounding_whitespace() -> None:
    """Trim before handing to the DB layer."""

    assert read_username_from_stash({"username": "  alice  "}) == "alice"


def test_read_username_rejects_non_string_value() -> None:
    """Tampered cookie with integer / bool username → ``None``."""

    assert read_username_from_stash({"username": 42}) is None
    assert read_username_from_stash({"username": True}) is None


def test_read_auth_source_happy_path() -> None:
    """Both literal values round-trip."""

    assert (
        read_auth_source_from_stash({"auth_source": "internal"})
        == "internal"
    )
    assert (
        read_auth_source_from_stash({"auth_source": "proxy"}) == "proxy"
    )


def test_read_auth_source_rejects_unknown() -> None:
    """Anything outside the literal set → ``None``."""

    assert read_auth_source_from_stash({"auth_source": "oauth"}) is None
    assert read_auth_source_from_stash({"auth_source": None}) is None
    assert read_auth_source_from_stash({}) is None
    assert read_auth_source_from_stash(None) is None
