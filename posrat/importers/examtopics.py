
# posrat/importers/examtopics.py
"""ExamTopics RTF bulk-import parser (Phase 8.2)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

from posrat.importers.base import (
    ImportSource,
    ParseError,
    ParsedChoice,
    ParsedImage,
    ParsedQuestion,
    ParseResult,
    normalize_paragraphs,
    register_import_source,
)

_log = logging.getLogger(__name__)

_Q_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^(Q\d+)\r",
)
"""Matches a Q-label at the start of a line, followed by a carriage-return.

ExamTopics RTF encodes the question number as ``Q<N>\\r`` on its own
(the \\r is a literal CR byte embedded via ``\\'0d``). The pattern anchors
to the start of a line (``^``, with MULTILINE) so random ``Q`` characters
inside question bodies don't trigger a split.
"""

_CHOICE_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Z])\.\s+(.*)",
)
"""Matches a single answer choice line: ``A. text``."""

_ANSWER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Answer:\s*(.+)",
)
"""Matches the answer line: ``Answer: A`` or ``Answer: B, D``."""

_COMMUNITY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)^Community vote distribution",
)
"""Marks the start of the community-vote section, which we drop entirely."""

_MULTI_CHOICE_RE: Final[re.Pattern[str]] = re.compile(
    r"\(Choose (two|three|four)\.\)",
    re.IGNORECASE,
)
"""Detects multi-choice cues in the question body."""

_IMG_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(
    r"⟨IMG:(\d+)⟩",
)


def _tokenize(text: str) -> list[tuple[int, str]]:
    """Split stripped-RTF text into ``[(n, block), ...]`` ordered by Q-number.

    Each block contains everything from ``Q<N>\\r`` up to but not including
    the next ``Q<N+k>\\r`` (or end-of-string). The leading ``Q<N>\\r``
    prefix is stripped from the block; only the body remains.
    """
    matches = list(_Q_LABEL_RE.finditer(text))
    if not matches:
        return []

    blocks: list[tuple[int, str]] = []
    for idx, m in enumerate(matches):
        n = int(m.group(1)[1:])
        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append((n, text[body_start:body_end]))
    return blocks


def _parse_answer_letters(raw: str) -> list[str]:
    """Extract letter list from an Answer string like ``"B, D"``, ``"BD"``, or ``"B D"``."""
    raw = raw.strip().upper()
    # Split on commas/spaces first
    tokens = re.split(r"[,\s]+", raw)
    result: list[str] = []
    for token in tokens:
        if len(token) == 1 and token.isalpha():
            result.append(token)
        elif len(token) > 1 and token.isalpha():
            # "BD" style — each character is a separate letter
            result.extend(list(token))
    return result


def _parse_block(
    n: int,
    block: str,
    global_images: dict[int, tuple[str, bytes]],
) -> ParsedQuestion | ParseError:
    """Parse one Q-block into a :class:`ParsedQuestion` or :class:`ParseError`."""
    lines = block.splitlines()

    # Collect body lines (up to first blank line before choices),
    # choice lines, the answer letter(s), and the community-vote
    # distribution block (preserved for explanation).
    body_lines: list[str] = []
    choice_lines: list[str] = []
    answer_raw: str | None = None
    community_lines: list[str] = []
    in_community = False

    # Phase machine: "body" → "choices" → "answer" → ("community"
    # once we hit the "Community vote distribution" header). We drop
    # nothing: community lines become the question's explanation
    # (Visual CertExam-style Reference field) so the user has the
    # voting context visible in the Designer preview.
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

        if _ANSWER_RE.match(stripped):
            m = _ANSWER_RE.match(stripped)
            assert m is not None
            answer_raw = m.group(1)
            phase = "answer"
            continue

        if phase == "answer":
            continue

        choice_m = _CHOICE_RE.match(stripped)
        if choice_m:
            phase = "choices"
            choice_lines.append(stripped)
            continue

        if phase == "body":
            body_lines.append(line)
        elif phase == "choices":
            # continuation line of previous choice (multi-line choice text)
            if stripped:
                choice_lines.append(stripped)

    # Build question text: join physical lines then normalise so soft
    # wraps within a paragraph collapse to a single space while 2+
    # newline paragraph separators are preserved verbatim (see
    # ``normalize_paragraphs`` in ``posrat.importers.base``).
    question_text = normalize_paragraphs("\n".join(body_lines))

    # Detect multi_choice from body
    question_type: str = "single_choice"
    if _MULTI_CHOICE_RE.search(question_text):
        question_type = "multi_choice"

    # Parse choices
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
        m = _CHOICE_RE.match(line)
        if m:
            _flush()
            current_letter = m.group(1)
            current_parts = [m.group(2).strip()]
        else:
            if current_parts is not None:
                current_parts.append(line.strip())
    _flush()

    # Validate
    if not choices:
        return ParseError(source_range=f"Q{n}", reason="no choices found")

    if answer_raw is None or not answer_raw.strip():
        return ParseError(source_range=f"Q{n}", reason="no Answer line found")

    answer_letters = _parse_answer_letters(answer_raw)
    if not answer_letters:
        return ParseError(source_range=f"Q{n}", reason=f"Answer line unparseable: {answer_raw!r}")

    known_letters = {c.letter for c in choices}
    warnings: list[str] = []
    for letter in answer_letters:
        if letter not in known_letters:
            _log.warning("Q%d: Answer references unknown letter %r", n, letter)
            warnings.append(f"Answer references unknown choice letter {letter!r}")

    if warnings and all(letter not in known_letters for letter in answer_letters):
        return ParseError(
            source_range=f"Q{n}",
            reason=f"Answer references only unknown letters: {answer_letters}",
        )

    # Mark correct choices
    for choice in choices:
        choice.is_correct = choice.letter in answer_letters

    # Gather images referenced in question_text
    img_ids = [int(m.group(1)) for m in _IMG_PLACEHOLDER_RE.finditer(question_text)]
    images: list[ParsedImage] = []
    for img_id in img_ids:
        if img_id in global_images:
            suffix, data = global_images[img_id]
            images.append(ParsedImage(placeholder_id=img_id, data=data, suffix=suffix))

    # Assemble explanation from the preserved community-vote block.
    # Empty block (no "Community vote distribution" header found in
    # the source) stays ``None`` so Pydantic's Optional[str] stays
    # happy — a ``""`` would violate the "explanation is either
    # meaningful or absent" convention enforced elsewhere in the
    # Designer / Runner.
    explanation = normalize_paragraphs("\n".join(community_lines)) or None

    return ParsedQuestion(
        source_index=n,
        text=question_text,
        choices=choices,
        question_type=question_type,  # type: ignore[arg-type]
        explanation=explanation,
        images=images,
        warnings=warnings,
    )


class ExamTopicsParser:
    source_id = "examtopics"
    display_name = "ExamTopics RTF"
    file_extensions = (".rtf",)

    def parse(self, path: Path) -> ParseResult:
        from posrat.importers.rtf_utils import strip_rtf_to_text

        rtf_bytes = path.read_bytes()
        text, global_images = strip_rtf_to_text(rtf_bytes)

        questions: list[ParsedQuestion] = []
        parse_errors: list[ParseError] = []

        for n, block in _tokenize(text):
            result = _parse_block(n, block, global_images)
            if isinstance(result, ParseError):
                parse_errors.append(result)
            else:
                questions.append(result)

        return ParseResult(
            questions=questions,
            parse_errors=parse_errors,
            source_metadata={"source_file": str(path)},
        )


register_import_source(ExamTopicsParser())
