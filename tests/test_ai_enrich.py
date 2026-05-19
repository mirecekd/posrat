"""Tests for :mod:`posrat.ai.enrich` — pure helpers used by the
``python -m posrat enrich`` bulk runner.

The runner module itself (``enrich_runner``) wires Bedrock + Strands +
MCP together and is exercised separately with monkey-patched stubs.
This file covers the deterministic string- and model-level helpers:

* community-vote extraction / stripping / summarising,
* "already enriched" detection,
* DB ↔ AI letter parsing + verdict classification,
* prompt addendum assembly.

Every test runs without network / DB / NiceGUI.
"""

from __future__ import annotations

import pytest

from posrat.ai.enrich import (
    CommunityVote,
    ENRICHED_EXPLANATION_PREFIX,
    EnrichResult,
    append_community_vote_summary,
    build_enrich_user_prompt,
    classify_verdict,
    derive_db_correct_letters,
    extract_community_vote,
    is_already_enriched,
    parse_correct_letters_from_reply,
    strip_community_vote,
    summarize_community_vote,
)
from posrat.models import Choice, Question


# ---------------------------------------------------------------------------
# Community vote parsing


def test_extract_community_vote_returns_none_for_blank_inputs():
    assert extract_community_vote(None) is None
    assert extract_community_vote("") is None
    assert extract_community_vote("   \n\n  ") is None


def test_extract_community_vote_returns_none_when_header_missing():
    assert extract_community_vote("Just a regular explanation.") is None


def test_extract_community_vote_parses_paren_format():
    text = (
        "Some justification.\n\n"
        "Community vote distribution\n"
        "B (98%)\n"
        "C (2%)\n"
    )
    vote = extract_community_vote(text)
    assert vote is not None
    assert vote.distribution == {"B": 98, "C": 2}
    assert vote.top_letter == "B"
    assert vote.top_percent == 98


def test_extract_community_vote_parses_pipe_format():
    # HTML importer sometimes joins votes with " | " separators.
    text = "Community vote distribution | B (75%) | A (25%)"
    vote = extract_community_vote(text)
    assert vote is not None
    assert vote.distribution == {"B": 75, "A": 25}
    assert vote.top_letter == "B"


def test_extract_community_vote_returns_none_when_block_has_no_votes():
    text = "Community vote distribution\n(no consensus)\n"
    assert extract_community_vote(text) is None


def test_extract_community_vote_picks_highest_top_letter():
    # Even when the parser sees A first, max-by-percent must win.
    text = "Community vote distribution\nA (10%)\nC (60%)\nB (30%)\n"
    vote = extract_community_vote(text)
    assert vote is not None
    assert vote.top_letter == "C"
    assert vote.top_percent == 60


# ---------------------------------------------------------------------------
# Stripping


def test_strip_community_vote_returns_none_for_none_input():
    assert strip_community_vote(None) is None


def test_strip_community_vote_keeps_explanation_without_block():
    text = "Just a single paragraph."
    assert strip_community_vote(text) == text


def test_strip_community_vote_removes_block_at_end():
    text = (
        "Some justification.\n\n"
        "Community vote distribution\n"
        "B (98%)\n"
        "C (2%)\n"
    )
    stripped = strip_community_vote(text)
    assert stripped == "Some justification."


def test_strip_community_vote_removes_block_at_start():
    text = "Community vote distribution\nB (98%)\n"
    stripped = strip_community_vote(text)
    assert stripped == ""


# ---------------------------------------------------------------------------
# Summarising


def test_summarize_community_vote_overwhelming_top():
    vote = CommunityVote(distribution={"B": 98, "C": 2}, top_letter="B", top_percent=98)
    out = summarize_community_vote(vote)
    assert out == "Community vote: B (98%) overwhelmingly preferred."


