"""Tests for :mod:`posrat.ai.context`.build_question_context."""

from __future__ import annotations

from posrat.ai.context import build_question_context
from posrat.models import Choice, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep


def _single_choice() -> Question:
    return Question(
        id="q1",
        type="single_choice",
        text="Which service stores objects?",
        explanation="S3 is object storage.",
        section="Storage",
        complexity=2,
        choices=[
            Choice(id="a", text="S3", is_correct=True),
            Choice(id="b", text="EBS", is_correct=False),
            Choice(id="c", text="EFS", is_correct=False),
        ],
    )


def _multi_choice() -> Question:
    return Question(
        id="q2",
        type="multi_choice",
        text="Which are AWS compute services?",
        choices=[
            Choice(id="a", text="EC2", is_correct=True),
            Choice(id="b", text="Lambda", is_correct=True),
            Choice(id="c", text="RDS", is_correct=False),
        ],
    )


def _hotspot() -> Question:
    return Question(
        id="q3",
        type="hotspot",
        text="Match the service to its category.",
        explanation="S3=storage, Lambda=compute.",
        hotspot=Hotspot(
            options=[
                HotspotOption(id="o1", text="Storage"),
                HotspotOption(id="o2", text="Compute"),
            ],
            steps=[
                HotspotStep(id="s1", prompt="S3", correct_option_id="o1"),
                HotspotStep(
                    id="s2", prompt="Lambda", correct_option_id="o2"
                ),
            ],
        ),
    )


def test_none_returns_empty_string():
    assert build_question_context(None) == ""


def test_single_choice_includes_correct_marker_and_explanation():
    out = build_question_context(_single_choice())
    assert "Which service stores objects?" in out
    assert "A. S3 [correct]" in out
    assert "B. EBS" in out
    assert "[correct]" not in out.split("B. EBS")[1].split("\n")[0]
    assert "Explanation" in out
    assert "S3 is object storage." in out
    assert "Section: Storage" in out
    assert "Complexity: 2/5" in out


def test_single_choice_hides_answers_when_include_false():
    out = build_question_context(_single_choice(), include_answers=False)
    assert "[correct]" not in out
    assert "Explanation" not in out
    assert "S3 is object storage." not in out
    # Choices themselves must still be visible so the LLM sees the options.
    assert "A. S3" in out
    assert "B. EBS" in out


def test_multi_choice_marks_all_correct():
    out = build_question_context(_multi_choice())
    assert "A. EC2 [correct]" in out
    assert "B. Lambda [correct]" in out
    # RDS wrong → no marker.
    rds_line = [ln for ln in out.splitlines() if "RDS" in ln][0]
    assert "[correct]" not in rds_line


def test_hotspot_renders_options_and_steps_with_correct():
    out = build_question_context(_hotspot())
    assert "Options pool:" in out
    assert "- Storage" in out
    assert "- Compute" in out
    assert "Steps:" in out
    assert "1. S3" in out
    assert "correct: Storage" in out
    assert "2. Lambda" in out
    assert "correct: Compute" in out


def test_hotspot_hides_correct_when_include_false():
    out = build_question_context(_hotspot(), include_answers=False)
    assert "correct:" not in out
    # Options pool and step prompts still visible.
    assert "- Storage" in out
    assert "1. S3" in out


def test_optional_fields_omitted_when_absent():
    q = Question(
        id="q4",
        type="single_choice",
        text="Plain question.",
        choices=[
            Choice(id="a", text="yes", is_correct=True),
            Choice(id="b", text="no", is_correct=False),
        ],
    )
    out = build_question_context(q)
    assert "Section:" not in out
    assert "Complexity:" not in out
    assert "Explanation" not in out
