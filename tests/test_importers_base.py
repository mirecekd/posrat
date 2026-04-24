# tests/test_importers_base.py
"""Unit tests for the importer registry + intermediate dataclasses (step 8.0).

These tests only exercise the *registration* machinery and the structural
invariants of the intermediate dataclasses. Concrete parsers (ExamTopics RTF)
are tested in their own modules because they need fixture files and depend
on the RTF stripper built in step 8.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from posrat.importers import base as importers_base
from posrat.importers.base import (
    IMPORT_SOURCES,
    ImportSource,
    ParsedChoice,
    ParsedImage,
    ParsedQuestion,
    ParseError,
    ParseResult,
    get_import_source,
    list_import_sources,
    normalize_paragraphs,
    register_import_source,
)


class _FakeSource:
    """Minimal :class:`ImportSource` implementation used only in tests."""

    def __init__(
        self,
        source_id: str = "fake",
        display_name: str = "Fake source",
        file_extensions: tuple[str, ...] = (".fake",),
    ) -> None:
        self.source_id = source_id
        self.display_name = display_name
        self.file_extensions = file_extensions

    def parse(self, path: Path) -> ParseResult:
        return ParseResult(questions=[])


@pytest.fixture(autouse=True)
def _clear_registry():
    """Ensure each test starts with an empty registry.

    The production registry is module-level and shared process-wide, so we
    snapshot-restore it to keep tests order-independent.
    """
    snapshot = dict(IMPORT_SOURCES)
    IMPORT_SOURCES.clear()
    try:
        yield
    finally:
        IMPORT_SOURCES.clear()
        IMPORT_SOURCES.update(snapshot)


def test_parsed_choice_is_plain_dataclass():
    """ParsedChoice should be a dataclass with positional field order."""
    choice = ParsedChoice(letter="A", text="Use S3", is_correct=True)
    assert choice.letter == "A"
    assert choice.text == "Use S3"
    assert choice.is_correct is True


def test_parsed_question_defaults_are_single_choice_and_empty_lists():
    """Optional fields default to single_choice + empty lists."""
    question = ParsedQuestion(
        source_index=1,
        text="What is AWS?",
        choices=[
            ParsedChoice(letter="A", text="Cloud", is_correct=True),
            ParsedChoice(letter="B", text="On-prem", is_correct=False),
        ],
    )
    assert question.question_type == "single_choice"
    assert question.explanation is None
    assert question.images == []
    assert question.warnings == []


def test_parsed_image_holds_bytes_and_suffix():
    """Image bytes are stored verbatim; suffix includes the leading dot."""
    image = ParsedImage(placeholder_id=0, data=b"\x89PNG...", suffix=".png")
    assert image.data == b"\x89PNG..."
    assert image.suffix == ".png"


def test_parse_result_defaults_are_empty_containers():
    """Default ParseResult has no errors and no metadata."""
    result = ParseResult(questions=[])
    assert result.questions == []
    assert result.parse_errors == []
    assert result.source_metadata == {}


def test_parse_error_is_dataclass():
    """ParseError carries the source locator + reason."""
    err = ParseError(source_range="Q347", reason="no choices found")
    assert err.source_range == "Q347"
    assert err.reason == "no choices found"


def test_fake_source_satisfies_import_source_protocol():
    """The minimal _FakeSource helper conforms to the runtime-checkable
    Protocol, proving the Protocol accepts plain duck-typed objects."""
    assert isinstance(_FakeSource(), ImportSource)


def test_register_import_source_adds_to_registry():
    """register_import_source populates IMPORT_SOURCES keyed by source_id."""
    source = _FakeSource(source_id="abc")
    returned = register_import_source(source)

    assert returned is source  # registration returns the source unchanged
    assert IMPORT_SOURCES["abc"] is source


def test_register_import_source_rejects_duplicate_ids():
    """Two sources with the same source_id must raise ValueError."""
    register_import_source(_FakeSource(source_id="dup"))
    with pytest.raises(ValueError, match="already registered"):
        register_import_source(_FakeSource(source_id="dup"))


def test_register_import_source_rejects_non_conforming_objects():
    """Objects missing required Protocol attributes are rejected with
    TypeError (fail-fast at registration time)."""

    class _Broken:
        source_id = "broken"
        # missing display_name, file_extensions, parse()

    with pytest.raises(TypeError, match="does not satisfy"):
        register_import_source(_Broken())  # type: ignore[arg-type]


def test_get_import_source_returns_registered_instance():
    """get_import_source should return exactly the object that was
    registered under that id."""
    source = _FakeSource(source_id="x")
    register_import_source(source)
    assert get_import_source("x") is source


def test_get_import_source_raises_keyerror_for_unknown_id():
    """Lookup of an unregistered id raises KeyError, not a silent None."""
    with pytest.raises(KeyError, match="no import source registered"):
        get_import_source("does-not-exist")


def test_list_import_sources_sorted_by_display_name():
    """list_import_sources returns all registered sources in a deterministic
    alphabetical order regardless of registration order."""
    zebra = _FakeSource(source_id="z", display_name="Zebra")
    alpha = _FakeSource(source_id="a", display_name="Alpha")
    middle = _FakeSource(source_id="m", display_name="Middle")

    # Register in non-alphabetical order to prove sorting is applied.
    register_import_source(zebra)
    register_import_source(middle)
    register_import_source(alpha)

    assert list_import_sources() == [alpha, middle, zebra]


# ---------------------------------------------------------------------------
# normalize_paragraphs — reflow context monolith + question stem.
# ---------------------------------------------------------------------------


def test_normalize_paragraphs_merges_all_context_into_one_monolith():
    """All context lines (non-question lines) — regardless of any
    internal periods — must merge into ONE monolithic paragraph
    joined by single spaces. This mirrors the import convention:
    the user wants the question stem ``?`` line separated from
    everything else, and everything else glued together."""
    text = (
        "A company makes forecasts each quarter to decide how to "
        "optimize operations to meet expected demand.\n"
        "The company uses ML models to make these forecasts.\n"
        "An AI practitioner is writing a report about the trained "
        "ML models to provide transparency and explainability\n"
        "to company stakeholders."
    )
    assert normalize_paragraphs(text) == (
        "A company makes forecasts each quarter to decide how to "
        "optimize operations to meet expected demand. "
        "The company uses ML models to make these forecasts. "
        "An AI practitioner is writing a report about the trained "
        "ML models to provide transparency and explainability "
        "to company stakeholders."
    )


def test_normalize_paragraphs_separates_question_with_triple_newline():
    """The ``?`` line must end up in its own paragraph separated
    from the context monolith by exactly three newlines."""
    text = (
        "Context sentence one.\n"
        "Context sentence two.\n"
        "What should the practitioner do?"
    )
    assert normalize_paragraphs(text) == (
        "Context sentence one. Context sentence two."
        "\n\n\n"
        "What should the practitioner do?"
    )


def test_normalize_paragraphs_handles_soft_wrapped_question_line():
    """The question itself may also be split across two physical
    lines. Those soft-wrap fragments must merge into the single
    question line before the triple-newline separator kicks in."""
    text = (
        "Context line.\n"
        "What should the AI practitioner include in the report to meet\n"
        "the transparency requirements?"
    )
    assert normalize_paragraphs(text) == (
        "Context line."
        "\n\n\n"
        "What should the AI practitioner include in the report to meet "
        "the transparency requirements?"
    )


def test_normalize_paragraphs_without_any_question_is_single_monolith():
    """A body without any ``?`` collapses to one monolithic paragraph."""
    text = "First.\nSecond.\nThird."
    assert normalize_paragraphs(text) == "First. Second. Third."


def test_normalize_paragraphs_eats_trailing_whitespace_around_soft_break():
    """Horizontal whitespace clinging to a soft break must not leak
    into the collapsed sentence as a double space."""
    text = "word   \n   next word"
    assert normalize_paragraphs(text) == "word next word"


def test_normalize_paragraphs_trims_outer_whitespace():
    """Leading/trailing whitespace around the whole string is
    stripped — the Designer trims these anyway before rendering."""
    assert normalize_paragraphs("\n\n  body text  \n\n") == "body text"


def test_normalize_paragraphs_empty_string_stays_empty():
    assert normalize_paragraphs("") == ""


def test_normalize_paragraphs_preserves_author_typed_paragraph_break():
    """Existing runs of 2+ newlines (author-typed blank lines) must
    survive verbatim so we don't squash intentional structure."""
    text = "First block.\n\nSecond block?"
    assert normalize_paragraphs(text) == "First block.\n\nSecond block?"


def test_module_exports_include_registry_helpers():
    """Every public symbol referenced in the design doc is re-exported
    from posrat.importers.__init__ and matches the base-module source of
    truth."""
    from posrat import importers as pkg

    assert pkg.IMPORT_SOURCES is importers_base.IMPORT_SOURCES
    assert pkg.register_import_source is importers_base.register_import_source
    assert pkg.get_import_source is importers_base.get_import_source
    assert pkg.list_import_sources is importers_base.list_import_sources
    assert pkg.ImportSource is importers_base.ImportSource
    assert pkg.ParsedQuestion is importers_base.ParsedQuestion
    assert pkg.ParsedChoice is importers_base.ParsedChoice
    assert pkg.ParsedImage is importers_base.ParsedImage
    assert pkg.ParseError is importers_base.ParseError
    assert pkg.ParseResult is importers_base.ParseResult
