"""Tests for the ``python -m posrat`` CLI dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from posrat.__main__ import main
from posrat.system import (
    SYSTEM_DB_FILENAME,
    get_user,
    open_system_db,
    verify_password,
)


def _with_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``resolve_data_dir`` to a tmp dir and return the path."""

    monkeypatch.setenv("POSRAT_DATA_DIR", str(tmp_path))
    return tmp_path


def test_no_arguments_invokes_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare ``python -m posrat`` must delegate to ``run_server``."""

    fake_server = MagicMock(return_value=0)
    monkeypatch.setattr("posrat.__main__.run_server", fake_server)

    assert main([]) == 0
    fake_server.assert_called_once_with()


def test_unknown_command_returns_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Typos → exit code 2 + usage to stderr."""

    fake_server = MagicMock(return_value=0)
    monkeypatch.setattr("posrat.__main__.run_server", fake_server)

    assert main(["run-server"]) == 2
    err = capsys.readouterr().err
    assert "unknown command" in err
    assert "create-admin" in err  # usage mentions the subcommand
    fake_server.assert_not_called()


def test_help_flags_print_usage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` exits 0 without starting the server."""

    fake_server = MagicMock(return_value=0)
    monkeypatch.setattr("posrat.__main__.run_server", fake_server)

    for flag in ("-h", "--help", "help"):
        capsys.readouterr()  # reset
        assert main([flag]) == 0
        err = capsys.readouterr().err
        assert "Usage:" in err
    fake_server.assert_not_called()


def test_create_admin_requires_username_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing username token → exit 2."""

    assert main(["create-admin"]) == 2
    assert "exactly one argument" in capsys.readouterr().err


def test_create_admin_rejects_extra_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``create-admin alice bob`` → exit 2."""

    assert main(["create-admin", "alice", "bob"]) == 2
    assert "exactly one argument" in capsys.readouterr().err


def test_create_admin_rejects_blank_username(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whitespace-only username is rejected before any DB work."""

    assert main(["create-admin", "   "]) == 2
    assert "must not be empty" in capsys.readouterr().err


def test_create_admin_persists_admin_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: CLI creates admin with hashed password."""

    data_dir = _with_data_dir(tmp_path, monkeypatch)

    fake_reset = MagicMock(
        side_effect=lambda dd, username, **kw: __import__(
            "posrat.system.bootstrap", fromlist=["reset_admin_password_cli"]
        ).reset_admin_password_cli(
            dd,
            username,
            prompt_password=lambda: "s3cret",
            confirm_password=lambda: "s3cret",
            display_name=kw.get("display_name"),
        ),
    )
    with patch(
        "posrat.__main__.reset_admin_password_cli",
        side_effect=lambda dd, username: __import__(
            "posrat.system.bootstrap", fromlist=["reset_admin_password_cli"]
        ).reset_admin_password_cli(
            dd,
            username,
            prompt_password=lambda: "s3cret",
            confirm_password=lambda: "s3cret",
        ),
    ):
        assert main(["create-admin", "alice"]) == 0

    out = capsys.readouterr().out
    assert "alice" in out

    db = open_system_db(data_dir / SYSTEM_DB_FILENAME)
    try:
        user = get_user(db, "alice")
        assert user is not None
        assert user.is_admin is True
        assert verify_password("s3cret", user.password_hash or "")
    finally:
        db.close()


def test_create_admin_translates_value_error_to_exit_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ValueError from the bootstrap helper → exit 1, message on stderr."""

    _with_data_dir(tmp_path, monkeypatch)

    with patch(
        "posrat.__main__.reset_admin_password_cli",
        side_effect=ValueError("passwords do not match"),
    ):
        assert main(["create-admin", "alice"]) == 1

    assert "passwords do not match" in capsys.readouterr().err


def test_create_admin_handles_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ctrl-C during password prompt → exit 130 (SIGINT convention)."""

    _with_data_dir(tmp_path, monkeypatch)

    with patch(
        "posrat.__main__.reset_admin_password_cli",
        side_effect=KeyboardInterrupt,
    ):
        assert main(["create-admin", "alice"]) == 130

    assert "aborted" in capsys.readouterr().err
