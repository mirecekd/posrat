"""Password hashing helpers for POSRAT internal accounts.

Uses the :mod:`bcrypt` library directly (skipping the more common
``passlib`` wrapper) because passlib 1.7.4 currently crashes at import
time on bcrypt 5.x due to an internal probe that trips the "password
cannot be longer than 72 bytes" guard. Using ``bcrypt`` as a first-class
dependency shrinks the surface area and matches the shape other modern
Python projects reach for today.

Bcrypt has a hard **72-byte** ceiling on the password input — longer
passwords are either silently truncated (older versions) or rejected
with ``ValueError`` (bcrypt 5.x). We pre-truncate on the UTF-8 byte
representation of the password so the contract is explicit rather than
version-dependent:

- ``hash_password("a" * 80)`` hashes only the first 72 bytes.
- ``verify_password("a" * 80, h) == verify_password("a" * 72, h)``.

That is the same semantics users already accept when interacting with
any bcrypt-backed system (git, macOS login, etc.), so we surface it
here explicitly instead of pretending it is not a thing.

The :data:`BCRYPT_ROUNDS` module-level constant lets tests override the
work factor via :func:`hash_password` calls — production callers just
rely on the default.
"""

from __future__ import annotations

import bcrypt

#: Default bcrypt cost (work) factor. 12 is the OWASP-recommended floor
#: for 2024+. Tests drop this to 4 via the ``rounds=`` argument of
#: :func:`hash_password` to keep the suite fast (bcrypt 12 is ~250 ms
#: per hash on a laptop, which adds up across hundreds of tests).
BCRYPT_ROUNDS = 12


#: Hard upper bound bcrypt enforces on the password byte length. Stored
#: as a module constant so the truncation logic in :func:`hash_password`
#: and the docstring stay in sync. Exposed for tests that want to
#: assert the invariant directly.
BCRYPT_MAX_PASSWORD_BYTES = 72


def _truncate_to_bcrypt_limit(password: str) -> bytes:
    """Encode ``password`` as UTF-8 and clip to 72 bytes.

    Splitting the string at a byte boundary may land in the middle of a
    multi-byte codepoint, which is fine for hashing purposes (bcrypt
    takes bytes, not text) — the resulting hash is deterministic for
    any given input, and :func:`verify_password` applies the same
    truncation so round-trips work.
    """

    return password.encode("utf-8")[:BCRYPT_MAX_PASSWORD_BYTES]


def hash_password(password: str, *, rounds: int = BCRYPT_ROUNDS) -> str:
    """Return a bcrypt hash of ``password`` as an ASCII-safe string.

    Raises:
        ValueError: when ``password`` is empty. Allowing zero-length
            secrets would let callers of :func:`create_user` drift into
            an "internal account but password-hash was empty string"
            state — reject it at the bottleneck.
    """

    if not password:
        raise ValueError("password must not be empty")

    secret = _truncate_to_bcrypt_limit(password)
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(secret, salt).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Return ``True`` when ``password`` matches ``hashed``.

    Wrong password / malformed hash → ``False`` (never raises for
    expected user input). This mirrors the "constant-time result
    indicates mismatch" contract of :func:`bcrypt.checkpw` and keeps
    the call site simple: the auth service can just branch on the
    boolean without a try/except wrapper.
    """

    if not password or not hashed:
        return False

    secret = _truncate_to_bcrypt_limit(password)
    try:
        return bcrypt.checkpw(secret, hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash (not a bcrypt string, wrong encoding, etc.)
        # — treat as mismatch rather than propagating an error. Admins
        # inspecting the audit log still see the "failed login" event.
        return False
