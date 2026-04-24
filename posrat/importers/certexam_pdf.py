# posrat/importers/certexam_pdf.py
"""CertExam Designer PDF bulk-import parser.

Parses the PDF exports produced by the Visual CertExam **Exam Designer**
("Export to PDF") — a common distribution format for AWS / Azure /
Cisco VCE-style practice exams. The layout is very close to the
practice-exam RTF dumps (same per-question block shape: ``QUESTION <N>``,
``A. ... D.``, ``Correct Answer: <letters>``, optional community-vote
distribution). We therefore share as much of the parsing shape with
:mod:`posrat.importers.rtf_questions` as possible, but the *source*
extraction is completely different — we rely on :mod:`pypdf` to pull
plain text out of the PDF and then regex-tokenise the stream the same
way the RTF parser does.

Why a separate parser instead of extending the RTF parser:

* Extraction path differs fundamentally (PDF -> text via pypdf vs RTF
  control-word stripping). Keeping them in one module would force a
  format-branching constructor and make both harder to reason about.
* PDF page-boundary handling requires its own normalisation step
  (each page starts with the exam-code header such as ``AIF-C01``;
  the tokeniser must not be confused by it).
* The UI layer already expects one :class:`ImportSource` per file
  format (accept= on ``ui.upload`` auto-filters the picker based on
  the parser's declared ``file_extensions``).

Images embedded in the PDF are **not** extracted in this first
iteration. PDF image bytes live in XObject streams and mapping them
back to a position in the reconstructed text stream is non-trivial;
almost all AWS/Azure certification dumps are text-only anyway. Adding
image support can be layered on top later without changing the
intermediate :class:`ParsedQuestion` contract.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Final

from posrat.importers.base import (
    ParseError,
    ParsedChoice,
    ParsedQuestion,
    ParseResult,
    normalize_paragraphs,
    register_import_source,
)
from posrat.importers.rtf_questions import _parse_answer_letters

_log = logging.getLogger(__name__)


_Q_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^QUESTION\s+(\d+)\s*$",
)
"""Matches a ``QUESTION <N>`` label on its own line.

CertExam Designer emits each question stem with a bare ``QUESTION N``
header followed by a newline — no punctuation, no trailing text.
Anchoring to start-of-line (``^`` with MULTILINE) and requiring the
numeric group to occupy the rest of the line rules out false matches
where the word "QUESTION" appears mid-sentence inside a stem.
"""

_CHOICE_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-E])\.\s+(.*)",
)
"""Matches a single answer choice line: ``A. text``.

AWS / Azure exams routinely include 5-option multi-choice questions
(``A``..``E``), so we accept up to ``E`` here. If a future source
emits more choices the range can be widened without any other change.
"""

_ANSWER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Correct Answer:\s*(.+)",
)
"""Matches the answer line: ``Correct Answer: A`` or ``Correct Answer: BC``.

Note the difference from the RTF dump (``Answer: ...``) — CertExam
Designer uses the longer ``Correct Answer:`` prefix verbatim.
"""

_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Section:",
)
"""Matches the ``Section: (none)`` marker that follows ``Correct Answer``.

Used as an end-of-choices guard for blocks that skip the Answer line
(malformed source) and for phase-machine cleanup between the answer
block and the community-vote block.
"""

_EXPLANATION_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Explanation(/Reference)?:?$",
)
"""Matches the ``Explanation`` / ``Explanation/Reference:`` boilerplate
headers that Visual CertExam emits between the answer row and the
community-vote distribution. These carry no user-visible text and are
dropped from the captured explanation block."""

_COMMUNITY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Community vote distribution",
)
"""Marks the start of the community-vote section, which we preserve as
the question's explanation (matches the RTF parser's convention so the
Designer's Reference field looks consistent across sources)."""

_MULTI_CHOICE_RE: Final[re.Pattern[str]] = re.compile(
    r"\(Choose (two|three|four|five)\.?\)",
    re.IGNORECASE,
)
"""Detects multi-choice cues in the question body.

CertExam Designer preserves the original exam-provider wording
(``(Choose two.)``, ``(Choose three.)``, ...). Case-insensitive so
mixed-case variants that occasionally show up in Azure dumps still
match.
"""

_EXAM_CODE_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Z0-9]{2,10}(?:-[A-Z0-9]{1,6})?$",
)
"""Matches an exam-code header like ``AIF-C01``, ``SAP-C02``, ``AZ-900``.

Each PDF page starts with the exam code as a standalone line. We
strip these **before** tokenising the Q-blocks so the label does not
get glued to the preceding question text when pages join. Kept tight
(uppercase letters + digits + at most one hyphenated segment) to
avoid accidentally eating unrelated short lines that happen to be
all-caps.
"""


