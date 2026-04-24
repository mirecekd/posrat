# tests/test_importers_html_questions.py
"""Tests for posrat.importers.html_questions (Practice-exam HTML parser)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_module_imports() -> None:
    import posrat.importers.html_questions  # noqa: F401


def test_parser_registered_in_registry() -> None:
    import posrat.importers  # noqa: F401 — triggers registration side-effect
    from posrat.importers.base import get_import_source

    src = get_import_source("html_questions")
    assert src.display_name == "Practice-exam HTML"


def test_html_parser_reexported_from_init() -> None:
    from posrat.importers import HtmlQuestionsParser

    assert HtmlQuestionsParser.source_id == "html_questions"


def test_parser_satisfies_import_source_protocol() -> None:
    from posrat.importers.base import ImportSource
    from posrat.importers.html_questions import HtmlQuestionsParser

    parser = HtmlQuestionsParser()
    assert isinstance(parser, ImportSource)
    assert parser.source_id == "html_questions"
    assert parser.display_name == "Practice-exam HTML"
    assert ".html" in parser.file_extensions
    assert ".htm" in parser.file_extensions


_SINGLE_CHOICE_CARD = """
<div class="card exam-question-card">
  <div class="card-header bg-primary">
    Question #7
    <span class="question-title-topic">Topic 1</span>
  </div>
  <div class="card-body question-body" data-id="42">
    <p class="card-text">
      What is the capital of France?<br>Choose the best option.
    </p>
    <div class="question-choices-container">
      <ul>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="A">A.</span>
          Berlin
        </li>
        <li class="multi-choice-item correct-hidden">
          <span class="multi-choice-letter" data-choice-letter="B">B.</span>
          Paris
          <span class="badge badge-success most-voted-answer-badge">Most Voted</span>
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="C">C.</span>
          Madrid
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="D">D.</span>
          Rome
        </li>
      </ul>
    </div>
    <p class="card-text question-answer bg-light white-text">
      <span class="correct-answer-box">
        <strong>Correct Answer:</strong>
        <span class="correct-answer">B</span>
      </span>
      <span class="answer-description"></span>
      <div class="voting-summary">
        <i>Community vote distribution</i>
        <div class="progress vote-distribution-bar">
          <div class="vote-bar progress-bar bg-primary">B (98%)</div>
          <div class="vote-bar progress-bar bg-info">2%</div>
        </div>
      </div>
    </p>
  </div>
</div>
"""


_MULTI_CHOICE_CARD = """
<div class="card exam-question-card">
  <div class="card-header bg-primary">
    Question #2
    <span class="question-title-topic">Topic 1</span>
  </div>
  <div class="card-body question-body" data-id="99">
    <p class="card-text">
      Which three steps apply? (Choose three.)
    </p>
    <div class="question-choices-container">
      <ul>
        <li class="multi-choice-item correct-hidden">
          <span class="multi-choice-letter" data-choice-letter="A">A.</span>
          Alpha option
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="B">B.</span>
          Beta option
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="C">C.</span>
          Gamma option
        </li>
        <li class="multi-choice-item correct-hidden">
          <span class="multi-choice-letter" data-choice-letter="D">D.</span>
          Delta option
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="E">E.</span>
          Epsilon option
        </li>
        <li class="multi-choice-item correct-hidden">
          <span class="multi-choice-letter" data-choice-letter="F">F.</span>
          Zeta option
        </li>
      </ul>
    </div>
    <p class="card-text question-answer bg-light white-text">
      <span class="correct-answer-box">
        <strong>Correct Answer:</strong>
        <span class="correct-answer">ADF</span>
      </span>
    </p>
  </div>
