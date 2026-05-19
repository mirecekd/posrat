"""Tests for :mod:`posrat.ai.enrich_runner` (async orchestration) and
:mod:`posrat.ai.enrich_persistence` (disk-side write helpers + backup).

The orchestrator monkey-patches :func:`posrat.ai.agent.run_chat_turn`
with a deterministic async generator so the whole pipeline runs
without Bedrock / MCP / network. The persistence layer hits real
SQLite files in ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from posrat.ai import enrich_persistence, enrich_runner
from posrat.ai.config import AISettings
from posrat.ai.enrich import EnrichResult
from posrat.designer.browser import (
    create_exam_file,
    load_questions_from_file,
)
from posrat.models import Choice, Hotspot, HotspotOption, HotspotStep, Question
from posrat.storage import add_question, open_db


# ---------------------------------------------------------------------------
# Helpers


def _make_settings() -> AISettings:
    return AISettings(
        enabled=True,
        model_id="anthropic.claude-3",
        region="eu-west-1",
        system_prompt=None,
        mcp_config_json=None,
        enrich_prompt=None,
        updated_at=None,
    )


def _seed_exam(tmp_path: Path, *, with_hotspot: bool = False) -> Path:
    exam_path = create_exam_file(
        tmp_path, "exam-bulk", "Bulk Exam", description=None
    )
    db = open_db(exam_path)
    try:
        add_question(
            db,
            "exam-bulk",
            Question(
                id="q-1",
                type="single_choice",
                text="Q1?",
                explanation=None,
                choices=[
                    Choice(id="q-1-a", text="A", is_correct=False),
                    Choice(id="q-1-b", text="B", is_correct=True),
                    Choice(id="q-1-c", text="C", is_correct=False),
                    Choice(id="q-1-d", text="D", is_correct=False),
                ],
            ),
        )
        add_question(
            db,
            "exam-bulk",
            Question(
                id="q-2",
                type="multi_choice",
                text="Q2?",
                explanation=(
                    "Old prose.\n\n"
                    "Community vote distribution\n"
                    "B (60%)\nC (40%)\n"
                ),
                choices=[
                    Choice(id="q-2-a", text="A", is_correct=False),
                    Choice(id="q-2-b", text="B", is_correct=True),
                    Choice(id="q-2-c", text="C", is_correct=True),
                    Choice(id="q-2-d", text="D", is_correct=False),
                ],
            ),
        )
        if with_hotspot:
            add_question(
                db,
                "exam-bulk",
                Question(
                    id="q-3",
                    type="hotspot",
                    text="Hotspot?",
                    hotspot=Hotspot(
                        options=[HotspotOption(id="opt-yes", text="Yes")],
                        steps=[
                            HotspotStep(
                                id="s1",
                                prompt="Step 1",
                                correct_option_id="opt-yes",
                            )
                        ],
                    ),
                ),
            )
    finally:
        db.close()
    return exam_path


def _stub_run_chat_turn(reply_by_question_text: dict[str, str]):
    """Build a fake :func:`run_chat_turn` async generator factory.

    Picks the canned reply by inspecting the ``question_context``
    kwarg (which carries the ``Q: <text>`` line built by
    :func:`build_question_context`). Lets each test decide what the
    "AI" returns for each question without touching any real model.
    """

    async def _fake_run_chat_turn(
        settings,
        user_prompt,
        *,
        question_context="",
        mcp_clients=None,
        prior_messages=None,
    ):
        for needle, reply in reply_by_question_text.items():
            if f"Q: {needle}" in question_context:
                # Yield the reply in three chunks so the buffer
                # concatenation path is exercised.
                mid = max(1, len(reply) // 2)
                yield {"data": reply[:mid]}
                yield {"data": reply[mid:]}
                yield {"complete_messages": []}
                return
        yield {"data": ""}
        yield {"complete_messages": []}

    return _fake_run_chat_turn


# ---------------------------------------------------------------------------
# Backup


def test_make_backup_creates_timestamped_copy(tmp_path):
    src = tmp_path / "exam.sqlite"
    src.write_bytes(b"PRAGMA hello;")
    fixed_now = datetime(2026, 5, 19, 12, 30, 45, tzinfo=timezone.utc)
    backup = enrich_persistence.make_backup(src, now=fixed_now)
    assert backup.name == "exam.bak-20260519-123045.sqlite"
    assert backup.read_bytes() == b"PRAGMA hello;"
    # Source intact
    assert src.read_bytes() == b"PRAGMA hello;"


def test_make_backup_raises_when_source_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        enrich_persistence.make_backup(tmp_path / "nope.sqlite")


def test_make_backup_raises_on_collision(tmp_path):
    src = tmp_path / "exam.sqlite"
    src.write_bytes(b"x")
    fixed_now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    enrich_persistence.make_backup(src, now=fixed_now)
    with pytest.raises(FileExistsError):
        enrich_persistence.make_backup(src, now=fixed_now)


# ---------------------------------------------------------------------------
# Exam loading


def test_resolve_exam_id_returns_id(tmp_path):
    exam_path = _seed_exam(tmp_path)
    assert enrich_persistence.resolve_exam_id(exam_path) == "exam-bulk"


def test_load_exam_questions_returns_in_order(tmp_path):
    exam_path = _seed_exam(tmp_path)
    questions = enrich_persistence.load_exam_questions(exam_path)
    assert [q.id for q in questions] == ["q-1", "q-2"]


# ---------------------------------------------------------------------------
# update_choice_correctness_in_file


def test_update_choice_correctness_flips_flags(tmp_path):
    exam_path = _seed_exam(tmp_path)
    changed = enrich_persistence.update_choice_correctness_in_file(
        exam_path, "q-1", ["C"]
    )
    assert changed is True

    questions = load_questions_from_file(exam_path, "exam-bulk")
    q1 = next(q for q in questions if q.id == "q-1")
    correct_ids = [c.id for c in q1.choices if c.is_correct]
    assert correct_ids == ["q-1-c"]
    # Texts preserved
    texts = [c.text for c in q1.choices]
    assert texts == ["A", "B", "C", "D"]


def test_update_choice_correctness_returns_false_for_unknown(tmp_path):
    exam_path = _seed_exam(tmp_path)
    changed = enrich_persistence.update_choice_correctness_in_file(
        exam_path, "q-missing", ["A"]
    )
    assert changed is False


def test_update_choice_correctness_noop_when_already_correct(tmp_path):
    exam_path = _seed_exam(tmp_path)
    # q-1 already has B correct — passing ["B"] should not write.
    changed = enrich_persistence.update_choice_correctness_in_file(
        exam_path, "q-1", ["B"]
    )
    assert changed is False


def test_update_choice_correctness_rejects_empty_letters(tmp_path):
    exam_path = _seed_exam(tmp_path)
    with pytest.raises(ValueError):
        enrich_persistence.update_choice_correctness_in_file(
            exam_path, "q-1", []
        )


def test_update_choice_correctness_handles_multi(tmp_path):
    exam_path = _seed_exam(tmp_path)
    # q-2 starts B+C correct. Switch to A+D.
    enrich_persistence.update_choice_correctness_in_file(
        exam_path, "q-2", ["A", "D"]
    )
    questions = load_questions_from_file(exam_path, "exam-bulk")
    q2 = next(q for q in questions if q.id == "q-2")
    correct_ids = sorted(c.id for c in q2.choices if c.is_correct)
    assert correct_ids == ["q-2-a", "q-2-d"]


# ---------------------------------------------------------------------------
# persist_enrich_result


def test_persist_enrich_result_writes_explanation_on_match(tmp_path):
    exam_path = _seed_exam(tmp_path)
    result = EnrichResult(
        question_id="q-1",
        verdict="match",
        db_letters=["B"],
        ai_letters=["B"],
        new_explanation="## Correct Answer: B\n\nBody.",
    )
    enrich_persistence.persist_enrich_result(
        exam_path, result, auto_correct_mismatches=True
    )
    questions = load_questions_from_file(exam_path, "exam-bulk")
    q1 = next(q for q in questions if q.id == "q-1")
    assert q1.explanation == "## Correct Answer: B\n\nBody."
    # is_correct unchanged
    correct_ids = [c.id for c in q1.choices if c.is_correct]
    assert correct_ids == ["q-1-b"]


def test_persist_enrich_result_auto_corrects_mismatch(tmp_path):
    exam_path = _seed_exam(tmp_path)
    result = EnrichResult(
        question_id="q-1",
        verdict="mismatch",
        db_letters=["B"],
        ai_letters=["C"],
        new_explanation="## Correct Answer: C\n\nReason.",
    )
    enrich_persistence.persist_enrich_result(
        exam_path, result, auto_correct_mismatches=True
    )
    q1 = next(
        q
        for q in load_questions_from_file(exam_path, "exam-bulk")
        if q.id == "q-1"
    )
    correct_ids = [c.id for c in q1.choices if c.is_correct]
    assert correct_ids == ["q-1-c"]
    assert q1.explanation.startswith("## Correct Answer: C")


def test_persist_enrich_result_keeps_db_when_auto_correct_off(tmp_path):
    exam_path = _seed_exam(tmp_path)
    result = EnrichResult(
        question_id="q-1",
        verdict="mismatch",
        db_letters=["B"],
        ai_letters=["C"],
        new_explanation="## Correct Answer: C\n\nReason.",
    )
    enrich_persistence.persist_enrich_result(
        exam_path, result, auto_correct_mismatches=False
    )
    q1 = next(
        q
        for q in load_questions_from_file(exam_path, "exam-bulk")
        if q.id == "q-1"
    )
    # Explanation updated, but is_correct flags stay on B
    correct_ids = [c.id for c in q1.choices if c.is_correct]
    assert correct_ids == ["q-1-b"]
    assert q1.explanation.startswith("## Correct Answer: C")


def test_persist_enrich_result_skips_for_skipped_verdict(tmp_path):
    exam_path = _seed_exam(tmp_path)
    result = EnrichResult(
        question_id="q-1",
        verdict="skipped_hotspot",
    )
    enrich_persistence.persist_enrich_result(
        exam_path, result, auto_correct_mismatches=True
    )
    q1 = next(
        q
        for q in load_questions_from_file(exam_path, "exam-bulk")
        if q.id == "q-1"
    )
    assert q1.explanation is None


# ---------------------------------------------------------------------------
# enrich_runner.enrich_questions


def test_enrich_questions_pipeline_match_and_mismatch(tmp_path, monkeypatch):
    exam_path = _seed_exam(tmp_path)
    questions = enrich_persistence.load_exam_questions(exam_path)

    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn(
            {
                "Q1?": "## Correct Answer: B\n\n**Bingo.**\n\nBody.",
                # AI disagrees with our DB on q-2 (DB=B+C, AI=A+D)
                "Q2?": "## Correct Answer: A, D\n\n**Different.**",
            }
        ),
    )

    progress_calls: list[tuple[int, int, EnrichResult]] = []

    def _progress(idx, total, result):
        progress_calls.append((idx, total, result))

    summary = asyncio.run(
        enrich_runner.enrich_questions(
            _make_settings(),
            questions,
            overwrite_already_enriched=False,
            progress=_progress,
        )
    )

    assert summary.total == 2
    assert summary.matches == 1
    assert summary.mismatches == 1
    assert progress_calls[0][2].question_id == "q-1"
    assert progress_calls[0][2].verdict == "match"
    assert progress_calls[1][2].verdict == "mismatch"
    # q-2 carried a community vote — its new_explanation should
    # contain the appended summary sentence.
    q2_result = progress_calls[1][2]
    assert "Community vote: B" in (q2_result.new_explanation or "")


def test_enrich_questions_skips_already_enriched_by_default(tmp_path, monkeypatch):
    exam_path = _seed_exam(tmp_path)
    # Pre-mark q-1 as already enriched.
    db = open_db(exam_path)
    try:
        db.execute(
            "UPDATE questions SET explanation = ? WHERE id = ?",
            ("## Correct Answer: B\n\nKept.", "q-1"),
        )
        db.commit()
    finally:
        db.close()

    questions = enrich_persistence.load_exam_questions(exam_path)

    call_log: list[str] = []

    def _stub(reply_map):
        async def _runner(settings, prompt, *, question_context, mcp_clients=None, prior_messages=None):
            call_log.append(question_context)
            for needle, reply in reply_map.items():
                if f"Q: {needle}" in question_context:
                    yield {"data": reply}
                    yield {"complete_messages": []}
                    return
            yield {"data": ""}
            yield {"complete_messages": []}

        return _runner

    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub({"Q2?": "## Correct Answer: B, C\n\nMatch."}),
    )

    summary = asyncio.run(
        enrich_runner.enrich_questions(
            _make_settings(), questions, overwrite_already_enriched=False
        )
    )
    verdicts = [r.verdict for r in summary.results]
    assert "skipped_already_enriched" in verdicts
    assert summary.skipped_already_enriched == 1
    # The stub must have been called only for q-2, not q-1
    assert all("Q1?" not in ctx for ctx in call_log)


def test_enrich_questions_skips_hotspot(tmp_path, monkeypatch):
    exam_path = _seed_exam(tmp_path, with_hotspot=True)
    questions = enrich_persistence.load_exam_questions(exam_path)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn(
            {
                "Q1?": "## Correct Answer: B\n\nMatch.",
                "Q2?": "## Correct Answer: B, C\n\nMatch.",
            }
        ),
    )
    summary = asyncio.run(
        enrich_runner.enrich_questions(_make_settings(), questions)
    )
    hotspot_results = [r for r in summary.results if r.question_id == "q-3"]
    assert hotspot_results
    assert hotspot_results[0].verdict == "skipped_hotspot"
    assert summary.skipped_hotspot == 1


def test_enrich_questions_handles_empty_reply_as_error(tmp_path, monkeypatch):
    exam_path = _seed_exam(tmp_path)
    questions = enrich_persistence.load_exam_questions(exam_path)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn({}),  # no canned replies → empty stream
    )
    summary = asyncio.run(
        enrich_runner.enrich_questions(_make_settings(), questions)
    )
    assert summary.errors == 2
    assert all(r.verdict == "error" for r in summary.results)


def test_enrich_questions_persist_callback_writes_to_disk(tmp_path, monkeypatch):
    exam_path = _seed_exam(tmp_path)
    questions = enrich_persistence.load_exam_questions(exam_path)
    monkeypatch.setattr(
        enrich_runner,
        "run_chat_turn",
        _stub_run_chat_turn(
            {
                "Q1?": "## Correct Answer: B\n\nMatch.",
                "Q2?": "## Correct Answer: B, C\n\nMatch.",
            }
        ),
    )

    async def _persist(result):
        enrich_persistence.persist_enrich_result(
            exam_path, result, auto_correct_mismatches=True
        )

    summary = asyncio.run(
        enrich_runner.enrich_questions(
            _make_settings(),
            questions,
            persist=_persist,
        )
    )
    assert summary.matches == 2
    questions_after = enrich_persistence.load_exam_questions(exam_path)
    explanations = {q.id: q.explanation for q in questions_after}
    assert explanations["q-1"].startswith("## Correct Answer: B")
    assert explanations["q-2"].startswith("## Correct Answer: B, C")
    # q-2 had community vote → summary appended at the end.
    assert "Community vote: B" in explanations["q-2"]