def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, dict[str, str]]:
    """Decode ``pdf_bytes`` into a single text string plus source metadata.

    Returns
    -------
    tuple
        ``(text, metadata)`` where ``text`` is every page's
        ``extract_text()`` joined by newlines **with the per-page
        exam-code header stripped**, and ``metadata`` is a
        ``dict[str, str]`` of the key-value pairs found on the first
        page (``"exam_code"``, ``"passing_score"``, ``"time_limit"``,
        ``"file_version"``). Missing metadata keys are simply omitted
        so callers can use ``metadata.get("passing_score")`` with no
        defaults bookkeeping.

    Notes
    -----
    * ``pypdf`` is imported lazily so unit tests that only exercise
      the tokenizer / block parser don't pay the import cost.
    * The first page is metadata-only (``AIF-C01`` / ``Passing Score:
      700`` / ...) — no ``QUESTION`` label. We feed it to the tokenizer
      anyway; the absence of Q-labels makes it a harmless no-op while
      letting the metadata extraction share the same ``page.extract_text()``
      call.
    * Per-page exam-code headers are detected heuristically via
      :data:`_EXAM_CODE_HEADER_RE` on the first non-empty line of each
      page; if the first line doesn't match it is left untouched so
      custom exports without a code header don't lose content.
    """

    # Lazy import keeps the top-level module importable without pypdf
    # in minimal test environments.
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))

    metadata: dict[str, str] = {}
    page_texts: list[str] = []

    for page_index, page in enumerate(reader.pages):
        raw = page.extract_text() or ""
        lines = raw.splitlines()

        # Peel off the exam-code header from every page. We only look
        # at the first non-blank line to keep the heuristic predictable.
        stripped_lines: list[str] = []
        header_removed = False
        for line in lines:
            if (
                not header_removed
                and line.strip()
                and _EXAM_CODE_HEADER_RE.match(line.strip())
            ):
                exam_code = line.strip()
                metadata.setdefault("exam_code", exam_code)
                header_removed = True
                continue
            stripped_lines.append(line)

        # Harvest the first-page metadata block on the first page only.
        # The header lines look like ``Passing Score: 700`` or
        # ``Time Limit: 90 min`` — simple ``k: v`` pairs.
        if page_index == 0:
            for line in stripped_lines:
                stripped = line.strip()
                if ":" not in stripped:
                    continue
                key, _, value = stripped.partition(":")
                key_norm = key.strip().lower().replace(" ", "_")
                value_norm = value.strip()
                if key_norm in (
                    "number",
                    "passing_score",
                    "time_limit",
                    "file_version",
                ) and value_norm:
                    metadata.setdefault(key_norm, value_norm)

        page_texts.append("\n".join(stripped_lines))

    text = "\n".join(page_texts)
    return text, metadata


def _tokenize(text: str) -> list[tuple[int, str]]:
    """Split the joined PDF text into ``[(n, block), ...]`` tuples.

    Each block contains everything from ``QUESTION <N>`` up to (but
    not including) the next ``QUESTION <N+k>`` or end-of-string. The
    leading Q-label line is stripped from the block so the downstream
    :func:`_parse_block` can focus on the body.
    """

    matches = list(_Q_LABEL_RE.finditer(text))
    if not matches:
        return []

    blocks: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        n = int(match.group(1))
        body_start = match.end()
        body_end = (
            matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        )
        blocks.append((n, text[body_start:body_end]))
    return blocks


