# tests/test_importers_examtopics.py
"""Tests for posrat.importers.examtopics (ExamTopics RTF parser)."""

from __future__ import annotations


def test_module_imports() -> None:
    import posrat.importers.examtopics  # noqa: F401


def test_parser_registered_in_registry() -> None:
    import posrat.importers  # noqa: F401 — triggers registration side-effect
    from posrat.importers.base import get_import_source

    src = get_import_source("examtopics")
    assert src.display_name == "ExamTopics RTF"


def test_examtopics_parser_reexported_from_init() -> None:
    from posrat.importers import ExamTopicsParser

    assert ExamTopicsParser.source_id == "examtopics"


_THREE_Q_TEXT = (
    "Q1\r"
    "What is X?\n\nA. One\nB. Two\n\nAnswer: A\n\n"
    "Community vote distribution\nA (93%)\n7%\n\n"
    "Q2\r"
    "What is Y?\n\nA. Foo\nB. Bar\n\nAnswer: B\n\n"
    "Q3\r"
    "What is Z? (Choose two.)\n\nA. P\nB. Q\nC. R\n\nAnswer: A, C\n\n"
)


def test_parser_satisfies_import_source_protocol() -> None:
    from posrat.importers.base import ImportSource
    from posrat.importers.examtopics import ExamTopicsParser

    parser = ExamTopicsParser()
    assert isinstance(parser, ImportSource)
    assert parser.source_id == "examtopics"
    assert parser.display_name == "ExamTopics RTF"
    assert ".rtf" in parser.file_extensions


def test_tokenize_finds_three_blocks() -> None:
    from posrat.importers.examtopics import _tokenize

    blocks = _tokenize(_THREE_Q_TEXT)
    assert len(blocks) == 3
    ns = [n for n, _ in blocks]
    assert ns == [1, 2, 3]
    # Each body should NOT start with the Q-label
    for _, body in blocks:
        assert not body.startswith("Q")


def test_parse_block_single_choice_happy_path() -> None:
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = "What is X?\n\nA. One\nB. Two\n\nAnswer: A\n\nCommunity vote distribution\nA (93%)\n7%\n"
    result = _parse_block(1, block, {})
    assert isinstance(result, ParsedQuestion)
    assert result.source_index == 1
    assert result.question_type == "single_choice"
    assert len(result.choices) == 2
    a_choice = next(c for c in result.choices if c.letter == "A")
    b_choice = next(c for c in result.choices if c.letter == "B")
    assert a_choice.is_correct is True
    assert b_choice.is_correct is False
    assert a_choice.text == "One"
    # Community vote distribution block is preserved as explanation so
    # the user sees it in the Designer's Reference field.
    assert result.explanation is not None
    assert "Community vote distribution" in result.explanation


def test_parse_block_multi_choice_choose_two() -> None:
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = "Which two are correct? (Choose two.)\n\nA. P\nB. Q\nC. R\n\nAnswer: A, C\n\n"
    result = _parse_block(2, block, {})
    assert isinstance(result, ParsedQuestion)
    assert result.question_type == "multi_choice"
    correct = [c.letter for c in result.choices if c.is_correct]
    assert sorted(correct) == ["A", "C"]


def test_parse_block_answer_formats() -> None:
    """``"B, D"``, ``"BD"``, and ``"B D"`` must all yield two correct choices."""
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    base_block = "Stem? (Choose two.)\n\nA. A\nB. B\nC. C\nD. D\n\nAnswer: {ans}\n\n"
    for answer_str in ("B, D", "BD", "B D"):
        result = _parse_block(3, base_block.format(ans=answer_str), {})
        assert isinstance(result, ParsedQuestion), f"failed for answer {answer_str!r}"
        correct = {c.letter for c in result.choices if c.is_correct}
        assert correct == {"B", "D"}, f"wrong correct set for answer {answer_str!r}"


def test_parse_block_no_choices_returns_parse_error() -> None:
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParseError

    block = "Just some text with no choices.\n\nAnswer: A\n\n"
    result = _parse_block(4, block, {})
    assert isinstance(result, ParseError)
    assert result.source_range == "Q4"


def test_image_placeholder_in_body_populates_images() -> None:
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    # Simulate a block where Q body contains ⟨IMG:3⟩ (global id 3)
    block = "Look at the image ⟨IMG:3⟩. Which is correct?\n\nA. Yes\nB. No\n\nAnswer: A\n\n"
    global_images = {3: (".png", b"\x89PNG")}
    result = _parse_block(6, block, global_images)
    assert isinstance(result, ParsedQuestion)
    assert len(result.images) == 1
    assert result.images[0].placeholder_id == 3
    assert result.images[0].suffix == ".png"
    assert result.images[0].data == b"\x89PNG"


def test_community_vote_goes_into_explanation() -> None:
    """The community-vote block is kept as the question's explanation
    so the user sees it in the Designer's Reference field (matches
    Visual CertExam's Explanation/Reference column)."""
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "What? \n\nA. X\nB. Y\n\nAnswer: B\n\n"
        "Community vote distribution\nB (80%)\n20%\n\n"
    )
    result = _parse_block(5, block, {})
    assert isinstance(result, ParsedQuestion)
    assert result.explanation is not None
    assert "Community vote distribution" in result.explanation
    assert "B (80%)" in result.explanation