def test_summarize_community_vote_majority_with_runner_up():
    vote = CommunityVote(distribution={"B": 62, "A": 38}, top_letter="B", top_percent=62)
    out = summarize_community_vote(vote)
    assert out == "Community vote: B (62%) leading; A trails at 38%."


def test_summarize_community_vote_majority_without_runner_up():
    vote = CommunityVote(distribution={"B": 60}, top_letter="B", top_percent=60)
    out = summarize_community_vote(vote)
    assert out == "Community vote: B (60%) leading."


def test_summarize_community_vote_three_way_split():
    vote = CommunityVote(
        distribution={"B": 45, "A": 40, "C": 15},
        top_letter="B",
        top_percent=45,
    )
    out = summarize_community_vote(vote)
    assert out == "Community vote split: B (45%), A (40%), C (15%)."


def test_summarize_community_vote_contains_no_unicode_dashes():
    vote = CommunityVote(distribution={"B": 62, "A": 38}, top_letter="B", top_percent=62)
    out = summarize_community_vote(vote)
    assert "\u2013" not in out  # en-dash
    assert "\u2014" not in out  # em-dash


# ---------------------------------------------------------------------------
# Already-enriched detection


def test_is_already_enriched_false_for_blank():
    assert is_already_enriched(None) is False
    assert is_already_enriched("") is False
    assert is_already_enriched("   ") is False


def test_is_already_enriched_true_for_template_prefix():
    text = f"{ENRICHED_EXPLANATION_PREFIX} D\n\n**Disable user...**"
    assert is_already_enriched(text) is True


def test_is_already_enriched_tolerates_leading_whitespace():
    text = f"\n  {ENRICHED_EXPLANATION_PREFIX} D"
    assert is_already_enriched(text) is True


def test_is_already_enriched_false_for_legacy_explanation():
    text = "Community vote distribution\nB (98%)"
    assert is_already_enriched(text) is False


# ---------------------------------------------------------------------------
# DB letter derivation


def _question(question_type: str, choices: list[tuple[str, bool]]) -> Question:
    """Tiny helper to build a single/multi choice ``Question`` for tests."""
    return Question(
        id="q-test",
        type=question_type,
        text="Sample question text.",
        choices=[
            Choice(id=f"q-test-c-{idx}", text=f"Choice {idx}", is_correct=correct)
            for idx, (_letter, correct) in enumerate(choices)
        ],
    )


def test_derive_db_correct_letters_single_choice():
    q = _question(
        "single_choice",
        [("A", False), ("B", True), ("C", False), ("D", False)],
    )
    assert derive_db_correct_letters(q) == ["B"]


def test_derive_db_correct_letters_multi_choice():
    q = _question(
        "multi_choice",
        [("A", True), ("B", False), ("C", True), ("D", False)],
    )
    assert derive_db_correct_letters(q) == ["A", "C"]


def test_derive_db_correct_letters_hotspot_returns_empty():
    # Hotspot questions don't fit the letter scheme; the runner skips
    # them, but the helper must not raise.
    from posrat.models import Hotspot, HotspotOption, HotspotStep

    q = Question(
        id="q-hot",
        type="hotspot",
        text="Hotspot prompt.",
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
    )
    assert derive_db_correct_letters(q) == []


# ---------------------------------------------------------------------------
# AI letter parsing


def test_parse_correct_letters_from_reply_blank():
    assert parse_correct_letters_from_reply(None) == []
    assert parse_correct_letters_from_reply("") == []


def test_parse_correct_letters_from_reply_single_letter():
    reply = "## Correct Answer: D\n\n**Disable the IAM user.**"
    assert parse_correct_letters_from_reply(reply) == ["D"]


def test_parse_correct_letters_from_reply_compact_multi():
    reply = "## Correct Answer: BC\n\nBoth options together..."
    assert parse_correct_letters_from_reply(reply) == ["B", "C"]


def test_parse_correct_letters_from_reply_comma_separated():
    reply = "## Correct Answer: B, C\n\n..."
    assert parse_correct_letters_from_reply(reply) == ["B", "C"]