def _parse_block(n: int, block: str) -> ParsedQuestion | ParseError:
    """Parse one Q-block into :class:`ParsedQuestion` or :class:`ParseError`.

    The shape of a block is:

    ```
    <question text — possibly multi-line>
    A. <choice>
    B. <choice>
    C. <choice>
    D. <choice>
    Correct Answer: B
    Section: (none)
    Explanation
    Explanation/Reference:
    Community vote distribution
    B (98%)
    2%
    ```

    The community-vote block (when present) is preserved as the
    ``explanation`` so the Designer's Reference field shows the
    voting context — same convention as the RTF parser.

    The function never raises; malformed blocks degrade gracefully to
    :class:`ParseError` records that the preview dialog surfaces in an
    "X parse errors" expansion without aborting the overall import.
    """

    lines = block.splitlines()

    body_lines: list[str] = []
    choice_lines: list[str] = []
    answer_raw: str | None = None
    community_lines: list[str] = []
    in_community = False

    # Phase machine: "body" → "choices" → "answer" → "post_answer"
    # (eating Section: / Explanation / Explanation/Reference lines) →
    # "community" once the ``Community vote distribution`` header
    # shows up. Anything after the community header (until end-of-
    # block) becomes the explanation verbatim.
    phase = "body"

    for line in lines:
        stripped = line.strip()

        if _COMMUNITY_RE.match(stripped):
            in_community = True
            community_lines.append(stripped)
            continue
        if in_community:
            if stripped:
                community_lines.append(stripped)
            continue

        answer_match = _ANSWER_RE.match(stripped)
        if answer_match:
            answer_raw = answer_match.group(1)
            phase = "answer"
            continue

        # After the Answer line we skip Section: / Explanation /
        # Explanation/Reference boilerplate until we hit either the
        # community block or the end of the input. Blank lines are
        # tolerated so the phase stays stable.
        if phase == "answer":
            if (
                _SECTION_RE.match(stripped)
                or _EXPLANATION_HEADER_RE.match(stripped)
                or stripped == ""
            ):
                continue
            # Anything else would be stray content; we drop it so the
            # explanation field captures only the community-vote block.
            continue

        choice_match = _CHOICE_RE.match(stripped)
        if choice_match:
            phase = "choices"
            choice_lines.append(stripped)
            continue

        if phase == "body":
            body_lines.append(line)
        elif phase == "choices":
            # Continuation of the previous choice (wrapped multi-line
            # answer text).
            if stripped:
                choice_lines.append(stripped)

    # Collapse soft-wrapped lines while preserving intentional
    # paragraph separators. PDFs frequently break a single sentence
    # across two physical lines; we want each such sentence to import
    # as one unit, with blank lines (2+ newlines) still delimiting
    # paragraphs verbatim — see ``normalize_paragraphs`` in
    # ``posrat.importers.base``.
    question_text = normalize_paragraphs("\n".join(body_lines))

    question_type: str = "single_choice"
    if _MULTI_CHOICE_RE.search(question_text):
        question_type = "multi_choice"

    choices: list[ParsedChoice] = []
    current_letter: str | None = None
    current_parts: list[str] = []

    def _flush() -> None:
        if current_letter is not None:
            choices.append(
                ParsedChoice(
                    letter=current_letter,
                    text=" ".join(current_parts).strip(),
                    is_correct=False,
                )
            )

    for line in choice_lines:
        match = _CHOICE_RE.match(line)
        if match:
            _flush()
            current_letter = match.group(1)
            current_parts = [match.group(2).strip()]
        else:
            current_parts.append(line.strip())
    _flush()

    if not choices:
        return ParseError(source_range=f"Q{n}", reason="no choices found")

    if answer_raw is None or not answer_raw.strip():
        return ParseError(
            source_range=f"Q{n}", reason="no Correct Answer line found"
        )

    answer_letters = _parse_answer_letters(answer_raw)
    if not answer_letters:
        return ParseError(
            source_range=f"Q{n}",
            reason=f"Correct Answer line unparseable: {answer_raw!r}",
        )

    known_letters = {c.letter for c in choices}
    warnings: list[str] = []
    for letter in answer_letters:
        if letter not in known_letters:
            _log.warning(
                "Q%d: Correct Answer references unknown letter %r", n, letter
            )
            warnings.append(
                f"Correct Answer references unknown choice letter {letter!r}"
            )

    if warnings and all(
        letter not in known_letters for letter in answer_letters
    ):
        return ParseError(
            source_range=f"Q{n}",
            reason=(
                "Correct Answer references only unknown letters: "
                f"{answer_letters}"
            ),
        )

    for choice in choices:
        choice.is_correct = choice.letter in answer_letters

    explanation = normalize_paragraphs("\n".join(community_lines)) or None

    return ParsedQuestion(
        source_index=n,
        text=question_text,
        choices=choices,
        question_type=question_type,  # type: ignore[arg-type]
        explanation=explanation,
        images=[],
        warnings=warnings,
    )


class CertExamPdfParser:
    """ImportSource for Visual CertExam Designer PDF exports.

    The parser is registered at module-import time via
    :func:`register_import_source` so simply adding
    ``import posrat.importers.certexam_pdf`` anywhere makes it
    available in the Designer's bulk-import dropdown.
    """

    source_id = "certexam_pdf"
    display_name = "CertExam Designer PDF"
    file_extensions = (".pdf",)

    def parse(self, path: Path) -> ParseResult:
        pdf_bytes = path.read_bytes()
        text, metadata = extract_pdf_text(pdf_bytes)

        questions: list[ParsedQuestion] = []
        parse_errors: list[ParseError] = []

        for n, block in _tokenize(text):
            result = _parse_block(n, block)
            if isinstance(result, ParseError):
                parse_errors.append(result)
            else:
                questions.append(result)

        source_metadata: dict[str, str] = {"source_file": str(path)}
        source_metadata.update(metadata)

        return ParseResult(
            questions=questions,
            parse_errors=parse_errors,
            source_metadata=source_metadata,
        )


register_import_source(CertExamPdfParser())