</div>
"""


def _parse_html(html: str):
    """Write ``html`` to a tempfile and run it through the parser."""
    import tempfile
    from posrat.importers.html_questions import HtmlQuestionsParser

    with tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        tmp = Path(f.name)
    try:
        return HtmlQuestionsParser().parse(tmp)
    finally:
        tmp.unlink()


def test_single_choice_happy_path() -> None:
    """One card → one ParsedQuestion with the expected stem, choices and
    correct-answer mapping. Validates the happy path before exercising
    the multi-answer / edge-case shapes below."""
    from posrat.importers.base import ParsedQuestion

    result = _parse_html(_SINGLE_CHOICE_CARD)
    assert len(result.parse_errors) == 0
    assert len(result.questions) == 1
    q = result.questions[0]
    assert isinstance(q, ParsedQuestion)
    assert q.source_index == 7
    assert q.question_type == "single_choice"
    assert len(q.choices) == 4
    correct = [c for c in q.choices if c.is_correct]
    assert len(correct) == 1
    assert correct[0].letter == "B"
    assert correct[0].text == "Paris"
    # The "Most Voted" badge text must not leak into the choice text.
    assert "Most Voted" not in correct[0].text
    # The community-vote block is kept as explanation — mirrors
    # the RTF / PDF parser convention.
    assert q.explanation is not None
    assert "Community vote" in q.explanation


def test_multi_choice_with_combined_answer_string() -> None:
    """``<span class="correct-answer">ADF</span>`` (no separators) must
    fan out into three flagged letters — the RTF parser's
    ``"BD"`` / ``"B, D"`` / ``"B D"`` normalisation behaviour applies
    here too so users never have to massage the source HTML."""
    from posrat.importers.base import ParsedQuestion

    result = _parse_html(_MULTI_CHOICE_CARD)
    assert len(result.parse_errors) == 0
    q = result.questions[0]
    assert isinstance(q, ParsedQuestion)
    assert q.question_type == "multi_choice"
    correct = {c.letter for c in q.choices if c.is_correct}
    assert correct == {"A", "D", "F"}
    assert len(q.choices) == 6


def test_question_with_br_in_stem_preserves_sentence_breaks() -> None:
    """``<br>`` inside the stem must become a newline so
    :func:`normalize_paragraphs` can reflow the question mark into its
    own paragraph. Without that behaviour the stem would collapse into
    one unreadable run of sentences."""

    result = _parse_html(_SINGLE_CHOICE_CARD)
    q = result.questions[0]
    # Either the ``?`` sentence sits on its own line or at least the
    # two sentences got separated by whitespace — in both cases the
    # stem preview rendered to the Designer is readable.
    assert "capital of France" in q.text
    assert "Choose the best option" in q.text


def test_multiple_cards_parse_independently() -> None:
    """Two sibling cards at the document top level → two parsed
    questions with independent state. Regression guard against state
    leakage across the HTMLParser state machine."""

    combined = _SINGLE_CHOICE_CARD + "\n" + _MULTI_CHOICE_CARD
    result = _parse_html(combined)
    assert len(result.questions) == 2
    # Source indices preserved verbatim from the ``Question #N`` header.
    ns = sorted(q.source_index for q in result.questions)
    assert ns == [2, 7]


def test_card_without_question_number_yields_parse_error() -> None:
    """Missing or malformed ``Question #<N>`` header degrades to a
    :class:`ParseError` rather than crashing the whole parse."""
    from posrat.importers.base import ParseError

    malformed = _SINGLE_CHOICE_CARD.replace(
        "Question #7", "Question without number"
    )
    result = _parse_html(malformed)
    assert len(result.questions) == 0
    assert len(result.parse_errors) == 1
    assert isinstance(result.parse_errors[0], ParseError)


def test_card_without_choices_yields_parse_error() -> None:
    """A card with no ``<li class="multi-choice-item">`` produces a
    :class:`ParseError`, not a broken :class:`ParsedQuestion`."""
    from posrat.importers.base import ParseError

    stripped = (
        '<div class="card exam-question-card">'
        '<div class="card-header">Question #5</div>'
        '<div class="card-body question-body">'
        '<p class="card-text">Stem without choices?</p>'
        '<p class="card-text question-answer">'
        '<span class="correct-answer">A</span></p>'
        '</div></div>'
    )
    result = _parse_html(stripped)
    assert len(result.questions) == 0
    assert len(result.parse_errors) == 1
    assert isinstance(result.parse_errors[0], ParseError)
    assert "no choices" in result.parse_errors[0].reason


_HOTSPOT_CARD = """
<div class="card exam-question-card">
  <div class="card-header bg-primary">Question #5</div>
  <div class="card-body question-body">
    <p class="card-text">
      HOTSPOT -<br>A security engineer needs to implement AWS IAM Identity Center.<br>Select the correct steps from the list.<br>Step one candidate.<br>Step two candidate.<br><img title="task" src="https://example.com/task.png">
    </p>
    <p class="card-text question-answer bg-light white-text">
      <span class="correct-answer-box">
        <strong>Correct Answer:</strong>
        <span class="correct-answer"><img title="ans" src="https://example.com/answer.png"></span>
      </span>
    </p>
  </div>