def test_parse_correct_letters_from_reply_and_separator():
    reply = "## Correct Answer: B and D\n\n..."
    assert parse_correct_letters_from_reply(reply) == ["B", "D"]


def test_parse_correct_letters_from_reply_three_way():
    reply = "## Correct Answer: B, D, E\n\n..."
    assert parse_correct_letters_from_reply(reply) == ["B", "D", "E"]


def test_parse_correct_letters_from_reply_returns_empty_on_missing_heading():
    reply = "Some prose without a heading."
    assert parse_correct_letters_from_reply(reply) == []


def test_parse_correct_letters_from_reply_deduplicates():
    reply = "## Correct Answer: B B C"
    assert parse_correct_letters_from_reply(reply) == ["B", "C"]


# ---------------------------------------------------------------------------
# Verdict classification


def test_classify_verdict_match():
    assert classify_verdict(["B"], ["B"]) == "match"


def test_classify_verdict_match_order_insensitive():
    assert classify_verdict(["B", "C"], ["C", "B"]) == "match"


def test_classify_verdict_mismatch():
    assert classify_verdict(["B"], ["C"]) == "mismatch"


def test_classify_verdict_unknown_when_ai_blank():
    assert classify_verdict(["B"], []) == "unknown"


# ---------------------------------------------------------------------------
# Community vote append


def test_append_community_vote_summary_noop_for_none():
    reply = "## Correct Answer: B\n\nBody"
    assert append_community_vote_summary(reply, None) == reply


def test_append_community_vote_summary_appends_blank_line_then_sentence():
    reply = "## Correct Answer: B\n\nBody."
    vote = CommunityVote(distribution={"B": 98, "C": 2}, top_letter="B", top_percent=98)
    out = append_community_vote_summary(reply, vote)
    assert out.endswith("Community vote: B (98%) overwhelmingly preferred.\n")
    # Blank-line separator between body and summary.
    assert "Body.\n\nCommunity vote:" in out


# ---------------------------------------------------------------------------
# Prompt assembly


def test_build_enrich_user_prompt_includes_db_letters():
    out = build_enrich_user_prompt(
        "Base prompt",
        db_letters=["B"],
        has_community_vote=False,
    )
    assert "Base prompt" in out
    assert "marks B as the correct answer." in out
    assert "Verify this against AWS documentation." in out


def test_build_enrich_user_prompt_pluralises_multi_correct():
    out = build_enrich_user_prompt(
        "Base prompt",
        db_letters=["B", "C"],
        has_community_vote=False,
    )
    assert "marks B, C as the correct answers." in out


def test_build_enrich_user_prompt_handles_no_correct_marked():
    out = build_enrich_user_prompt(
        "Base prompt",
        db_letters=[],
        has_community_vote=False,
    )
    assert "does not have a correct answer marked yet" in out


def test_build_enrich_user_prompt_mentions_community_vote_when_present():
    out = build_enrich_user_prompt(
        "Base prompt",
        db_letters=["B"],
        has_community_vote=True,
    )
    assert "community-vote distribution" in out
    assert "do not include it" in out


def test_build_enrich_user_prompt_skips_community_section_when_absent():
    out = build_enrich_user_prompt(
        "Base prompt",
        db_letters=["B"],
        has_community_vote=False,
    )
    assert "community-vote distribution" not in out


# ---------------------------------------------------------------------------
# EnrichResult dataclass


def test_enrich_result_defaults():
    result = EnrichResult(question_id="q-1", verdict="skipped_hotspot")
    assert result.db_letters == []
    assert result.ai_letters == []
    assert result.new_explanation is None
    assert result.error_message is None


def test_enrich_result_is_frozen():
    result = EnrichResult(question_id="q-1", verdict="match")
    with pytest.raises(Exception):  # FrozenInstanceError
        result.verdict = "mismatch"  # type: ignore[misc]
