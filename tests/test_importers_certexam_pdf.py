# tests/test_importers_certexam_pdf.py
"""Tests for :mod:`posrat.importers.certexam_pdf` (CertExam Designer PDF parser).

The unit tests exercise the pure, pypdf-independent helpers
(``_tokenize``, ``_parse_block``) against synthetic strings so they
run instantly and don't require the ~2 MB real PDF to be on disk. A
skip-able end-to-end smoke test at the bottom hits the real reference
PDF when it's available on the developer machine — it's the same
pattern used for the RTF fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_module_imports() -> None:
    import posrat.importers.certexam_pdf  # noqa: F401


def test_parser_registered_in_registry() -> None:
    import posrat.importers  # noqa: F401 — triggers registration side-effect
    from posrat.importers.base import get_import_source

    src = get_import_source("certexam_pdf")
    assert src.display_name == "CertExam Designer PDF"
    assert ".pdf" in src.file_extensions


def test_parser_reexported_from_init() -> None:
    from posrat.importers import CertExamPdfParser

    assert CertExamPdfParser.source_id == "certexam_pdf"


def test_parser_satisfies_import_source_protocol() -> None:
    from posrat.importers.base import ImportSource
    from posrat.importers.certexam_pdf import CertExamPdfParser

    parser = CertExamPdfParser()
    assert isinstance(parser, ImportSource)
    assert parser.source_id == "certexam_pdf"
    assert parser.file_extensions == (".pdf",)


# ---------------------------------------------------------------------------
# Tokenizer / block parser — pure string in, dataclass out.
# ---------------------------------------------------------------------------


_THREE_Q_TEXT = (
    "QUESTION 1\n"
    "What is X?\n"
    "A. One\n"
    "B. Two\n"
    "Correct Answer: A\n"
    "Section: (none)\n"
    "Explanation\n"
    "Explanation/Reference:\n"
    "Community vote distribution\n"
    "A (93%)\n"
    "7%\n"
    "QUESTION 2\n"
    "What is Y?\n"
    "A. Foo\n"
    "B. Bar\n"
    "Correct Answer: B\n"
    "QUESTION 3\n"
    "What is Z? (Choose two.)\n"
    "A. P\n"
    "B. Q\n"
    "C. R\n"
    "Correct Answer: AC\n"
)


def test_tokenize_finds_three_blocks() -> None:
    from posrat.importers.certexam_pdf import _tokenize

    blocks = _tokenize(_THREE_Q_TEXT)
    assert [n for n, _ in blocks] == [1, 2, 3]
    # Each body should NOT begin with a Q-label — the label is stripped
    # by the tokenizer so _parse_block starts reading the real content.
    for _, body in blocks:
        assert "QUESTION" not in body.splitlines()[0]


def test_tokenize_returns_empty_when_no_q_labels() -> None:
    from posrat.importers.certexam_pdf import _tokenize

    assert _tokenize("Just prose without question markers.") == []


def test_parse_block_single_choice_happy_path() -> None:
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "What is X?\n"
        "A. One\n"
        "B. Two\n"
        "Correct Answer: B\n"
        "Section: (none)\n"
        "Explanation\n"
        "Explanation/Reference:\n"
        "Community vote distribution\n"
        "B (93%)\n"
        "7%\n"
    )
    result = _parse_block(1, block)
    assert isinstance(result, ParsedQuestion)
    assert result.source_index == 1
    assert result.question_type == "single_choice"
    assert len(result.choices) == 2
    correct = [c for c in result.choices if c.is_correct]
    assert [c.letter for c in correct] == ["B"]
    # Community-vote block is captured as the explanation (Designer's
    # Reference field convention, mirrors the RTF parser).
    assert result.explanation is not None
    assert "Community vote distribution" in result.explanation
    assert "B (93%)" in result.explanation
    # Boilerplate "Section:" / "Explanation" / "Explanation/Reference:"
    # headers must NOT leak into the captured explanation block.
    assert "Section:" not in result.explanation
    assert "Explanation/Reference:" not in result.explanation


def test_parse_block_multi_choice_from_choose_two_cue() -> None:
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "Pick the two correct. (Choose two.)\n"
        "A. P\n"
        "B. Q\n"
        "C. R\n"
        "Correct Answer: AC\n"
    )
    result = _parse_block(7, block)
    assert isinstance(result, ParsedQuestion)
    assert result.question_type == "multi_choice"
    correct = {c.letter for c in result.choices if c.is_correct}
    assert correct == {"A", "C"}


def test_parse_block_accepts_five_choice_answer() -> None:
    """Real AWS/Azure dumps include ``A``..``E`` multi-choice questions.

    The regex must accept ``E`` as a valid choice letter and the
    answer parser must pick it up as correct when ``Correct Answer:
    AE`` shows up.
    """
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "Pick two. (Choose two.)\n"
        "A. Alpha\n"
        "B. Beta\n"
        "C. Gamma\n"
        "D. Delta\n"
        "E. Epsilon\n"
        "Correct Answer: AE\n"
    )
    result = _parse_block(42, block)
    assert isinstance(result, ParsedQuestion)
    assert len(result.choices) == 5
    correct = {c.letter for c in result.choices if c.is_correct}
    assert correct == {"A", "E"}


def test_parse_block_missing_answer_returns_parse_error() -> None:
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParseError

    block = "Stem only.\nA. One\nB. Two\n"
    result = _parse_block(9, block)
    assert isinstance(result, ParseError)
    assert result.source_range == "Q9"
    assert "Correct Answer" in result.reason


def test_parse_block_no_choices_returns_parse_error() -> None:
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParseError

    block = "Just some text.\nCorrect Answer: A\n"
    result = _parse_block(11, block)
    assert isinstance(result, ParseError)
    assert result.source_range == "Q11"
    assert "choices" in result.reason


def test_parse_block_no_community_vote_leaves_explanation_none() -> None:
    """When the source omits the community-vote block, explanation
    must stay ``None`` — never an empty string — so the Designer can
    display "no reference" cleanly."""
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "What? \n"
        "A. X\n"
        "B. Y\n"
        "Correct Answer: B\n"
        "Section: (none)\n"
        "Explanation\n"
        "Explanation/Reference:\n"
    )
    result = _parse_block(5, block)
    assert isinstance(result, ParsedQuestion)
    assert result.explanation is None


def test_parse_block_multiline_choice_text_is_joined() -> None:
    """A choice whose text wraps across multiple PDF lines should end
    up as a single space-joined string — mirrors the RTF
    parser's behaviour and matches how the PDF extractor splits
    long lines."""
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "Long stem?\n"
        "A. First line of A\n"
        "continuation of A\n"
        "B. Second option\n"
        "Correct Answer: A\n"
    )
    result = _parse_block(12, block)
    assert isinstance(result, ParsedQuestion)
    a_choice = next(c for c in result.choices if c.letter == "A")
    assert "continuation of A" in a_choice.text


def test_parse_block_no_images_populated_for_pdf() -> None:
    """CertExam PDF parser does not emit ``ParsedImage`` records in
    this first iteration — images live in XObject streams and need a
    separate pipeline. The ``images`` list must therefore always be
    empty even for questions whose source text mentions images."""
    from posrat.importers.certexam_pdf import _parse_block
    from posrat.importers.base import ParsedQuestion

    block = (
        "Look at the diagram and decide.\n"
        "A. X\n"
        "B. Y\n"
        "Correct Answer: A\n"
    )
    result = _parse_block(13, block)
    assert isinstance(result, ParsedQuestion)
    assert result.images == []


# ---------------------------------------------------------------------------
# End-to-end smoke test against the real CertExam Designer PDF fixture.
# ---------------------------------------------------------------------------


_REAL_PDF_ENV = "POSRAT_CERTEXAM_PDF"
_REAL_PDF_DEFAULT = "/mnt/c/DATA/certy/export-exam_designer.pdf"


def _real_pdf_path() -> Path | None:
    """Return the on-disk CertExam PDF fixture or ``None`` when unavailable.

    The 2.4 MB real-world export is not shipped in the repo. Developers
    can point the test at their local copy via
    ``POSRAT_CERTEXAM_PDF``; CI environments without the file are
    skipped so the test remains portable.
    """
    candidate = Path(os.environ.get(_REAL_PDF_ENV, _REAL_PDF_DEFAULT))
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(
    _real_pdf_path() is None, reason="real CertExam PDF fixture not available"
)
def test_real_certexam_pdf_parses_without_errors() -> None:
    """End-to-end smoke test: parse the 334-question reference PDF.

    The production-quality bar mirrors the RTF fixture smoke
    test: every Q-block must turn into a :class:`ParsedQuestion`, zero
    :class:`ParseError` records are tolerated for the shipped fixture,
    and the well-known Q1 must be identified as a ``single_choice``
    with choice B as the correct answer (``B. Partial dependence
    plots``).
    """
    from posrat.importers.certexam_pdf import CertExamPdfParser

    path = _real_pdf_path()
    assert path is not None  # @skipif handled None
    result = CertExamPdfParser().parse(path)

    assert len(result.questions) >= 300, (
        f"expected >=300 parsed, got {len(result.questions)}"
    )
    assert len(result.parse_errors) == 0, (
        f"unexpected ParseErrors: {[str(e) for e in result.parse_errors[:5]]}"
    )

    q1 = next(q for q in result.questions if q.source_index == 1)
    assert q1.question_type == "single_choice"
    correct = [c for c in q1.choices if c.is_correct]
    assert [c.letter for c in correct] == ["B"]

    # At least one multi-choice question must be present (``Choose two.``
    # cue triggers the branch). This locks in the multi_choice path on
    # real data so a regression in the regex is caught early.
    assert any(q.question_type == "multi_choice" for q in result.questions)

    # Source metadata from the first page of the PDF must round-trip.
    assert result.source_metadata.get("passing_score") == "700"


# ---------------------------------------------------------------------------
# extract_pdf_text — exam-code header stripping + metadata harvest.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _real_pdf_path() is None, reason="real CertExam PDF fixture not available"
)
def test_extract_pdf_text_strips_per_page_header() -> None:
    """Each PDF page starts with ``AIF-C01`` (the exam code). The
    joined text must not contain a trailing exam code glued to the
    previous question's content — :func:`extract_pdf_text` strips
    that header from every page."""
    from posrat.importers.certexam_pdf import extract_pdf_text

    path = _real_pdf_path()
    assert path is not None
    text, metadata = extract_pdf_text(path.read_bytes())

    # The exam code survives exactly once in the metadata; it should
    # not appear 138 times sprinkled through the body (which would be
    # a symptom of per-page header leakage).
    assert metadata.get("exam_code") == "AIF-C01"
    assert text.count("AIF-C01") < 3  # allow a metadata echo or two

    # Sanity: the tokenizer can still find QUESTION labels.
    assert "QUESTION 1" in text
    assert "QUESTION 334" in text
