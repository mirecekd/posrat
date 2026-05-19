"""Tests for :mod:`posrat.ai.enrich_cli` and the ``enrich`` dispatcher.

The CLI calls :func:`posrat.ai.enrich_cli._load_settings` to pull the
admin AI settings out of ``system.sqlite``. Tests monkey-patch that
helper so they can drive the CLI end-to-end without touching the
real data dir.

The agent itself is stubbed by replacing
:func:`posrat.ai.agent.run_chat_turn` (re-exported into the runner
module under the same name).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from posrat.ai import enrich_cli, enrich_runner
from posrat.ai.config import AISettings
from posrat.designer.browser import (
    create_exam_file,
    load_questions_from_file,
)
from posrat.models import Choice, Question
from posrat.storage import add_question, open_db


def _seed_simple_exam(tmp_path: Path) -> Path:
    exam_path = create_exam_file(
        tmp_path, "exam-cli", "CLI Exam", description=None
    )
    db = open_db(exam_path)
    try:
        add_question(
            db,
            "exam-cli",
            Question(
                id="q-cli-1",
                type="single_choice",
                text="What is the answer?",
                explanation=None,
                choices=[
                    Choice(id="q-cli-1-a", text="A", is_correct=False),
                    Choice(id="q-cli-1-b", text="B", is_correct=True),
                    Choice(id="q-cli-1-c", text="C", is_correct=False),
                    Choice(id="q-cli-1-d", text="D", is_correct=False),
                ],
            ),
        )
    finally:
        db.close()
    return exam_path


def _make_enabled_settings() -> AISettings:
    return AISettings(
        enabled=True,
        model_id="anthropic.claude-3",
        region="eu-west-1",
        system_prompt=None,
        mcp_config_json=None,
        enrich_prompt=None,
        updated_at=None,
    )


def _stub_run_chat_turn(reply: str):
    async def _runner(
        settings,
        prompt,
        *,
        question_context="",
        mcp_clients=None,
        prior_messages=None,
    ):
        yield {"data": reply}
        yield {"complete_messages": []}

    return _runner


def test_run_enrich_command_match_writes_explanation_and_creates_backup(
    tmp_path, monkeypatch, capsys
):
    exam_path = _seed_simple_exam(tmp_path)

    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: B\n\nBecause."),
    )

    code = enrich_cli.run_enrich_command([str(exam_path)])
    assert code == 0

    backups = list(tmp_path.glob("exam-cli.bak-*.sqlite"))
    assert len(backups) == 1, backups

    questions = load_questions_from_file(exam_path, "exam-cli")
    q = questions[0]
    assert q.explanation == "## Correct Answer: B\n\nBecause."

    captured = capsys.readouterr()
    assert "Backup written:" in captured.out
    assert "[match] q-cli-1" in captured.out
    assert "Summary:" in captured.out


def test_run_enrich_command_dry_run_skips_writes(tmp_path, monkeypatch, capsys):
    exam_path = _seed_simple_exam(tmp_path)
    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: C\n\nDifferent."),
    )

    code = enrich_cli.run_enrich_command([str(exam_path), "--dry-run"])
    assert code == 0

    # No backup file
    assert not list(tmp_path.glob("exam-cli.bak-*.sqlite"))
    # Explanation untouched
    questions = load_questions_from_file(exam_path, "exam-cli")
    assert questions[0].explanation is None

    captured = capsys.readouterr()
    assert "Dry-run mode" in captured.out
    assert "[mismatch]" in captured.out


def test_run_enrich_command_auto_correct_default_flips_db(
    tmp_path, monkeypatch, capsys
):
    exam_path = _seed_simple_exam(tmp_path)
    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: D\n\nReason."),
    )

    code = enrich_cli.run_enrich_command([str(exam_path)])
    assert code == 0

    questions = load_questions_from_file(exam_path, "exam-cli")
    correct = [c.id for c in questions[0].choices if c.is_correct]
    assert correct == ["q-cli-1-d"]


def test_run_enrich_command_no_auto_correct_keeps_db(tmp_path, monkeypatch):
    exam_path = _seed_simple_exam(tmp_path)
    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: D\n\nReason."),
    )

    code = enrich_cli.run_enrich_command(
        [str(exam_path), "--no-auto-correct"]
    )
    assert code == 0

    questions = load_questions_from_file(exam_path, "exam-cli")
    correct = [c.id for c in questions[0].choices if c.is_correct]
    # DB still has B as the correct one
    assert correct == ["q-cli-1-b"]


def test_run_enrich_command_writes_report_when_requested(
    tmp_path, monkeypatch
):
    exam_path = _seed_simple_exam(tmp_path)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: B\n\nMatch."),
    )

    code = enrich_cli.run_enrich_command(
        [str(exam_path), "--report", str(report_path)]
    )
    assert code == 0

    payload = json.loads(report_path.read_text())
    assert payload["total"] == 1
    assert payload["match"] == 1
    assert payload["results"][0]["question_id"] == "q-cli-1"
    assert payload["results"][0]["verdict"] == "match"


def test_run_enrich_command_refuses_when_ai_disabled(
    tmp_path, monkeypatch, capsys
):
    exam_path = _seed_simple_exam(tmp_path)
    disabled = AISettings.default()  # enabled=False by default
    monkeypatch.setattr(enrich_cli, "_load_settings", lambda: disabled)

    code = enrich_cli.run_enrich_command([str(exam_path)])
    assert code == 1
    err = capsys.readouterr().err
    assert "AI chat is disabled" in err


def test_run_enrich_command_returns_1_for_missing_file(tmp_path, capsys):
    code = enrich_cli.run_enrich_command(
        [str(tmp_path / "nope.sqlite")]
    )
    assert code == 1
    assert "file not found" in capsys.readouterr().err


def test_run_enrich_command_skips_already_enriched_by_default(
    tmp_path, monkeypatch, capsys
):
    exam_path = _seed_simple_exam(tmp_path)
    # Pre-mark q-cli-1 as already enriched
    db = open_db(exam_path)
    try:
        db.execute(
            "UPDATE questions SET explanation = ? WHERE id = ?",
            ("## Correct Answer: B\n\nKept.", "q-cli-1"),
        )
        db.commit()
    finally:
        db.close()

    called: list[str] = []

    async def _no_call(
        settings, prompt, *, question_context="", mcp_clients=None, prior_messages=None
    ):
        called.append(question_context)
        yield {"data": "## Correct Answer: D"}
        yield {"complete_messages": []}

    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(enrich_runner, "run_chat_turn", _no_call)

    code = enrich_cli.run_enrich_command([str(exam_path)])
    assert code == 0
    assert called == []  # never invoked the agent

    captured = capsys.readouterr()
    assert "[skip-done]" in captured.out


def test_run_enrich_command_overwrite_forces_reenrichment(
    tmp_path, monkeypatch, capsys
):
    exam_path = _seed_simple_exam(tmp_path)
    db = open_db(exam_path)
    try:
        db.execute(
            "UPDATE questions SET explanation = ? WHERE id = ?",
            ("## Correct Answer: B\n\nOld.", "q-cli-1"),
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn("## Correct Answer: B\n\nFreshly enriched."),
    )

    code = enrich_cli.run_enrich_command(
        [str(exam_path), "--overwrite"]
    )
    assert code == 0

    questions = load_questions_from_file(exam_path, "exam-cli")
    assert "Freshly enriched" in (questions[0].explanation or "")


def test_run_enrich_command_reports_errors_with_exit_code_1(
    tmp_path, monkeypatch
):
    exam_path = _seed_simple_exam(tmp_path)

    async def _raises(
        settings, prompt, *, question_context="", mcp_clients=None, prior_messages=None
    ):
        raise RuntimeError("synthetic boom")
        # Yield to make this a generator (unreachable but pleases analyzer)
        yield {}  # pragma: no cover

    monkeypatch.setattr(enrich_cli, "_load_settings", _make_enabled_settings)
    monkeypatch.setattr(enrich_runner, "run_chat_turn", _raises)

    code = enrich_cli.run_enrich_command([str(exam_path)])
    assert code == 1


def test_dispatcher_routes_enrich_subcommand(tmp_path, monkeypatch):
    """``python -m posrat enrich ...`` must reach the CLI helper."""

    from posrat import __main__ as posrat_main

    captured: dict = {}

    def _fake_runner(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(
        "posrat.ai.enrich_cli.run_enrich_command", _fake_runner
    )

    code = posrat_main.main(["enrich", str(tmp_path / "ex.sqlite"), "--dry-run"])
    assert code == 0
    assert captured["argv"] == [str(tmp_path / "ex.sqlite"), "--dry-run"]


def test_dispatcher_help_includes_enrich(capsys):
    from posrat import __main__ as posrat_main

    code = posrat_main.main(["--help"])
    assert code == 0
    assert "enrich" in capsys.readouterr().err
