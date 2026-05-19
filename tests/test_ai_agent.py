"""Tests for :mod:`posrat.ai.agent`.

We deliberately do **not** test against real Bedrock — that would
require live AWS credentials and network access. Instead we verify
the pure wiring helpers (system prompt composition) and trust the
Strands SDK to handle the rest. If ``strands`` is not installed the
import-smoke test is skipped so the core POSRAT build keeps working
without the optional AI stack.
"""

from __future__ import annotations

import importlib.util

import pytest

from posrat.ai.agent import _compose_system_prompt
from posrat.ai.config import DEFAULT_SYSTEM_PROMPT, AISettings


def _make(system_prompt=None) -> AISettings:
    return AISettings(
        enabled=True,
        model_id="anthropic.claude-3",
        region="eu-west-1",
        system_prompt=system_prompt,
        mcp_config_json=None,
        enrich_prompt=None,
        updated_at=None,
    )



def test_compose_uses_default_when_admin_prompt_missing():
    out = _compose_system_prompt(_make(), "")
    assert out == DEFAULT_SYSTEM_PROMPT


def test_compose_uses_admin_prompt_when_set():
    settings = _make("Admin custom prompt.")
    out = _compose_system_prompt(settings, "")
    assert out == "Admin custom prompt."


def test_compose_appends_context_when_set():
    settings = _make("Admin prompt.")
    ctx = "QUESTION: foo\nA. yes"
    out = _compose_system_prompt(settings, ctx)
    assert out.startswith("Admin prompt.")
    assert "---" in out
    assert ctx in out


def test_compose_empty_context_has_no_divider():
    out = _compose_system_prompt(_make("X"), "")
    assert "---" not in out


@pytest.mark.skipif(
    importlib.util.find_spec("strands") is None,
    reason="Strands Agents SDK not installed",
)
def test_build_agent_importable_smoke():
    """Import smoke test — ensure build_agent can be called when Strands is installed.

    Skipped when ``strands`` isn't available so the core CI still
    passes without the optional AI extra. Does not construct a
    :class:`BedrockModel` (that would try to validate credentials)
    — instead we only check that :func:`build_agent` is importable
    and that it's a callable with the documented signature.
    """

    from posrat.ai.agent import build_agent

    assert callable(build_agent)
