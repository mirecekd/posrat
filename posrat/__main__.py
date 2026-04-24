"""POSRAT entry point.

By default (no arguments) delegates to :func:`posrat.app.main`, which
launches the NiceGUI server. Running ``python -m posrat`` therefore
opens the application in a browser tab — same behaviour as before
Phase 10.

Phase 10 adds a small CLI dispatcher in front of the default path so
operators can create or reset an admin account without starting the
server:

- ``python -m posrat create-admin <username>`` — interactive prompt
  for a new password, writes the hashed value into the system DB.

Any other positional token is rejected with a short usage message so
``python -m posrat --help`` still behaves like a newbie would expect.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from posrat.app import main as run_server
from posrat.designer import resolve_data_dir
from posrat.system import reset_admin_password_cli


def _print_usage() -> None:
    print(
        "Usage:\n"
        "  python -m posrat                          launch the NiceGUI server (default)\n"
        "  python -m posrat create-admin <username>  interactively (re)set an admin password\n",
        file=sys.stderr,
    )


def _cmd_create_admin(argv: Sequence[str]) -> int:
    """Run the interactive ``create-admin`` subcommand.

    ``argv`` is the slice after ``create-admin`` itself (``sys.argv[2:]``).
    Positional: the username. Extra tokens are rejected so typos like
    ``create-admin alice bob`` fail loudly instead of silently creating
    just ``alice``.
    """

    if len(argv) != 1:
        print(
            "create-admin takes exactly one argument: <username>",
            file=sys.stderr,
        )
        return 2

    username = argv[0].strip()
    if not username:
        print("username must not be empty", file=sys.stderr)
        return 2

    data_dir = Path(resolve_data_dir())
    try:
        result = reset_admin_password_cli(data_dir, username)
    except ValueError as exc:
        print(f"create-admin failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # ``getpass`` intercepts Ctrl-C while waiting for input; turn
        # that into a graceful non-zero exit so shell pipelines see a
        # well-defined status.
        print("\ncreate-admin aborted", file=sys.stderr)
        return 130

    print(result.message)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch ``python -m posrat`` subcommands.

    Defaults to launching the NiceGUI server (no arguments). Returns
    the subcommand's exit code so callers (``sys.exit(main())``)
    propagate it verbatim.
    """

    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        return run_server()

    command, *rest = argv
    if command == "create-admin":
        return _cmd_create_admin(rest)
    if command in {"-h", "--help", "help"}:
        _print_usage()
        return 0

    print(f"unknown command: {command!r}", file=sys.stderr)
    _print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
