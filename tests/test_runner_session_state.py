"""Tests for :mod:`posrat.runner.session_state`."""

from __future__ import annotations

from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    advance_session_stash,
    build_runner_session_stash,
    is_session_stash_complete,
)


def _make_stash() -> dict:
    return build_runner_session_stash(
        session_id="s-1",
        exam_path="/tmp/exam.sqlite",
        exam_id="e1",
        mode="training",
        question_ids=["q-a", "q-b", "q-c"],
        started_at="2026-04-23T10:00:00Z",
        time_limit_minutes=90,
        candidate_name="Alice",
    )


def test_build_runner_session_stash_shape() -> None:
    """Stash dict contains every key downstream code expects."""

    stash = _make_stash()
    assert stash["session_id"] == "s-1"
    assert stash["exam_path"] == "/tmp/exam.sqlite"
    assert stash["exam_id"] == "e1"
    assert stash["mode"] == "training"
    assert stash["question_ids"] == ["q-a", "q-b", "q-c"]
    assert stash["current_index"] == 0
    assert stash["started_at"] == "2026-04-23T10:00:00Z"
    assert stash["time_limit_minutes"] == 90
    assert stash["candidate_name"] == "Alice"


def test_build_runner_session_stash_copies_question_ids() -> None:
    """Mutating the caller's list must not retroactively affect the stash."""

    ids = ["q-a", "q-b"]
    stash = build_runner_session_stash(
        session_id="s",
        exam_path="/tmp/e.sqlite",
        exam_id="e",
        mode="exam",
        question_ids=ids,
        started_at="2026-04-23T10:00:00Z",
        time_limit_minutes=None,
        candidate_name="A",
    )
    ids.append("q-c")
    assert stash["question_ids"] == ["q-a", "q-b"]


def test_is_session_stash_complete_happy_path() -> None:
    """Freshly-built stash counts as complete."""

    assert is_session_stash_complete(_make_stash()) is True


def test_is_session_stash_complete_rejects_partial() -> None:
    """Missing a required key → ``False``."""

    stash = _make_stash()
    del stash["time_limit_minutes"]
    assert is_session_stash_complete(stash) is False


def test_is_session_stash_complete_rejects_non_dict() -> None:
    """None / primitive types must not blow up inside the UI guard."""

    assert is_session_stash_complete(None) is False
    assert is_session_stash_complete("nope") is False
    assert is_session_stash_complete([]) is False


def test_advance_session_stash_not_finished() -> None:
    """First call on a 3-question stash leaves room for 2 more."""

    stash = _make_stash()
    finished = advance_session_stash(stash)
    assert finished is False
    assert stash["current_index"] == 1


def test_advance_session_stash_finishes_past_last() -> None:
    """Cursor past the last question → ``True`` (finished)."""

    stash = _make_stash()
    assert advance_session_stash(stash) is False
    assert advance_session_stash(stash) is False
    finished = advance_session_stash(stash)
    assert finished is True
    assert stash["current_index"] == 3


def test_runner_session_storage_key_constant() -> None:
    """The storage key stays stable — we pin it so downstream code can
    rely on a fixed cookie path across refactors."""

    assert RUNNER_SESSION_STORAGE_KEY == "runner_session"
