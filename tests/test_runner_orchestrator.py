"""Tests for :mod:`posrat.runner.orchestrator`.

The orchestrator wires the sampler + grading + session DAO together,
so tests exercise full end-to-end flows against a real SQLite file
(via ``tmp_path``). Every helper accepts dependency-injection seams
(seeded ``random.Random``, explicit ``session_id`` / ``started_at``)
so assertions stay deterministic.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from posrat.models import Answer, Choice, Exam, Question, Session
from posrat.runner.orchestrator import (
    SessionScore,
    compute_session_score,
    start_runner_session,
    submit_runner_answer,
)
from posrat.storage import create_exam, get_session, list_sessions, open_db


def _seed_exam(
    tmp_path: Path,
    *,
    exam_id: str = "e1",
    question_count: int = 5,
    with_scoring: bool = False,
) -> Path:
    path = tmp_path / f"{exam_id}.sqlite"
    questions = [
        Question(
            id=f"q-{idx}",
            type="single_choice",
            text=f"Question {idx}",
            choices=[
                Choice(
                    id=f"q-{idx}-a",
                    text="A",
                    is_correct=True,
                ),
                Choice(
                    id=f"q-{idx}-b",
                    text="B",
                    is_correct=False,
                ),
            ],
        )
        for idx in range(question_count)
    ]
    kwargs: dict = {
        "id": exam_id,
        "name": exam_id.upper(),
        "questions": questions,
    }
    if with_scoring:
        kwargs["passing_score"] = 700
        kwargs["target_score"] = 1000
    db = open_db(path)
    try:
        create_exam(db, Exam(**kwargs))
    finally:
        db.close()
    return path


def test_start_runner_session_persists_snapshot(tmp_path) -> None:
    """Happy path: session row + sampled ids returned deterministically."""

    path = _seed_exam(tmp_path, question_count=10)

    started = start_runner_session(
        path,
        exam_id="e1",
        mode="exam",
        candidate_name="Alice",
        question_count=4,
        time_limit_minutes=90,
        passing_score=700,
        target_score=1000,
        session_id="s-fixed",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(42),
    )
    assert started.session.id == "s-fixed"
    assert started.session.candidate_name == "Alice"
    assert started.session.question_count == 4
    assert started.session.time_limit_minutes == 90
    assert started.session.passing_score == 700
    assert started.session.target_score == 1000
    assert len(started.question_ids) == 4

    # Same seed + same inputs reproduces the sample.
    second = start_runner_session(
        path,
        exam_id="e1",
        mode="exam",
        candidate_name="Alice",
        question_count=4,
        session_id="s-second",
        started_at="2026-04-23T10:00:01Z",
        rng=random.Random(42),
    )
    assert second.question_ids == started.question_ids

    # DB round-trip keeps the snapshot.
    db = open_db(path)
    try:
        reloaded = get_session(db, "s-fixed")
        assert reloaded is not None
        assert reloaded.candidate_name == "Alice"
        assert reloaded.question_count == 4
    finally:
        db.close()


def test_start_runner_session_clamps_question_count_over_pool(tmp_path) -> None:
    """Asking for more than the pool clamps to the pool size on the snapshot."""

    path = _seed_exam(tmp_path, question_count=3)
    started = start_runner_session(
        path,
        exam_id="e1",
        mode="training",
        candidate_name="dev",
        question_count=100,
        session_id="s-clamp",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(0),
    )
    assert len(started.question_ids) == 3
    assert started.session.question_count == 3


def test_start_runner_session_rejects_empty_exam(tmp_path) -> None:
    """An exam with no questions cannot be run."""

    path = _seed_exam(tmp_path, question_count=0)
    with pytest.raises(ValueError):
        start_runner_session(
            path,
            exam_id="e1",
            mode="exam",
            candidate_name="dev",
        )


def test_start_runner_session_rejects_unknown_exam(tmp_path) -> None:
    """LookupError surfaces verbatim from the DAO layer."""

    path = _seed_exam(tmp_path)
    with pytest.raises(LookupError):
        start_runner_session(
            path,
            exam_id="ghost",
            mode="exam",
            candidate_name="dev",
        )


def test_submit_runner_answer_happy_path(tmp_path) -> None:
    """Correct single_choice pick → is_correct=True + Answer persisted."""

    path = _seed_exam(tmp_path, question_count=3)
    start_runner_session(
        path,
        exam_id="e1",
        mode="training",
        candidate_name="dev",
        question_count=3,
        session_id="s-1",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(0),
    )

    is_correct, given_json = submit_runner_answer(
        path,
        session_id="s-1",
        question_id="q-0",
        payload={"choice_id": "q-0-a"},
        time_ms=1500,
        answer_id="a-1",
    )
    assert is_correct is True
    assert given_json == '{"choice_id":"q-0-a"}'

    # Session reload picks up the recorded answer.
    db = open_db(path)
    try:
        sess = get_session(db, "s-1")
    finally:
        db.close()
    assert sess is not None
    assert len(sess.answers) == 1
    assert sess.answers[0].id == "a-1"
    assert sess.answers[0].is_correct is True
    assert sess.answers[0].time_ms == 1500


def test_submit_runner_answer_records_wrong_answer(tmp_path) -> None:
    """Incorrect payload still persists, but with is_correct=False."""

    path = _seed_exam(tmp_path, question_count=2)
    start_runner_session(
        path,
        exam_id="e1",
        mode="training",
        candidate_name="dev",
        session_id="s-wrong",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(0),
    )
    is_correct, _ = submit_runner_answer(
        path,
        session_id="s-wrong",
        question_id="q-0",
        payload={"choice_id": "q-0-b"},  # wrong
    )
    assert is_correct is False


def test_submit_runner_answer_rejects_unknown_question(tmp_path) -> None:
    path = _seed_exam(tmp_path)
    start_runner_session(
        path,
        exam_id="e1",
        mode="training",
        candidate_name="dev",
        session_id="s-1",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(0),
    )
    with pytest.raises(LookupError):
        submit_runner_answer(
            path,
            session_id="s-1",
            question_id="ghost",
            payload={"choice_id": "c-a"},
        )


def _fake_answers(outcomes: list[bool]) -> list[Answer]:
    """Build deterministic ``Answer`` objects for score tests."""

    return [
        Answer(
            id=f"a-{idx}",
            session_id="s-1",
            question_id=f"q-{idx}",
            given_json='{"choice_id":"c-a"}',
            is_correct=is_correct,
        )
        for idx, is_correct in enumerate(outcomes)
    ]


def _session(**overrides) -> Session:
    kwargs = {
        "id": "s-1",
        "exam_id": "e1",
        "mode": "exam",
        "started_at": "2026-04-23T10:00:00Z",
    }
    kwargs.update(overrides)
    return Session(**kwargs)


def test_compute_session_score_passes_at_threshold() -> None:
    """70 % of 1000 = 700, which meets passing_score=700 → passed=True."""

    session = _session(passing_score=700, target_score=1000)
    answers = _fake_answers([True] * 7 + [False] * 3)

    score = compute_session_score(session, answers=answers)
    assert isinstance(score, SessionScore)
    assert score.correct_count == 7
    assert score.total_count == 10
    assert score.percent == pytest.approx(70.0)
    assert score.raw_score == 700
    assert score.passed is True


def test_compute_session_score_fails_below_threshold() -> None:
    """69.9 % rounds down to 699 raw points → below 700 → passed=False."""

    session = _session(passing_score=700, target_score=1000)
    # 699 / 1000 correct = 69.9 %
    answers = _fake_answers([True] * 699 + [False] * 301)

    score = compute_session_score(session, answers=answers)
    assert score.correct_count == 699
    assert score.raw_score == 699
    assert score.passed is False


def test_compute_session_score_no_passing_criterion() -> None:
    """Without passing_score/target_score, ``passed`` is ``None``."""

    session = _session()
    answers = _fake_answers([True, False, True])

    score = compute_session_score(session, answers=answers)
    assert score.correct_count == 2
    assert score.total_count == 3
    assert score.passed is None
    assert score.raw_score is None


def test_compute_session_score_empty_session() -> None:
    """No recorded answers → every derived field is None/0."""

    session = _session(passing_score=700, target_score=1000)
    score = compute_session_score(session, answers=[])
    assert score.correct_count == 0
    assert score.total_count == 0
    assert score.percent is None
    assert score.raw_score is None
    assert score.passed is None


def test_compute_session_score_reads_session_answers_by_default() -> None:
    """When ``answers`` is not passed, the helper uses ``session.answers``."""

    session = _session(
        passing_score=500,
        target_score=1000,
        answers=_fake_answers([True, True, True, False, False]),
    )
    score = compute_session_score(session)
    assert score.correct_count == 3
    assert score.raw_score == 600
    assert score.passed is True


# --------------------------------------------------------------------------- #
# Bug-fix regression tests (2026-04-23) — per-question all-or-nothing scoring #
# --------------------------------------------------------------------------- #


def test_compute_session_score_denominator_uses_session_question_count() -> None:
    """Unanswered questions must count toward the total, not be ignored.

    Session was started with ``question_count=10`` (pinned snapshot).
    After answering 4 questions (all correct) the score is 4/10 = 40 %,
    not 4/4 = 100 %. This is the VCE behaviour: walking away from a
    session half-way does not accidentally inflate your percentage.
    """

    session = _session(question_count=10, passing_score=700, target_score=1000)
    answers = _fake_answers([True, True, True, True])  # 4 answered, all correct

    score = compute_session_score(session, answers=answers)
    assert score.correct_count == 4
    assert score.total_count == 10
    assert score.percent == pytest.approx(40.0)
    # raw_score = 40% of 1000 = 400 → below 700 → failed
    assert score.raw_score == 400
    assert score.passed is False


def test_compute_session_score_deduplicates_per_question_id() -> None:
    """Duplicate answer rows for the same question count only once.

    record_answer already replaces on re-submit, but defensively the
    scorer dedupes so hand-edited / legacy DBs don't tilt the tally.
    The later submission wins (matches dict insertion-order semantics
    when the same key is re-assigned).
    """

    # 3 raw rows for q-0 (evolving answers), 1 row for q-1.
    answers = [
        Answer(
            id="a-0",
            session_id="s-1",
            question_id="q-0",
            given_json='{"choice_id":"c-a"}',
            is_correct=False,  # early submission: wrong
        ),
        Answer(
            id="a-0b",
            session_id="s-1",
            question_id="q-0",
            given_json='{"choice_id":"c-b"}',
            is_correct=True,  # resubmission: right
        ),
        Answer(
            id="a-1",
            session_id="s-1",
            question_id="q-1",
            given_json='{"choice_id":"c-a"}',
            is_correct=False,
        ),
    ]

    session = _session(question_count=2)
    score = compute_session_score(session, answers=answers)
    assert score.total_count == 2
    # Latest submission wins: q-0 correct + q-1 wrong = 1/2.
    assert score.correct_count == 1
    assert score.percent == pytest.approx(50.0)


def test_record_answer_replaces_on_resubmit(tmp_path) -> None:
    """The DAO replaces (not accumulates) per ``(session_id, question_id)``."""

    from posrat.runner.orchestrator import start_runner_session, submit_runner_answer

    path = _seed_exam(tmp_path, question_count=3)
    start_runner_session(
        path,
        exam_id="e1",
        mode="training",
        candidate_name="dev",
        question_count=3,
        session_id="s-resubmit",
        started_at="2026-04-23T10:00:00Z",
        rng=random.Random(0),
    )

    # First submission: wrong pick.
    submit_runner_answer(
        path,
        session_id="s-resubmit",
        question_id="q-0",
        payload={"choice_id": "q-0-b"},  # wrong
    )
    # Resubmission: correct pick.
    submit_runner_answer(
        path,
        session_id="s-resubmit",
        question_id="q-0",
        payload={"choice_id": "q-0-a"},  # correct
    )

    # Only one answer row should remain on disk for (s-resubmit, q-0).
    db = open_db(path)
    try:
        rows = db.execute(
            "SELECT id, is_correct FROM answers"
            " WHERE session_id = ? AND question_id = ?",
            ("s-resubmit", "q-0"),
        ).fetchall()
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0]["is_correct"] == 1  # the later, correct answer won
