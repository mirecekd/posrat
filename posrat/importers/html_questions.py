# posrat/importers/html_questions.py
"""HTML bulk-import parser for saved practice-exam pages.

Targets HTML fragments / saved pages that render one question per
``<div class="exam-question-card">`` card. This is the markup produced
by several online practice-exam browsers — the parser only keys off
the stable CSS class hooks listed below, not any vendor branding, so a
locally-saved ``.html`` or ``.htm`` file with the same card layout is
consumed the same way regardless of which site authored it.

Expected shape of one question card:

.. code-block:: html

    <div class="card exam-question-card">
      <div class="card-header ...">
        Question #<N>
        <span class="question-title-topic ...">Topic 1</span>
      </div>
      <div class="card-body question-body" data-id="...">
        <p class="card-text">
          <stem text, may contain <br> between sentences>
        </p>
        <div class="question-choices-container">
          <ul>
            <li class="multi-choice-item">
              <span class="multi-choice-letter" data-choice-letter="A">A.</span>
              <choice text>
            </li>
            <li class="multi-choice-item correct-hidden">
              <span class="multi-choice-letter" data-choice-letter="B">B.</span>
              <choice text>
              <span class="badge ...">Most Voted</span>
            </li>
            ...
          </ul>
        </div>
        <p class="card-text question-answer ...">
          <span class="correct-answer-box">
            <strong>Correct Answer:</strong>
            <span class="correct-answer">B</span>
          </span>
          <span class="answer-description"> ... </span>
          <div class="voting-summary ...">
            <i>Community vote distribution</i>
            <div class="progress ..."> <div class="vote-bar ...">B (98%)</div> ... </div>
          </div>
        </p>
      </div>
    </div>

Mapping to the intermediate :class:`ParsedQuestion`:

* ``source_index`` — the integer inside ``Question #<N>``.
* ``text`` — the stem extracted from ``<p class="card-text">``,
  with ``<br>`` turned into newlines and HTML entities decoded. The
  shared :func:`normalize_paragraphs` helper then reflows soft wraps
  and lifts the final ``?`` line into its own paragraph, mirroring
  the behaviour of the RTF / PDF parsers so the Designer's preview
  stays consistent across sources.
* ``choices`` — one :class:`ParsedChoice` per ``<li class="multi-choice-item">``.
  ``is_correct`` comes from the ``<span class="correct-answer">``
  letter set in the answer block; an additional heuristic honours the
  ``correct-hidden`` CSS marker that the source HTML uses to flag
  most-voted choices, in case the answer span is empty.
* ``question_type`` — ``multi_choice`` when ``(Choose two./three./...)``
  appears in the stem or when multiple letters end up flagged
  correct; otherwise ``single_choice``.
* ``explanation`` — a plain-text rendition of the
  ``voting-summary`` block (``"Community vote distribution | B (98%) | ..."``),
  so the Designer's Reference field stays consistent with the RTF /
  PDF flows.
* ``images`` — not extracted as :class:`ParsedImage` bytes; saved HTML
  pages typically reference images via ``<img src="...">`` with
  external URLs, which the preview pipeline has no way to resolve
  without a network round-trip. Instead every ``<img>`` tag inside the
  stem, a choice ``<li>``, the answer block or the community-vote
  block is **inlined as a Markdown image tag** (``![](<src>)``) in the
  corresponding text field. That way the Runner can still show the
  image (the browser resolves the URL at render time) without the
  importer having to download anything. Images embedded via ``data:``
  URIs are kept verbatim — they are self-contained and already valid
  Markdown.

Hotspot cards (stems starting with ``HOTSPOT -`` and no ``<li>``
choices — the site renders the question as a screenshot of a table
and the answer as another screenshot) cannot be mapped to the full
:class:`posrat.models.hotspot.Hotspot` payload without OCR.  To keep
them importable at all we downgrade them to ``single_choice`` with
two synthetic choices: ``"Correct answer — see image below"`` flagged
correct, and ``"N/A"`` flagged incorrect. The original task image is
kept inside the stem (as Markdown) and the correct-answer image is
appended to the ``explanation`` so the Designer's Reference field
shows the answer without any special rendering logic. The user can
later upgrade the question to a real hotspot in the Designer if they
want to author the proper step-by-step structure.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
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

_log = logging.getLogger(__name__)


_QUESTION_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"Question\s*#\s*(\d+)",
    re.IGNORECASE,
)
"""Pulls the integer ``N`` out of a ``Question #<N>`` header.

