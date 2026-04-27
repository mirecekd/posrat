"""Tests for :mod:`posrat.ai.chat_dialog` — enrichment wiring.

The chat dialog is a NiceGUI UI surface that's cumbersome to unit
test end-to-end (the bubble rendering, modal open/close and stream
buffering are all handled by NiceGUI + async Strands internals that
need a live browser session). These tests focus on the two things
that *are* trivially verifiable without spinning up NiceGUI:

* :data:`posrat.ai.config.DEFAULT_ENRICH_PROMPT` exists, is a
  non-empty string, and mentions "AWS" + "correct answer" — the two
  must-haves for the canned prompt to do its job. If somebody
  accidentally blanks it out the Auto-enrich button silently
  degrades to "send an empty user message" which would be a
  confusing failure mode.
* :mod:`posrat.ai.chat_dialog` exports :func:`render_chat_dialog`
  and — critically — imports without blowing up even when NiceGUI
  + Strands are only partially available (lazy import is fine, but
  the module must parse).

Full interaction coverage (streaming → auto-commit) is out of scope
for a unit test; it's verified manually through the Designer
smoke-check and will be re-validated once Cline adds a NiceGUI
headless fixture.
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


def test_chat_dialog_module_imports():
    # Lazy-import safety check — the dialog module pulls in NiceGUI
    # + Strands indirectly, but must at least parse cleanly so the
    # AI widget entry points can defer-import it on click.
    from posrat.ai import chat_dialog  # noqa: F401

    assert hasattr(chat_dialog, "render_chat_dialog")
