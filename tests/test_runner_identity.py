"""Tests for :mod:`posrat.runner.identity`.

Resolution order:

1. ``headers[header_name]`` (nginx / OIDC forwarded subject).
2. ``env[USERNAME_ENV]`` ($USER fallback).
3. :data:`DEFAULT_LOCAL_USERNAME` static safety net.

Whitespace-only values at each stage must fall through to the next.
"""

from __future__ import annotations

import pytest

from posrat.runner.identity import (
    DEFAULT_LOCAL_USERNAME,
    DEFAULT_USERNAME_HEADER,
    USERNAME_ENV,
    resolve_username,
)


def test_resolve_username_prefers_header_when_present() -> None:
    """A non-empty header value wins over env and default."""

    result = resolve_username(
        headers={DEFAULT_USERNAME_HEADER: "alice@example.com"},
        env={USERNAME_ENV: "bob"},
    )
    assert result == "alice@example.com"


def test_resolve_username_trims_header_whitespace() -> None:
    """Surrounding whitespace is stripped from the header value."""

    result = resolve_username(
        headers={DEFAULT_USERNAME_HEADER: "  alice  "},
        env={USERNAME_ENV: "bob"},
    )
    assert result == "alice"


def test_resolve_username_falls_back_to_env_when_header_missing() -> None:
    """No header key → use the USER env var."""

    result = resolve_username(
        headers={},
        env={USERNAME_ENV: "bob"},
    )
    assert result == "bob"


def test_resolve_username_falls_back_to_env_when_header_blank() -> None:
    """Whitespace-only header is treated as missing."""

    result = resolve_username(
        headers={DEFAULT_USERNAME_HEADER: "   "},
        env={USERNAME_ENV: "bob"},
    )
    assert result == "bob"


def test_resolve_username_falls_back_to_default_when_both_missing() -> None:
    """Neither header nor env set → static safety net."""

    result = resolve_username(
        headers={},
        env={},
    )
    assert result == DEFAULT_LOCAL_USERNAME


def test_resolve_username_falls_back_to_default_when_env_blank() -> None:
    """Whitespace-only env value is treated as missing too."""

    result = resolve_username(
        headers={},
        env={USERNAME_ENV: "   "},
    )
    assert result == DEFAULT_LOCAL_USERNAME


def test_resolve_username_accepts_custom_header_name() -> None:
    """Different header names (e.g. X-Forwarded-User) can be wired in."""

    result = resolve_username(
        headers={"X-Forwarded-User": "carol"},
        header_name="X-Forwarded-User",
        env={USERNAME_ENV: "bob"},
    )
    assert result == "carol"


def test_resolve_username_skips_headers_when_none() -> None:
    """No headers object (non-request context) → go straight to env."""

    result = resolve_username(headers=None, env={USERNAME_ENV: "bob"})
    assert result == "bob"


def test_resolve_username_reads_process_env_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``env=None`` falls through to ``os.environ`` (guarded via monkeypatch)."""

    monkeypatch.setenv(USERNAME_ENV, "proc_user")
    result = resolve_username(headers=None, env=None)
    assert result == "proc_user"
