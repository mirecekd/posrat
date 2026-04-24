
# posrat/importers/base.py
"""Core types for the pluggable bulk-import layer.

The module defines:

* The :class:`ImportSource` runtime-checkable Protocol that every concrete
  parser (ExamTopics RTF, Whizlabs JSON, ...) implements.
* Intermediate dataclasses (:class:`ParsedQuestion`, :class:`ParsedChoice`,
  :class:`ParsedImage`, :class:`ParseError`, :class:`ParseResult`) that
  decouple *format-specific* parsing from *storage-specific* persistence.
  These objects survive the preview step; only after the user confirms the
  selection in the UI are they converted into Pydantic ``Question`` models and
  inserted into the database.
* A lightweight in-process registry (``IMPORT_SOURCES``) with
  :func:`register_import_source`, :func:`get_import_source`, and
  :func:`list_import_sources`. Concrete parsers call
  :func:`register_import_source` at import time so that the Designer UI can
  discover them via ``list_import_sources()`` and populate the dropdown in the
  bulk-import dialog.

Intermediate objects are deliberately *not* Pydantic models: the parser may
emit provisional / partially-valid data (e.g. a ``multi_choice`` question
flagged as ``has_warnings``) that should be shown in the preview but skipped
on import. Strict Pydantic validation kicks in later in
``convert_parsed_to_exam_questions`` (step 8.4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared text-normalisation helper
# ---------------------------------------------------------------------------


_PARAGRAPH_SEP_RE: Final[re.Pattern[str]] = re.compile(r"(\n{2,})")
"""Matches runs of 2+ newlines (paragraph separators). The capture group
is kept via :func:`re.split` so the *count* of separating newlines is
preserved when we rebuild the text — callers rely on the difference
between ``\\n\\n`` (sentence break) and ``\\n\\n\\n`` (pre-last-question
separator) being faithfully carried through."""


_INTRA_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]*\n[ \t]*")
"""Matches a single newline plus the surrounding horizontal whitespace
inside one paragraph. Replacing this with a single space collapses
soft-wrapped lines (e.g. a sentence that PDF/RTF broke across two
physical lines) back into one logical sentence while leaving
intentional paragraph boundaries alone (those are 2+ newlines and are
handled separately via :data:`_PARAGRAPH_SEP_RE`)."""


_QUESTION_END_RE: Final[re.Pattern[str]] = re.compile(r"\?(?:[\"')\]]+)?$")
"""Matches a question-mark ending on a physical line (optionally
followed by closing quote/paren characters). PDF/RTF exam dumps
typically place the real question (the ``?`` sentence) directly
before the choice list; our convention is to merge all preceding
context into one monolithic paragraph and keep ONLY the final
question line set apart by a triple-newline break, matching the
visual grouping the author intends."""


def normalize_paragraphs(text: str) -> str:
    """Reflow PDF/RTF-extracted body text into ``<context>\\n\\n\\n<question?>``.

    PDF and RTF extractors emit one physical ``\\n`` for every visual
    line break — including soft wraps that split a single sentence
    across two lines. The author's convention, however, is that the
    preceding context (possibly spanning multiple sentences) should
    import as ONE monolithic paragraph, and only the final sentence
    ending with ``?`` should stand apart.

    This helper implements that rule:

    * All physical line breaks (single ``\\n``) are collapsed to a
      single space so that the context merges into one flowing
      paragraph. Horizontal whitespace clinging to the break is also
      swallowed so ``"foo  \\n  bar"`` becomes ``"foo bar"``.
    * A line that ends with ``?`` is treated as the question stem.
      Between the monolithic context and the question line we emit
      three newlines (``\\n\\n\\n``) so the preview renders the
      question in its own visually-separated paragraph.
    * If there is no ``?`` line (for example a block with only a
      statement context) the whole result is a single monolith.
    * Multiple ``?`` lines — rare, but possible when a stem contains
      a clarifying question earlier in the body — are all promoted
      to their own paragraphs with a ``\\n\\n\\n`` separator before
      each so the author's emphasis is preserved everywhere.
    * Pre-existing paragraph separators (runs of 2+ newlines) in the
      input are respected verbatim — we never collapse them down.
    * Outer whitespace of the whole string is trimmed so the Designer
      preview doesn't inherit stray blanks from the extractor.

    Parameters
    ----------
    text:
        Raw joined body text (typically the output of
        ``"\\n".join(body_lines)`` in a parser's phase machine).

    Returns
    -------
    str
        Reflowed body with context monolith and question stem
        separated by a ``\\n\\n\\n`` break.
    """

    # Respect any existing paragraph separators (2+ \n) by splitting
    # on them first; each chunk then gets reflowed independently and
    # the separators are re-inserted verbatim. Authors who typed a
    # real blank line keep that blank line intact.
    parts = _PARAGRAPH_SEP_RE.split(text)
    rebuilt: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            # Paragraph separator — preserve the exact newline count.
            rebuilt.append(part)
            continue
        rebuilt.append(_reflow_paragraph(part))
    return "".join(rebuilt).strip()


def _reflow_paragraph(paragraph: str) -> str:
    """Collapse ``paragraph`` into a context monolith + question stem.

    Walks the physical lines; joins each consecutive run of non-``?``
    lines with single spaces (the "monolith") and, whenever a line
    ending with ``?`` is encountered, emits it on its own preceded by
    a triple-newline break. See :func:`normalize_paragraphs` for the
    full rationale.
    """

    lines = paragraph.split("\n")
    cleaned: list[str] = [line.strip() for line in lines]
    cleaned = [line for line in cleaned if line]
    if not cleaned:
        return ""

    # Walk the lines collecting a "context" buffer. As soon as we hit
    # a ``?`` line, we need TWO separate paragraphs: the context
    # collected so far, and the question line itself. Soft-wrapped
    # question lines (a ``?`` sentence split across multiple physical
    # lines) are handled by back-tracking: we peel trailing lines off
    # the buffer until we land on one that either ends a sentence
    # (``.``, ``!``, ``:``) or is the beginning of the buffer.
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in cleaned:
        buffer.append(line)
        if not _QUESTION_END_RE.search(line):
            continue

        # Find where the question line actually *starts*. We scan
        # backwards over the buffer; any line in that tail that does
        # not end with sentence punctuation (``.``, ``!``, ``:``) is
        # a soft-wrap continuation that belongs to the same question.
        question_start = len(buffer) - 1
        while question_start > 0:
            prev = buffer[question_start - 1]
            if re.search(r"[.!:](?:[\"')\]]+)?$", prev):
                break
            question_start -= 1

        context_lines = buffer[:question_start]
        question_lines = buffer[question_start:]
        if context_lines:
            paragraphs.append(" ".join(context_lines))
        paragraphs.append(" ".join(question_lines))
        buffer = []

    if buffer:
        paragraphs.append(" ".join(buffer))

    return "\n\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Intermediate dataclasses (format-agnostic representation)
# ---------------------------------------------------------------------------


@dataclass
class ParsedChoice:
    """Single answer option parsed from the source.

    Attributes
    ----------
    letter:
        Original letter label (``"A"``, ``"B"``, ...). Preserved for debugging
        and for the preview dialog; the final ``Choice.id`` is regenerated
        when the question is persisted, so this value is not exported to the
        database directly.
    text:
        Human-readable answer text with all format-specific escapes resolved.
    is_correct:
        True when the source marks this choice as the (or one of the) correct
        answer(s). Parser is responsible for translating the source-specific
        convention into this flag (e.g. ExamTopics' ``Answer: D`` => choice
        with letter ``D`` has ``is_correct=True``).
    """

    letter: str
    text: str
    is_correct: bool


@dataclass
class ParsedImage:
    """In-memory representation of an image extracted from the source.

    Images are buffered here during parsing so that the preview step can
    report *how many* images each question carries without touching the
    filesystem. Persistence to ``assets/`` happens later in the conversion
    step (8.4) and only for questions the user actually chose to import.

    Attributes
    ----------
    placeholder_id:
        Numeric ID used to splice the image back into the surrounding text.
        Parsers emit placeholders of the form ``⟨IMG:{id}⟩`` in the question
        body; the conversion step replaces them with Markdown image tags
        once the bytes have been written to disk and a final URL is known.
    data:
        Raw bytes of the image, ready to be written to disk. No format
        transcoding is performed at parse time.
    suffix:
        Lower-cased file suffix including the leading dot (``".png"``,
        ``".jpg"``). Used to pick the asset filename extension.
    """

    placeholder_id: int
    data: bytes
    suffix: str


@dataclass
class ParsedQuestion:
    """Intermediate question record emitted by a parser.

    This is the object the preview dialog renders. It may carry *warnings*
    (parser-detected issues such as ``"multi_choice has no correct answer"``)
    without preventing the preview from being shown. The conversion step will
    later re-validate these records via Pydantic and return structured errors
    for anything that can't be persisted.

    Attributes
    ----------
    source_index:
        1-based question number as seen in the source file (``Q1``, ``Q2``,
        ...). Used only for user-facing labels in the preview dialog; the
        database-level ``Question.id`` is generated independently.
    text:
        Question body with ``⟨IMG:{id}⟩`` placeholders instead of inline
        images. The conversion step replaces placeholders with the final
        Markdown image tags.
    choices:
        Ordered list of :class:`ParsedChoice`. At minimum two entries are
        expected for ``single_choice`` / ``multi_choice``; empty list is
        allowed only for ``hotspot`` (which ExamTopics does not emit).
    question_type:
        One of ``"single_choice"``, ``"multi_choice"``, ``"hotspot"``. Parsers
        infer this from source cues (e.g. "Choose two." => multi_choice).
        If the cue is ambiguous, the parser should default to
        ``"single_choice"`` and attach a warning.
    explanation:
        Optional free-form text. ExamTopics uses this slot for the
        human-readable community-vote distribution (``"Community vote:
        A (93%), 7%"``).
    images:
        Images referenced by placeholders in :attr:`text`. Ordering is
        significant: ``images[i]`` matches ``⟨IMG:i⟩``.
    warnings:
        Parser-detected issues that don't abort extraction. Rendered as a
        tooltip in the preview row so the user can decide whether to include
        the question anyway.
    """

    source_index: int
    text: str
    choices: list[ParsedChoice]
    question_type: Literal["single_choice", "multi_choice", "hotspot"] = (
        "single_choice"
    )
    explanation: str | None = None
    images: list[ParsedImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParseError:
    """A block in the source that the parser could not turn into a question.

    ``ParseError`` records are surfaced in the preview dialog as an
    "X parse errors" banner with a drill-down view, but they never abort
    the overall import: the user can still proceed with the questions that
    did parse.
    """

    source_range: str
    """Human-readable locator such as ``"Q347"`` or ``"lines 1234-1256"``
    so the user can hunt the problem in the source file."""

    reason: str
    """Short English/Czech explanation (``"no choices found"``, ``"answer
    references unknown letter"``, ...)."""


@dataclass
class ParseResult:
    """Container returned by :meth:`ImportSource.parse`.

    Held in ``app.storage.user`` between the preview and commit dialogs so
    that the second button press ("Import selected") does not need to
    re-parse the file.
    """

    questions: list[ParsedQuestion]
    parse_errors: list[ParseError] = field(default_factory=list)
    source_metadata: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser protocol + registry
# ---------------------------------------------------------------------------


@runtime_checkable
class ImportSource(Protocol):
    """Runtime-checkable contract every concrete parser must satisfy.

    Parsers are registered with :func:`register_import_source` (typically
    from their own module's top level so that importing the parser has the
    side-effect of exposing it in the UI dropdown). They must be safe to
    construct with zero arguments; any parser-specific configuration should
    be injected via :meth:`parse` parameters in the future.

    The Protocol is intentionally narrow: parsers only need to describe
    themselves (for the UI) and know how to ingest a file path. All the
    heavy lifting (database writes, asset persistence, Pydantic validation)
    happens in later pipeline stages that are *parser-agnostic*.
    """

    source_id: str
    """Stable machine identifier (``"examtopics"``). Used as the key in
    :data:`IMPORT_SOURCES` and as the ``value`` of the UI dropdown option."""

    display_name: str
    """Human-readable label for the UI (``"ExamTopics RTF"``)."""

    file_extensions: tuple[str, ...]
    """Accepted file suffixes **including the leading dot** (``(".rtf",)``).
    Used to filter the file picker so the user can't accidentally point the
    parser at an unrelated format."""

    def parse(self, path: Path) -> ParseResult:
        """Turn a source file into a :class:`ParseResult`.

        Implementations must be **read-only**: they must not write to the
        filesystem, mutate the database, or network. All side-effecting
        persistence happens in later pipeline stages.
        """
        ...


IMPORT_SOURCES: dict[str, ImportSource] = {}
"""Registry of all :class:`ImportSource` instances keyed by ``source_id``.

Populated by :func:`register_import_source`. Consumers should prefer
:func:`get_import_source` / :func:`list_import_sources` over direct access to
keep the call sites decoupled from the storage type."""


def register_import_source(source: ImportSource) -> ImportSource:
    """Register ``source`` in :data:`IMPORT_SOURCES`.

    Rejects instances that don't satisfy the :class:`ImportSource` protocol
    and duplicates (same ``source_id`` registered twice) so that bugs in
    parser modules fail loudly at import time rather than at import-dialog-
    open time.

    Returns the source unchanged so the function can be used as a decorator
    on the parser class instance if desired.
    """
    if not isinstance(source, ImportSource):
        raise TypeError(
            f"object {source!r} does not satisfy the ImportSource protocol"
        )
    if source.source_id in IMPORT_SOURCES:
        raise ValueError(
            f"import source {source.source_id!r} is already registered"
        )
    IMPORT_SOURCES[source.source_id] = source
    return source


def get_import_source(source_id: str) -> ImportSource:
    """Look up a registered parser by its ``source_id``.

    Raises :class:`KeyError` if no parser with that ID has been registered;
    callers in the UI layer should present this as a generic "unsupported
    import source" error rather than leaking the raw exception.
    """
    try:
        return IMPORT_SOURCES[source_id]
    except KeyError as exc:
        raise KeyError(
            f"no import source registered with id {source_id!r}"
        ) from exc


def list_import_sources() -> list[ImportSource]:
    """Return all registered parsers, ordered by :attr:`display_name`.

    Used by the Designer's bulk-import dialog to populate its source
    dropdown. The stable sort lets the UI avoid the dropdown option order
    changing between Python sessions depending on which parser module got
    imported first.
    """
    return sorted(IMPORT_SOURCES.values(), key=lambda src: src.display_name)