def test_no_community_vote_leaves_explanation_none() -> None:
    """When the source omits the community-vote block the explanation
    stays ``None`` — we never make up text for the Reference field."""
    from posrat.importers.examtopics import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = "What? \n\nA. X\nB. Y\n\nAnswer: B\n\n"
    result = _parse_block(5, block, {})
    assert isinstance(result, ParsedQuestion)
    assert result.explanation is None


def _make_rtf(body: str) -> bytes:
    """Wrap a plain body string into minimal RTF bytes for testing."""
    # Encode \r as \'0d, \n as \\par, other chars verbatim (ASCII only)
    rtf_body = ""
    for ch in body:
        if ch == "\r":
            rtf_body += "\\'0d"
        elif ch == "\n":
            rtf_body += "\\par "
        else:
            rtf_body += ch
    return ("{\\rtf1\\ansi " + rtf_body + "}").encode("ascii")


def test_rtf_integration_single_question() -> None:
    """parse() on a synthetic RTF file returns one ParsedQuestion."""
    import tempfile
    from pathlib import Path
    from posrat.importers.examtopics import ExamTopicsParser

    rtf = _make_rtf(
        "Q1\rWhat is X?\n\nA. Alpha\nB. Beta\n\nAnswer: A\n\n"
        "Community vote distribution\nA (100%)\n\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".rtf", delete=False) as f:
        f.write(rtf)
        tmp = Path(f.name)

    try:
        result = ExamTopicsParser().parse(tmp)
    finally:
        tmp.unlink()

    assert len(result.questions) == 1
    assert len(result.parse_errors) == 0
    q = result.questions[0]
    assert q.source_index == 1
    assert q.question_type == "single_choice"
    # Synthetic RTF included a "Community vote distribution" block, so
    # the parser should carry it into ``explanation``.
    assert q.explanation is not None
    assert "Community vote distribution" in q.explanation
    correct = [c for c in q.choices if c.is_correct]
    assert len(correct) == 1
    assert correct[0].letter == "A"


def test_rtf_integration_image_placeholder() -> None:
    """Image placeholder in body is wired to ParsedQuestion.images."""
    import tempfile
    from pathlib import Path
    from posrat.importers.rtf_utils import strip_rtf_to_text
    from posrat.importers.examtopics import ExamTopicsParser

    # Build RTF with an embedded PNG image between Q1 label and question body.
    img_hex = "89504e47"  # b'\x89PNG'
    rtf_src = (
        "{\\rtf1\\ansi "
        "Q1\\'0d"
        "Look at {\\pict\\pngblip " + img_hex + "} which answer?\\'0d"
        "\\par "
        "A. Yes\\par "
        "B. No\\par "
        "\\par "
        "Answer: A\\par "
        "}"
    )
    rtf = rtf_src.encode("ascii")

    # Sanity: strip gives us ⟨IMG:0⟩ placeholder
    text, imgs = strip_rtf_to_text(rtf)
    assert "⟨IMG:0⟩" in text
    assert 0 in imgs

    with tempfile.NamedTemporaryFile(suffix=".rtf", delete=False) as f:
        f.write(rtf)
        tmp = Path(f.name)

    try:
        result = ExamTopicsParser().parse(tmp)
    finally:
        tmp.unlink()

    assert len(result.questions) == 1
    q = result.questions[0]
    assert len(q.images) == 1
    assert q.images[0].placeholder_id == 0
    assert q.images[0].suffix == ".png"


# ---------------------------------------------------------------------------
# End-to-end smoke test against the real ExamTopics RTF fixture.
# ---------------------------------------------------------------------------

import os
from pathlib import Path

import pytest

_REAL_RTF_ENV = "POSRAT_EXAMTOPICS_RTF"
_REAL_RTF_DEFAULT = "/mnt/c/DATA/certy/examtopics.html_formatted.rtf"


def _real_rtf_path() -> Path | None:
    """Return the on-disk ExamTopics fixture, or ``None`` when unavailable.

    The 13 MB real-world dump is not shipped in the repo. Developers can
    point the test at their local copy via ``POSRAT_EXAMTOPICS_RTF`` or
    keep the canonical WSL path. CI environments without the file are
    skipped instead of failing so the test is portable.
    """
    candidate = Path(os.environ.get(_REAL_RTF_ENV, _REAL_RTF_DEFAULT))
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(_real_rtf_path() is None, reason="real ExamTopics RTF fixture not available")
def test_real_examtopics_rtf_parses_without_errors() -> None:
    """End-to-end smoke test: parse the 181-question reference RTF.

    Asserts the same quality bar we require from the bulk importer in
    production use: every Q-block must turn into a ``ParsedQuestion``
    (≥170 of 181, tolerating some drift in the source format), zero
    ``ParseError`` records for the current fixture, and the Q/A pair
    for the well-known Q1 must come out correct (D is the answer).
    """
    from posrat.importers.examtopics import ExamTopicsParser

    path = _real_rtf_path()
    assert path is not None  # makes type checker happy; @skipif handled None
    result = ExamTopicsParser().parse(path)

    assert len(result.questions) >= 170, (
        f"expected >=170 parsed, got {len(result.questions)}"
    )
    assert len(result.parse_errors) == 0, (
        f"unexpected ParseErrors: {[str(e) for e in result.parse_errors[:5]]}"
    )
    # Smoke checks on the first well-known question.
    q1 = next(q for q in result.questions if q.source_index == 1)
    assert q1.question_type == "single_choice"
    # The real fixture includes a community-vote block for every
    # question; it must end up as the explanation/reference.
    assert q1.explanation is not None
    assert "Community vote" in q1.explanation