The whitespace between ``Question``, ``#`` and the digits is lenient
(optional on both sides) so the pattern survives aggressive HTML
reformatting by minifiers.
"""


_MULTI_CHOICE_RE: Final[re.Pattern[str]] = re.compile(
    r"\(Choose (two|three|four|five)\.?\)",
    re.IGNORECASE,
)
"""Detects multi-choice cues in the question body.

Same set of spellings as the CertExam PDF parser — ``(Choose two.)``
through ``(Choose five.)`` with or without the trailing period —
keeps the heuristics aligned across RTF / PDF / HTML so an exam that
ships in multiple formats classifies identically.
"""


_WS_RUN_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")
"""Collapses multi-space / tab runs into a single space.

HTML indentation happily emits runs of 30+ spaces between tags; we
normalise them so choice and stem text stays readable in the preview
dialog without losing the single word separator.
"""


_HOTSPOT_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*HOTSPOT\s*[-–—:]?\s*",
    re.IGNORECASE,
)
"""Matches the ``HOTSPOT -`` / ``HOTSPOT –`` / ``HOTSPOT:`` prefix that
some practice-exam dumps use to mark hotspot questions. Case-insensitive
and tolerant of various dash characters (ASCII ``-``, en-dash, em-dash)
so a badly-copied stem still gets classified correctly.
"""


#: Placeholder choice texts used when we downgrade a hotspot card to a
#: pseudo single_choice. Pydantic's ``Choice`` validator requires at
#: least 2 non-empty choice texts; ``"N/A"`` is a universal "not
#: applicable" label that the Runner user will understand as a filler.
_HOTSPOT_CORRECT_LABEL: Final[str] = "Correct answer — see image below"
_HOTSPOT_NA_LABEL: Final[str] = "N/A"


def _collapse_inline_whitespace(text: str) -> str:
    """Trim each line and collapse internal whitespace to single spaces.

    Per-line trimming preserves the newlines injected by ``<br>`` while
    :data:`_WS_RUN_RE` removes the indentation runs HTML authors use
    for readability. The result is fit for
    :func:`normalize_paragraphs`, which expects one logical paragraph
    per blank-line-separated block.
    """

    lines = [_WS_RUN_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DOM-ish scraper built on stdlib html.parser (no lxml / beautifulsoup deps)
# ---------------------------------------------------------------------------


def _get_attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    """Return the value of attribute ``name`` from an ``attrs`` list.

    ``html.parser`` hands us attributes as ``list[(name, value)]``;
    this small helper keeps the call sites readable and tolerates the
    ``value is None`` case HTML5 allows for boolean attributes.
    """

    for attr_name, attr_value in attrs:
        if attr_name == name:
            return attr_value
    return None


def _has_class(
    attrs: list[tuple[str, str | None]], class_name: str
) -> bool:
    """Return ``True`` when ``class_name`` is one of the tag's classes.

    HTML attributes can carry multiple class tokens separated by
    whitespace (``class="card exam-question-card"``); simple substring
    matching would be brittle (``"question"`` would match both
    ``"question-body"`` and ``"exam-question-card"``), so we split and
    compare each token exactly.
    """

    cls = _get_attr(attrs, "class") or ""
    return class_name in cls.split()


class _QuestionCardExtractor(HTMLParser):
    """Collect one question card at a time from the HTML stream.

    The parser operates as a small state machine: it walks every tag,
    tracking the currently-open card / question-body / choice ``<li>``
    / answer block. When the outer ``<div class="exam-question-card">``
    closes, the accumulated state is flushed into :attr:`cards` and
    the state machine resets to await the next card.

    Each entry in :attr:`cards` is a plain ``dict`` with the keys:

    * ``header`` — raw text inside the ``card-header`` div (used to
      parse the ``Question #<N>`` number). Nested ``question-title-topic``
      span content is included but contributes nothing meaningful to
      the number parse.
    * ``stem`` — text collected from ``<p class="card-text">``
      (non-answer ``card-text`` only). ``<br>`` turns into ``\\n`` so
      the stem can be reflowed later.
    * ``choices`` — list of ``(letter, text, marked_correct)`` tuples
      in document order. ``marked_correct`` comes from the
      ``correct-hidden`` CSS class on the ``<li>``; it serves as a
      fallback when the answer block's ``<span class="correct-answer">``
      is empty.
    * ``answer_letters`` — letters found inside one or more
      ``<span class="correct-answer">`` elements in the answer block.
    * ``community`` — text collected from the ``voting-summary`` block.

    The collected dicts stay deliberately anaemic; promoting them into
    :class:`ParsedQuestion` is the caller's job so the transformation
    stays easy to unit-test.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self._reset_card()
        self._card_depth = 0
        self._header_depth = 0
        self._stem_depth = 0
        self._answer_depth = 0
        self._voting_depth = 0
        self._choice_li_depth = 0
        self._current_letter: str | None = None
        self._choice_has_correct_class = False
        # Text buffer for the currently-open card. Lists hold raw
        # strings that we join on card-close.
        self._header_buf: list[str] = []
        self._stem_buf: list[str] = []
        self._choice_buf: list[str] = []
        self._answer_letters: list[str] = []
        self._answer_image_srcs: list[str] = []
        self._voting_buf: list[str] = []
        # Scratch flag set when we enter a ``<span class="correct-answer">``
        # whose text should be appended to ``_answer_letters``.
        self._in_correct_answer_span = 0

    def _reset_card(self) -> None:
        self._header_buf = []
        self._stem_buf = []
        self._choices: list[tuple[str, list[str], bool]] = []
        self._answer_letters = []
        self._answer_image_srcs = []
        self._voting_buf = []

    # ------------------------------------------------------------------
    # HTMLParser callbacks
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Outer question card — opens a fresh collection context.
        if tag == "div" and _has_class(attrs, "exam-question-card"):
            self._card_depth = 1
            self._reset_card()
            return

        if self._card_depth == 0:
            return

        # Track nested div depth inside an open card so we know when
        # the outer card really closes (opening any other tag merely
        # bumps the depth of its own kind).
        if tag == "div":
            self._card_depth += 1

        if tag == "div" and _has_class(attrs, "card-header"):
            self._header_depth = self._card_depth

        if tag == "p" and _has_class(attrs, "card-text"):
            # ``question-answer`` is also a ``card-text`` paragraph —
            # we must not mistake it for the stem.
            if _has_class(attrs, "question-answer"):
                self._answer_depth = 1
            else:
                self._stem_depth = 1

        # The community-vote summary lives inside the answer paragraph.
        # Track it separately so its text can be harvested without the
        # "Correct Answer:" label bleeding into the stem or explanation.
        if tag == "div" and _has_class(attrs, "voting-summary"):
            self._voting_depth = self._card_depth

        if tag == "li" and _has_class(attrs, "multi-choice-item"):
            self._choice_li_depth = self._card_depth
            self._current_letter = None
            self._choice_has_correct_class = _has_class(
                attrs, "correct-hidden"
            )
            self._choice_buf = []

        if tag == "span" and _has_class(attrs, "multi-choice-letter"):
            letter = _get_attr(attrs, "data-choice-letter")
            if letter:
                self._current_letter = letter.strip().upper()

        if (
            tag == "span"
            and self._answer_depth > 0
            and _has_class(attrs, "correct-answer")
        ):
            self._in_correct_answer_span += 1

        # "Most Voted" badge inside a choice must not leak into the
        # choice text. We can recognise it and swallow its characters
        # by bumping a temporary depth counter and restoring on the
        # matching end tag — the simpler route is to check the class
        # in ``handle_data`` via a flag.
        if (
            tag == "span"
            and self._choice_li_depth > 0
            and (
                _has_class(attrs, "most-voted-answer-badge")
                or _has_class(attrs, "badge-success")
            )
        ):
            # Stash a sentinel so ``handle_data`` knows to drop the text.
            self._choice_badge_depth = getattr(
                self, "_choice_badge_depth", 0
            ) + 1

        # Break tags inside stems / choices must produce a newline in
        # the extracted text so the reflow step can tell sentences apart.
        if tag == "br":
            if self._stem_depth > 0:
                self._stem_buf.append("\n")
            elif self._choice_li_depth > 0:
                self._choice_buf.append("\n")

        # Inline <img> tags as Markdown image links — see module
        # docstring for why we do not attempt to download the bytes.
        # The same src is routed to the matching buffer based on the
        # currently-open context. In the correct-answer span the src
        # is kept in a separate list (``_answer_image_srcs``) so the
        # card-level logic can detect "answer is an image" and switch
        # the card to the hotspot-pseudo path without mis-reading the
        # Markdown as an answer letter.
        if tag == "img":
            src = _get_attr(attrs, "src") or ""
            if not src:
                return
            markdown = f"![]({src})"
            if self._in_correct_answer_span > 0:
                self._answer_image_srcs.append(src)
                return
            if self._voting_depth > 0:
                # Community-vote block rarely carries images, but if
                # it ever does, preserve them in the explanation.
                self._voting_buf.append(markdown)
                return
            if self._stem_depth > 0:
                self._stem_buf.append(markdown)
                return
            if self._choice_li_depth > 0:
                self._choice_buf.append(markdown)
                return

    def handle_endtag(self, tag: str) -> None:
        if self._card_depth == 0:
            return

        if tag == "span" and self._in_correct_answer_span > 0:
            self._in_correct_answer_span -= 1

        if (
            tag == "span"
            and self._choice_li_depth > 0
            and getattr(self, "_choice_badge_depth", 0) > 0
        ):
            self._choice_badge_depth -= 1

        if tag == "li" and self._choice_li_depth > 0:
            if self._card_depth == self._choice_li_depth:
                # Close this choice — flush the text buffer into the
                # card-level list.
                letter = self._current_letter
                text = "".join(self._choice_buf)
                self._choices.append(
                    (letter or "", [text], self._choice_has_correct_class)
                )
                self._choice_li_depth = 0
                self._current_letter = None
                self._choice_has_correct_class = False
                self._choice_buf = []

        if tag == "p":
            if self._stem_depth > 0:
                self._stem_depth = 0
            if self._answer_depth > 0:
                self._answer_depth = 0

        if tag == "div":
            # Close the matching constructs — in reverse order of how
            # they were opened. Ordering is safe because the HTML is
            # well-formed (the upstream saved file always closes
            # matching tags).
            if self._voting_depth and self._card_depth == self._voting_depth:
                self._voting_depth = 0
            if self._header_depth and self._card_depth == self._header_depth:
                self._header_depth = 0

            self._card_depth -= 1
            if self._card_depth == 0:
                # Card complete — flush into self.cards.
                self.cards.append(
                    {
                        "header": "".join(self._header_buf),
                        "stem": "".join(self._stem_buf),
                        "choices": list(self._choices),
                        "answer_letters": list(self._answer_letters),
                        "answer_image_srcs": list(self._answer_image_srcs),
                        "community": "".join(self._voting_buf),
                    }
                )
                self._reset_card()

    def handle_data(self, data: str) -> None:
        if self._card_depth == 0:
            return

        # Swallow the "Most Voted" badge text sitting inside a choice.
        if (
            self._choice_li_depth > 0
            and getattr(self, "_choice_badge_depth", 0) > 0
        ):
            return

        if self._in_correct_answer_span > 0:
            # Accumulate every letter we find — multi-answer pages use
            # one span per letter, so concatenation-then-split on the
            # answer side picks them all up cleanly.
            cleaned = data.strip()
            if cleaned:
                self._answer_letters.append(cleaned)
            return

        if self._header_depth > 0:
            self._header_buf.append(data)
            return

        if self._voting_depth > 0:
            self._voting_buf.append(data)
            return

        if self._stem_depth > 0:
            self._stem_buf.append(data)
            return

        if self._choice_li_depth > 0:
            # Skip the leading "A." chunk emitted by ``<span class="multi-choice-letter">``
            # — the letter already lives in ``self._current_letter``.
            if self._current_letter is not None:
                stripped = data.strip()
                if stripped == f"{self._current_letter}.":
                    return
            self._choice_buf.append(data)
            return


def _build_community_explanation(card: dict[str, object]) -> str | None:
    """Render the ``voting-summary`` block into a plain-text explanation.

    Extracted so both the normal and the hotspot-pseudo paths can
    reuse the same "Community vote distribution …" formatting
    without duplicated string handling.
    """

    community_raw = str(card.get("community", ""))
    community_clean = _collapse_inline_whitespace(community_raw)
    community_lines = [
        line for line in community_clean.splitlines() if line.strip()
    ]
    if not community_lines:
        return None
    if community_lines[0].lower().startswith("community vote"):
        return "\n".join(community_lines)
    return "Community vote distribution\n" + "\n".join(community_lines)


def _build_hotspot_pseudo_question(
    n: int,
    stem_text: str,
    answer_image_srcs: list[str],
    community_explanation: str | None,
) -> ParsedQuestion:
    """Wrap a hotspot card into a pseudo :class:`ParsedQuestion`.

    The source HTML stores the task as a scenario + list of possible
    steps in the stem (which we keep verbatim) plus a screenshot of a
    table inside the ``<span class="correct-answer">`` block. There is
    no structured option pool we can hand to the proper
    :class:`posrat.models.hotspot.Hotspot` payload; the only faithful
    representation is to keep the stem verbatim (the user sees the
    scenario text + task image), synthesise two choices so Pydantic's
    ``single_choice`` validator is satisfied — one correct (the "see
    image below" label) and one filler ``N/A`` — and append the
    answer image(s) to the explanation so the Designer's Reference
    field shows them.

    A warning is attached so the preview dialog marks the card with
    an amber icon, reminding the user that they can upgrade the
    question to a real hotspot in the Designer if they care about
    the structured form.
    """

    # Compose the explanation: existing community-vote block (if any)
    # followed by the answer image(s). The Markdown is rendered by the
    # Runner's existing question-text pipeline, so the user sees the
    # image without extra work.
    explanation_parts: list[str] = []
    if community_explanation:
        explanation_parts.append(community_explanation)
    explanation_parts.append("Correct answer:")
    for src in answer_image_srcs:
        explanation_parts.append(f"![]({src})")
    explanation = "\n\n".join(explanation_parts)

    choices = [
        ParsedChoice(
            letter="A",
            text=_HOTSPOT_CORRECT_LABEL,
            is_correct=True,
        ),
        ParsedChoice(
            letter="B",
            text=_HOTSPOT_NA_LABEL,
            is_correct=False,
        ),
    ]

    return ParsedQuestion(
        source_index=n,
        text=stem_text,
        choices=choices,
        question_type="single_choice",
        explanation=explanation,
        images=[],
        warnings=[
            "HOTSPOT question imported as pseudo single_choice —"
            " review the stem image and edit in the Designer if the"
            " structured hotspot form is desired."
        ],
    )


def _parse_card(card: dict[str, object]) -> ParsedQuestion | ParseError:
    """Promote one scraped card dict into :class:`ParsedQuestion`.

    Maintains the same validation contract as the RTF / PDF parsers:

    * The card must carry at least one choice. A card with zero
      ``<li class="multi-choice-item">`` entries degrades to
      :class:`ParseError` so the preview dialog surfaces the gap
      instead of silently importing an unusable question — **unless**
      the card is shaped as a hotspot (stem starts with ``HOTSPOT -``
      / the ``<span class="correct-answer">`` carries only an
      ``<img>``), in which case we build a synthetic single_choice
      so the card survives into the preview and the user can decide
      whether to import it as-is.
    * The card must carry at least one correct-answer letter. Empty
      ``<span class="correct-answer">`` blocks are tolerated when the
      ``correct-hidden`` marker on one or more ``<li>`` provides the
      fallback — this matches the source HTML, where the answer block
      is sometimes omitted but the choice markup still flags the
      most-voted option.
    """

    header_text = str(card.get("header", ""))
    match = _QUESTION_NUMBER_RE.search(header_text)
    if not match:
        return ParseError(
            source_range="Question #?",
            reason="missing or unrecognised Question # header",
        )
    n = int(match.group(1))

    # ---- stem ---------------------------------------------------------
    stem_raw = str(card.get("stem", ""))
    stem_text = normalize_paragraphs(_collapse_inline_whitespace(stem_raw))

    community_explanation = _build_community_explanation(card)

    # ---- hotspot detection -------------------------------------------
    # Three structural signals point at a hotspot card; any two are
    # enough. A stem that only *mentions* "hotspot" in prose without
    # the structural shape falls through to the normal single/multi
    # path unchanged.
    answer_image_srcs_raw = card.get("answer_image_srcs") or []
    assert isinstance(answer_image_srcs_raw, list)
    answer_image_srcs = [str(s) for s in answer_image_srcs_raw if s]

    raw_choices = card.get("choices") or []
    assert isinstance(raw_choices, list)

    answer_letters_raw = card.get("answer_letters") or []
    assert isinstance(answer_letters_raw, list)
    has_text_answer = any(
        str(t).strip() for t in answer_letters_raw
    )

    stem_marks_hotspot = bool(_HOTSPOT_PREFIX_RE.match(stem_text))
    has_choices = bool(raw_choices)
    answer_is_image_only = bool(answer_image_srcs) and not has_text_answer

    signals = sum(
        [stem_marks_hotspot, not has_choices, answer_is_image_only]
    )
    if signals >= 2:
        return _build_hotspot_pseudo_question(
            n=n,
            stem_text=stem_text,
            answer_image_srcs=answer_image_srcs,
            community_explanation=community_explanation,
        )

    question_type: str = "single_choice"
    if _MULTI_CHOICE_RE.search(stem_text):
        question_type = "multi_choice"

    # ---- choices ------------------------------------------------------
    choices: list[ParsedChoice] = []
    for raw in raw_choices:
        # Each entry is ``(letter, [text], marked_correct_class)``.
        if not isinstance(raw, tuple) or len(raw) != 3:
            continue
        letter, text_parts, marked_correct = raw
        if not letter:
            continue
        joined = " ".join(text_parts) if isinstance(text_parts, list) else str(text_parts)
        text = _collapse_inline_whitespace(joined)
        text = " ".join(text.split())  # final fold of newlines -> spaces
        choices.append(
            ParsedChoice(letter=str(letter), text=text, is_correct=False)
        )

    if not choices:
        return ParseError(source_range=f"Q{n}", reason="no choices found")

    # ---- answer letters ---------------------------------------------
    answer_letters: list[str] = []
    for token in card.get("answer_letters") or []:  # type: ignore[union-attr]
        token_str = str(token).strip().upper()
        if not token_str:
            continue
        # ``<span class="correct-answer">B</span>`` yields ``B``; a
        # multi-answer page with two spans yields ``["B", "D"]``. If a
        # single span holds ``"BD"`` or ``"B, D"`` we also break it down
        # so downstream code sees an uniform letter list.
        for letter in re.split(r"[,\s]+", token_str):
            if len(letter) == 1 and letter.isalpha():
                answer_letters.append(letter)
            elif len(letter) > 1 and letter.isalpha():
                answer_letters.extend(list(letter))

    # Fallback: infer correctness from the ``correct-hidden`` class on
    # the ``<li>`` when the answer block didn't hand us any letters.
    if not answer_letters:
        for raw in raw_choices:
            if not (isinstance(raw, tuple) and len(raw) == 3):
                continue
            letter, _text, marked_correct = raw
            if marked_correct and letter:
                answer_letters.append(str(letter).upper())

    if not answer_letters:
        return ParseError(
            source_range=f"Q{n}",
            reason="no Correct Answer found (no <span class='correct-answer'> and no correct-hidden marker)",
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

    # ---- multi/single promotion based on the correct-letter count ---
    # A stem without ``(Choose two.)`` may still be multi-choice when
    # the source tags two+ letters as correct (rare but it happens);
    # honour the data over the cue.
    if question_type == "single_choice" and sum(
        1 for c in choices if c.is_correct
    ) > 1:
        question_type = "multi_choice"

    # ---- explanation (community vote block) -------------------------
    # Reuses the helper shared with the hotspot branch so both paths
    # format the Reference field identically.
    explanation = community_explanation

    return ParsedQuestion(
        source_index=n,
        text=stem_text,
        choices=choices,
        question_type=question_type,  # type: ignore[arg-type]
        explanation=explanation,
        images=[],
        warnings=warnings,
    )


class HtmlQuestionsParser:
    """ImportSource for saved practice-exam HTML pages.

    The parser is registered at module-import time via
    :func:`register_import_source` so simply adding
    ``import posrat.importers.html_questions`` anywhere makes it
    available in the Designer's bulk-import dropdown.
    """

    source_id = "html_questions"
    display_name = "Practice-exam HTML"
    file_extensions = (".html", ".htm")

    def parse(self, path: Path) -> ParseResult:
        raw = path.read_bytes()
        # ``errors="replace"`` keeps the parser bulletproof against the
        # occasional stray non-UTF-8 byte in saved pages (some sites
        # mix cp1252 quotes into otherwise UTF-8 output).
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        extractor = _QuestionCardExtractor()
        extractor.feed(text)
        extractor.close()

        questions: list[ParsedQuestion] = []
        parse_errors: list[ParseError] = []

        for card in extractor.cards:
            result = _parse_card(card)
            if isinstance(result, ParseError):
                parse_errors.append(result)
            else:
                questions.append(result)

        return ParseResult(
            questions=questions,
            parse_errors=parse_errors,
            source_metadata={"source_file": str(path)},
        )


register_import_source(HtmlQuestionsParser())
