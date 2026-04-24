"""Tests for :class:`posrat.models.User`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from posrat.models import AuthSource, User


def _make_user(**overrides: object) -> User:
    """Helper: build a valid internal-account ``User`` with overrides."""

    defaults: dict[str, object] = {
        "username": "alice",
        "password_hash": "bcrypt$stub",
        "display_name": "Alice Liddell",
        "auth_source": "internal",
        "is_admin": False,
        "can_use_designer": False,
        "created_at": "2026-04-23T12:00:00Z",
        "last_login_at": None,
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def test_user_default_internal_account_round_trip() -> None:
    """Happy path: construct an internal account, dump, reload."""

    user = _make_user()
    assert user.username == "alice"
    assert user.auth_source == "internal"
    assert user.is_admin is False
    dumped = user.model_dump()
    rehydrated = User.model_validate(dumped)
    assert rehydrated == user


def test_user_proxy_account_has_no_password_hash() -> None:
    """Proxy users carry ``password_hash=None`` by contract."""

    user = _make_user(password_hash=None, auth_source="proxy")
    assert user.password_hash is None
    assert user.auth_source == "proxy"


def test_user_username_required_non_empty() -> None:
    """Empty username is rejected fail-fast by Pydantic."""

    with pytest.raises(ValidationError):
        _make_user(username="")


def test_user_display_name_optional_but_cannot_be_empty_string() -> None:
    """``min_length=1`` keeps the ``None`` fallback intact.

    Empty-string display names confuse UI fallback logic
    (:attr:`User.effective_display_name` would fall through to
    ``username or ""`` depending on operator). Rejecting them at the
    model boundary makes the invariant explicit.
    """

    with pytest.raises(ValidationError):
        _make_user(display_name="")


def test_user_auth_source_must_be_literal() -> None:
    """Unknown auth sources raise — CHECK constraint on DB mirrors this."""

    with pytest.raises(ValidationError):
        _make_user(auth_source="oauth")  # type: ignore[arg-type]


def test_user_admin_flag_is_boolean() -> None:
    """Boolean fields accept ``True`` / ``False`` explicitly."""

    user = _make_user(is_admin=True, can_use_designer=True)
    assert user.is_admin is True
    assert user.can_use_designer is True


def test_user_effective_display_name_prefers_display_name() -> None:
    """UI helper picks the friendlier name when available."""

    user = _make_user(display_name="Alice Liddell")
    assert user.effective_display_name == "Alice Liddell"


def test_user_effective_display_name_falls_back_to_username() -> None:
    """``None`` display name → username."""

    user = _make_user(display_name=None)
    assert user.effective_display_name == "alice"


def test_user_created_at_required() -> None:
    """``created_at`` is mandatory: empty string rejected."""

    with pytest.raises(ValidationError):
        _make_user(created_at="")


def test_user_last_login_at_optional_none_default() -> None:
    """Freshly-created users have never logged in."""

    user = _make_user()
    assert user.last_login_at is None


def test_auth_source_literal_values() -> None:
    """Compile-time sanity: allowed values are exactly these two."""

    allowed: tuple[AuthSource, ...] = ("internal", "proxy")
    for value in allowed:
        _make_user(
            auth_source=value,
            password_hash=None if value == "proxy" else "bcrypt$stub",
        )
