"""Multi-user username resolution for the Runner.

In the POSRAT deployment model the app itself does not implement
authentication — instead it sits behind a reverse proxy (nginx / OIDC)
that forwards the authenticated username via a request header. For
local development we fall back to the ``USER`` environment variable
and, as a final safety net, to a static ``local_dev`` string so the
Runner always has *something* to attribute results to.

The helper deliberately accepts any object with a ``.get()`` method
(matching NiceGUI's ``request.headers`` dict-like) plus an optional
explicit ``username`` override used by the mode dialog: if the
candidate overrides their displayed name we trust their choice.

Kept in its own module (rather than inlined into :mod:`posrat.runner`)
so unit tests can exercise every fallback path without a NiceGUI
request context — pass in a plain ``dict`` and the ``os.environ``
lookup mocked via ``monkeypatch``.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional


#: Environment variable consulted by :func:`resolve_username` as the
#: middle fallback (between the header and the static default). Mirrors
#: the ubiquitous POSIX ``$USER`` contract so developers running POSRAT
#: locally get their own login name without configuration.
USERNAME_ENV = "USER"


#: Last-resort username used when neither the proxy header nor the
#: environment variable provide a value. Kept as a constant so tests
#: and deployments share a single vocabulary for the "unauthenticated
#: local dev" state; production traffic passing through nginx always
#: sets the header, so reaching this fallback in prod is a deployment
#: bug worth surfacing loudly in logs.
DEFAULT_LOCAL_USERNAME = "local_dev"


#: Default header name to look up in :func:`resolve_username`. The nginx
#: / OIDC integration typically forwards the authenticated subject via
#: ``X-Remote-User`` or ``X-Forwarded-User``; we pick the former as the
#: default but callers can pass a different name.
DEFAULT_USERNAME_HEADER = "X-Remote-User"


def resolve_username(
    headers: Optional[Mapping[str, str]] = None,
    *,
    header_name: str = DEFAULT_USERNAME_HEADER,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the effective username for the current Runner request.

    Resolution order (first non-empty value wins):

    1. ``headers[header_name]`` — nginx / OIDC forwarded subject.
    2. ``env[USERNAME_ENV]`` — POSIX ``$USER`` fallback for local dev.
    3. :data:`DEFAULT_LOCAL_USERNAME` — hard-coded safety net so the
       session row can always be written.

    ``headers`` is optional (some code paths don't carry a request
    context, e.g. a CLI-triggered grading tool); passing ``None`` skips
    straight to the env var. ``env`` defaults to :data:`os.environ`
    but tests can inject a pinned mapping for determinism.

    The returned value is always a non-empty string — whitespace-only
    header values are treated as missing and fall through to the next
    fallback.
    """

    if headers is not None:
        raw = headers.get(header_name)
        if raw is not None:
            trimmed = str(raw).strip()
            if trimmed:
                return trimmed

    env_mapping = env if env is not None else os.environ
    env_value = env_mapping.get(USERNAME_ENV)
    if env_value is not None:
        trimmed = str(env_value).strip()
        if trimmed:
            return trimmed

    return DEFAULT_LOCAL_USERNAME
