"""Tests for :mod:`posrat.system.auth` — bcrypt hashing helpers."""

from __future__ import annotations

import pytest

from posrat.system import (
    BCRYPT_MAX_PASSWORD_BYTES,
    BCRYPT_ROUNDS,
    hash_password,
    verify_password,
)

#: Test-only low work factor. Production uses :data:`BCRYPT_ROUNDS` (12)
#: but 4 is the bcrypt minimum and keeps the whole file under 1 s.
FAST_ROUNDS = 4


def test_bcrypt_rounds_default_is_production_grade() -> None:
    """OWASP 2024 floor is 12 — sanity-check the constant."""

    assert BCRYPT_ROUNDS >= 12


def test_bcrypt_max_password_bytes_is_seventy_two() -> None:
    """bcrypt's hard ceiling — documented in :mod:`posrat.system.auth`."""

    assert BCRYPT_MAX_PASSWORD_BYTES == 72


def test_hash_password_returns_non_empty_string() -> None:
    """Hash is a string (not bytes) so it can land in TEXT columns."""

    h = hash_password("correct horse battery staple", rounds=FAST_ROUNDS)
    assert isinstance(h, str)
    assert h.startswith("$2")  # bcrypt prefix
    assert len(h) >= 50


def test_hash_password_rejects_empty() -> None:
    """Empty secret would sabotage :func:`create_user`'s invariants."""

    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("", rounds=FAST_ROUNDS)


def test_hash_password_is_nondeterministic() -> None:
    """Two hashes of the same password differ because bcrypt salts randomly."""

    a = hash_password("same-password", rounds=FAST_ROUNDS)
    b = hash_password("same-password", rounds=FAST_ROUNDS)
    assert a != b
    # Both still verify.
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_verify_password_accepts_correct() -> None:
    """Round-trip: hash → verify == True."""

    h = hash_password("s3cret", rounds=FAST_ROUNDS)
    assert verify_password("s3cret", h) is True


def test_verify_password_rejects_wrong() -> None:
    """Mismatched secret → False (never raises)."""

    h = hash_password("s3cret", rounds=FAST_ROUNDS)
    assert verify_password("w0rng", h) is False


def test_verify_password_rejects_empty_password() -> None:
    """Empty input → False without raising."""

    h = hash_password("s3cret", rounds=FAST_ROUNDS)
    assert verify_password("", h) is False


def test_verify_password_rejects_empty_hash() -> None:
    """Empty stored hash (e.g. proxy account) → False, no crash."""

    assert verify_password("anything", "") is False


def test_verify_password_rejects_malformed_hash() -> None:
    """Non-bcrypt strings in the hash column → False, not ValueError."""

    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_password_truncates_above_72_bytes() -> None:
    """bcrypt's 72-byte ceiling is handled transparently.

    Anything past byte 72 is silently clipped. A password padded with a
    different suffix must still verify as long as the first 72 bytes
    match — that's the contract of bcrypt, we just make it explicit
    here instead of surfacing the raw ``ValueError``.
    """

    base = "a" * BCRYPT_MAX_PASSWORD_BYTES
    h = hash_password(base + "extra-ignored", rounds=FAST_ROUNDS)
    assert verify_password(base, h) is True
    assert verify_password(base + "totally-different-suffix", h) is True


def test_hash_password_unicode_is_encoded_as_utf8() -> None:
    """UTF-8 multibyte characters work and survive round-trip."""

    password = "hes\u010dalovo heslo s diakritikou \u017e\u00fd"
    h = hash_password(password, rounds=FAST_ROUNDS)
    assert verify_password(password, h) is True
    assert verify_password("neco-jineho", h) is False


def test_verify_password_case_sensitive() -> None:
    """Bcrypt doesn't fold case — assert our helper preserves that."""

    h = hash_password("Password123", rounds=FAST_ROUNDS)
    assert verify_password("Password123", h) is True
    assert verify_password("password123", h) is False
    assert verify_password("PASSWORD123", h) is False


def test_hash_password_accepts_custom_rounds() -> None:
    """The ``rounds`` kwarg is honoured (bcrypt prefix encodes it)."""

    h = hash_password("abc", rounds=FAST_ROUNDS)
    # Bcrypt format: "$2b$<cost>$<22-char-salt><31-char-hash>"
    # The cost segment is the two characters after the second "$".
    parts = h.split("$")
    assert len(parts) >= 4
    assert int(parts[2]) == FAST_ROUNDS
