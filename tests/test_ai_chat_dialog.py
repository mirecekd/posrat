"""Tests for :mod:`posrat.ai.chat_dialog` — enrichment wiring.

The chat dialog is a NiceGUI UI surface that's cumbersome to unit
test end-to-end (the bubble rendering, modal open/close and stream
buffering are all handled by NiceGUI + async Strands internals that
need a live browser session). These tests focus on the parts that
*are* trivially verifiable without spinning up NiceGUI:

* :data:`posrat.ai.config.DEFAULT_ENRICH_PROMPT` exists, is a
  non-empty string, and instructs the model to produce a concrete
  markdown template with the agreed structure (Correct Answer /
  Justification / Why the other answers are wrong). If somebody
  blanks it out or drops the structural anchors, the Auto-enrich
  button silently degrades to a free-form reply that breaks the
  Explanation/Reference rendering downstream.
* The prompt explicitly forbids the formatting traps the operator
  cares about: en-dash / em-dash, Unicode icons, missing blank
  lines after headings, and non-clickable references.
* :mod:`posrat.ai.chat_dialog` exports :func:`render_chat_dialog`
  and imports without blowing up even when NiceGUI + Strands are
  only partially available (lazy import is fine, but the module
  must parse).

Full interaction coverage (streaming → auto-commit) is out of scope
for a unit test; it's verified manually through the Designer
smoke-check.
"""

from __future__ import annotations

from posrat.ai.config import DEFAULT_ENRICH_PROMPT


def test_default_enrich_prompt_is_meaningful():
    assert isinstance(DEFAULT_ENRICH_PROMPT, str)
    stripped = DEFAULT_ENRICH_PROMPT.strip()
    assert stripped, "DEFAULT_ENRICH_PROMPT must not be blank"
    lowered = stripped.lower()
    # Must talk about verifying the correct answer and must mention
    # AWS — the Auto-enrich button's entire reason to exist is to
    # cross-check the question against AWS docs.
    assert "correct answer" in lowered
    assert "aws" in lowered


def test_default_enrich_prompt_mentions_storage_intent():
    # The reply is stored verbatim as Explanation/Reference, so the
    # prompt must instruct the model to be concise — otherwise we
    # end up with 2000-word answers nobody wants to read during
    # review.
    lowered = DEFAULT_ENRICH_PROMPT.lower()
    assert "concise" in lowered or "brief" in lowered
    assert "explanation" in lowered or "reference" in lowered


def test_default_enrich_prompt_carries_template_structure():
    # The prompt must show the model the exact markdown anchors the
    # Designer agreed on. Missing any of these would let the LLM
    # drift into freeform prose, which breaks downstream rendering
    # in the Explanation/Reference field.
    text = DEFAULT_ENRICH_PROMPT
    assert "## Correct Answer:" in text
    assert "### Justification" in text
    assert "### Why the other answers are wrong" in text
    # The horizontal rule between sections must be present so the
    # model copies the visual separators verbatim.
    assert "\n---\n" in text


def test_default_enrich_prompt_contains_no_unicode_dashes():
    # Operator rule: only the plain ASCII hyphen is allowed in
    # Auto-enrich output. The prompt itself must therefore avoid
    # en-dash (U+2013) and em-dash (U+2014); otherwise the model
    # cargo-cults them into the reply.
    assert "\u2013" not in DEFAULT_ENRICH_PROMPT  # en-dash
    assert "\u2014" not in DEFAULT_ENRICH_PROMPT  # em-dash


def test_default_enrich_prompt_forbids_dashes_and_icons_explicitly():
    lowered = DEFAULT_ENRICH_PROMPT.lower()
    # Explicit "no en-dash / em-dash" instruction must reach the
    # model — relying on the absence in the prompt body is not
    # enough because the LLM has its own typographical priors.
    assert "en-dash" in lowered
    assert "em-dash" in lowered
    # Same story for Unicode icons / emoji — has to be a verbal
    # ban so the model doesn't sprinkle ✓ / ✗ / arrows around.
    assert "emoji" in lowered or "unicode icons" in lowered


def test_default_enrich_prompt_requires_clickable_references():
    lowered = DEFAULT_ENRICH_PROMPT.lower()
    # Either bullet form (- Reference: https://...) or inline
    # markdown links must be advertised. Both spellings are
    # accepted; the rule is that the model never writes "see the
    # AWS docs" without a URL.
    assert "https://" in DEFAULT_ENRICH_PROMPT
    assert "clickable" in lowered or "reference:" in lowered


def test_default_enrich_prompt_forbids_preamble():
    # The reply lands verbatim in the Explanation/Reference field.
    # If the model precedes the template with "Here is the answer:"
    # or any other lead-in, that prose ends up rendered above the
    # heading inside the question card. Guard against drift by
    # asserting the prompt explicitly bans the preamble pattern and
    # mandates that the reply starts with the heading.
    text = DEFAULT_ENRICH_PROMPT
    lowered = text.lower()
    assert "## correct answer:" in lowered
    assert "preamble" in lowered
    # The "first character must be #" rule is the strongest signal
    # to the model — if it disappears, anything goes.
    assert "first character" in lowered


def test_default_enrich_prompt_pins_english_output():

    # Explanations are stored as study material for English-language
    # AWS exams, so the prompt must override the system prompt's
    # "answer in the same language as the question" rule.
    lowered = DEFAULT_ENRICH_PROMPT.lower()
    assert "english" in lowered


def test_chat_dialog_module_imports():
    # Lazy-import safety check — the dialog module pulls in NiceGUI
    # + Strands indirectly, but must at least parse cleanly so the
    # AI widget entry points can defer-import it on click.
    from posrat.ai import chat_dialog  # noqa: F401

    assert hasattr(chat_dialog, "render_chat_dialog")