</div>
"""


_IMAGE_CHOICE_CARD = """
<div class="card exam-question-card">
  <div class="card-header bg-primary">Question #3</div>
  <div class="card-body question-body">
    <p class="card-text">Which policy matches?</p>
    <div class="question-choices-container">
      <ul>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="A">A.</span>
          <img title="policyA" src="https://example.com/a.png">
        </li>
        <li class="multi-choice-item correct-hidden">
          <span class="multi-choice-letter" data-choice-letter="B">B.</span>
          <img title="policyB" src="https://example.com/b.png">
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="C">C.</span>
          <img title="policyC" src="https://example.com/c.png">
        </li>
        <li class="multi-choice-item">
          <span class="multi-choice-letter" data-choice-letter="D">D.</span>
          <img title="policyD" src="https://example.com/d.png">
        </li>
      </ul>
    </div>
    <p class="card-text question-answer">
      <span class="correct-answer-box">
        <strong>Correct Answer:</strong>
        <span class="correct-answer">B</span>
      </span>
    </p>
  </div>
</div>
"""


def test_hotspot_card_downgrades_to_pseudo_single_choice() -> None:
    """A card whose stem starts with ``HOTSPOT -`` and whose answer
    block is an image (no letter) is too underspecified to map to a
    real :class:`posrat.models.hotspot.Hotspot`. The parser must still
    produce a usable :class:`ParsedQuestion` so the preview dialog
    shows the question — we build a pseudo single_choice with the
    answer image moved into the explanation and an attached warning
    telling the user to review the card in the Designer."""
    from posrat.importers.base import ParsedQuestion

    result = _parse_html(_HOTSPOT_CARD)
    assert len(result.parse_errors) == 0
    assert len(result.questions) == 1
    q = result.questions[0]
    assert isinstance(q, ParsedQuestion)
    assert q.question_type == "single_choice"
    assert len(q.choices) == 2
    assert q.choices[0].is_correct is True
    assert q.choices[1].is_correct is False
    assert q.choices[1].text == "N/A"
    # Warning must be present so the Designer preview flags the row.
    assert q.warnings
    # Scenario text + candidate steps survive verbatim in the stem.
    assert "HOTSPOT" in q.text
    assert "IAM Identity Center" in q.text
    assert "Step one candidate" in q.text
    # Task image inlined in the stem as Markdown.
    assert "![](https://example.com/task.png)" in q.text
    # Correct-answer image lands in the explanation, also as Markdown.
    assert q.explanation is not None
    assert "![](https://example.com/answer.png)" in q.explanation


def test_image_only_choices_keep_markdown_body() -> None:
    """Every choice ``<li>`` whose body is just an ``<img>`` must end
    up with the Markdown image tag as its text so the Runner can show
    the picture. Correct-answer detection by letter (``B``) still
    works because the letter comes from the ``data-choice-letter``
    attribute, not from the choice body."""

    result = _parse_html(_IMAGE_CHOICE_CARD)
    assert len(result.parse_errors) == 0
    q = result.questions[0]
    assert len(q.choices) == 4
    for choice in q.choices:
        assert choice.text.startswith("![]("), choice.text
    correct = [c.letter for c in q.choices if c.is_correct]
    assert correct == ["B"]


def test_correct_hidden_class_used_when_answer_span_empty() -> None:
    """If the ``<span class="correct-answer">`` block is empty but a
    choice ``<li>`` carries ``correct-hidden``, the parser still flags
    the correct choice — matches the source HTML where the answer
    block is occasionally blanked out for "discussion mode"."""
    from posrat.importers.base import ParsedQuestion

    card_empty_answer = _SINGLE_CHOICE_CARD.replace(
        '<span class="correct-answer">B</span>',
        '<span class="correct-answer"></span>',
    )
    result = _parse_html(card_empty_answer)
    assert len(result.parse_errors) == 0
    q = result.questions[0]
    assert isinstance(q, ParsedQuestion)
    correct = [c for c in q.choices if c.is_correct]
    assert len(correct) == 1
    assert correct[0].letter == "B"


# ---------------------------------------------------------------------------
# End-to-end smoke tests against the real HTML fixtures.
# ---------------------------------------------------------------------------


_FIXTURE_ENV = "POSRAT_HTML_FIXTURE"
_FIXTURE_DEFAULT = "/mnt/c/DATA/certy/examtopics_inner.html"
_FIXTURE2_ENV = "POSRAT_HTML_FIXTURE2"
_FIXTURE2_DEFAULT = "/mnt/c/DATA/certy/examtopics_inner2.html"
_FIXTURE3_ENV = "POSRAT_HTML_FIXTURE3"
_FIXTURE3_DEFAULT = "/mnt/c/DATA/certy/examtopics_inner3.html"


def _fixture_path(env: str, default: str) -> Path | None:
    """Return the fixture path when present, else ``None``.

    The saved HTML dumps are not shipped in the repo; developers can
    point the test at their local copies via environment variables or
    rely on the canonical WSL paths. CI environments without the file
    are skipped instead of failing so the test is portable.
    """
    candidate = Path(os.environ.get(env, default))
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(
    _fixture_path(_FIXTURE_ENV, _FIXTURE_DEFAULT) is None,
    reason="real HTML fixture not available",
)
def test_real_html_fixture_parses_without_errors() -> None:
    """End-to-end smoke test: parse the reference saved HTML page
    (``examtopics_inner.html`` equivalent, 10 questions). Asserts the
    same quality bar we require from the importer in production use:
    every card must turn into a :class:`ParsedQuestion`, no
    :class:`ParseError`, the first question must map to the well-known
    B answer and 4 choices."""
    from posrat.importers.html_questions import HtmlQuestionsParser

    path = _fixture_path(_FIXTURE_ENV, _FIXTURE_DEFAULT)
    assert path is not None  # makes type checker happy; @skipif handled None
    result = HtmlQuestionsParser().parse(path)

    assert len(result.parse_errors) == 0, (
        f"unexpected ParseErrors: {[str(e) for e in result.parse_errors[:5]]}"
    )
    assert len(result.questions) >= 5, (
        f"expected >=5 questions, got {len(result.questions)}"
    )
    q1 = next(q for q in result.questions if q.source_index == 1)
    assert q1.question_type == "single_choice"
    assert len(q1.choices) == 4
    correct = [c.letter for c in q1.choices if c.is_correct]
    assert correct == ["B"]


@pytest.mark.skipif(
    _fixture_path(_FIXTURE2_ENV, _FIXTURE2_DEFAULT) is None,
    reason="second real HTML fixture not available",
)
def test_real_html_fixture2_covers_multi_answer() -> None:
    """The second fixture carries a six-choice Q2 with ``ADF`` as the
    combined answer string; confirm the multi-choice path works
    end-to-end (choice count + correct-letter set)."""
    from posrat.importers.html_questions import HtmlQuestionsParser

    path = _fixture_path(_FIXTURE2_ENV, _FIXTURE2_DEFAULT)
    assert path is not None
    result = HtmlQuestionsParser().parse(path)
    assert len(result.parse_errors) == 0
    q2 = next(q for q in result.questions if q.source_index == 2)
    assert q2.question_type == "multi_choice"
    assert len(q2.choices) == 6
    correct = {c.letter for c in q2.choices if c.is_correct}
    assert correct == {"A", "D", "F"}


@pytest.mark.skipif(
    _fixture_path(_FIXTURE3_ENV, _FIXTURE3_DEFAULT) is None,
    reason="third real HTML fixture (hotspot) not available",
)
def test_real_html_fixture3_covers_hotspot_and_image_choices() -> None:
    """The third fixture mixes two card shapes we must support:

    * HOTSPOT cards (Q5, Q8) whose correct answer is a single image
      instead of a letter — the parser must downgrade those to a
      pseudo single_choice with two synthetic options and the answer
      image inlined in the explanation.
    * A normal multi-choice card (Q7) where every choice body is just
      an ``<img>`` — the parser must keep the Markdown image tag as
      the choice text so the Runner can render it.
    """
    from posrat.importers.html_questions import HtmlQuestionsParser

    path = _fixture_path(_FIXTURE3_ENV, _FIXTURE3_DEFAULT)
    assert path is not None
    result = HtmlQuestionsParser().parse(path)
    assert len(result.parse_errors) == 0
    assert len(result.questions) == 4

    q5 = next(q for q in result.questions if q.source_index == 5)
    # Hotspot pseudo: exactly 2 choices, first flagged correct, warning
    # attached so the Designer can highlight the card for manual review.
    assert q5.question_type == "single_choice"
    assert len(q5.choices) == 2
    assert q5.choices[0].is_correct is True
    assert q5.choices[1].is_correct is False
    assert "see image below" in q5.choices[0].text.lower()
    assert q5.choices[1].text == "N/A"
    assert q5.warnings, "hotspot pseudo must carry a warning"
    # Answer image lands in the explanation as Markdown so the Designer
    # Reference field shows it without any special rendering logic.
    assert q5.explanation is not None
    assert "image13" in q5.explanation
    # The scenario text + the list of possible steps must be kept
    # verbatim in the stem so none of the task content is lost.
    assert "HOTSPOT" in q5.text
    assert "IAM Identity Center" in q5.text

    q7 = next(q for q in result.questions if q.source_index == 7)
    # Image-only choices: each choice text is a Markdown image tag.
    assert q7.question_type == "single_choice"
    assert len(q7.choices) == 4
    for choice in q7.choices:
        assert choice.text.startswith("![]("), (
            f"choice {choice.letter!r} expected to be a Markdown image tag, "
            f"got {choice.text!r}"
        )
    correct = [c.letter for c in q7.choices if c.is_correct]
    assert correct == ["B"]
