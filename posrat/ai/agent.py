"""Strands agent factory and chat-turn runner.

Exposes two public helpers:

* :func:`build_agent` — given :class:`AISettings` + optional question
  context string + a pre-collected list of MCP tool handles, returns a
  fresh :class:`strands.Agent` wired to :class:`strands.models.BedrockModel`.
  Credentials come from the default boto3 chain (``AWS_PROFILE`` /
  env vars / IMDS / SSO cache).

* :func:`run_chat_turn` — async generator that opens every MCP client
  (``with`` stack), builds the agent, feeds the past conversation into
  :attr:`Agent.messages`, and yields text deltas from
  :meth:`Agent.stream_async` for the new user prompt. Returns the full
  message trace at the end so the caller can persist the updated
  conversation.

All Strands / boto3 / mcp imports are **lazy** — the module imports
cheaply so tests that don't hit the LLM can touch :mod:`posrat.ai`
without pulling the whole agent stack.

Tests do not exercise the real Bedrock path (that would hit the
network and require live credentials). :mod:`tests.test_ai_agent`
covers the shape-and-wiring with dummy stubs instead.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, AsyncIterator, List, Optional

from posrat.ai.config import DEFAULT_SYSTEM_PROMPT, AISettings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from strands import Agent
    from strands.tools.mcp import MCPClient


def _compose_system_prompt(
    settings: AISettings,
    question_context: str,
) -> str:
    """Glue the admin system prompt + per-question context block.

    Returns just the admin prompt when ``question_context`` is empty
    (header-button generic chat); otherwise appends a separator and
    the context block so the LLM sees both.
    """

    base = settings.system_prompt or DEFAULT_SYSTEM_PROMPT
    if not question_context:
        return base
    return f"{base}\n\n---\n\n{question_context}"


def build_agent(
    settings: AISettings,
    question_context: str = "",
    mcp_tools: Optional[list] = None,
    prior_messages: Optional[list] = None,
) -> "Agent":
    """Build a fresh :class:`Agent` for a single chat interaction.

    Args:
        settings: Resolved admin settings (Bedrock model id + region,
            optional admin system prompt).
        question_context: Output of :func:`posrat.ai.context.build_question_context`
            — glued onto the system prompt via a ``---`` divider.
        mcp_tools: Already-collected tool handles from MCP clients the
            caller has entered (see :func:`run_chat_turn`). ``None``
            means "no MCP tools".
        prior_messages: Previously-exchanged messages to seed the
            agent's conversation history. ``None`` means a fresh chat.
    """

    import boto3
    from strands import Agent
    from strands.models import BedrockModel

    session = boto3.Session(region_name=settings.region)
    model = BedrockModel(
        model_id=settings.model_id,
        boto_session=session,
    )

    agent = Agent(
        model=model,
        system_prompt=_compose_system_prompt(settings, question_context),
        tools=list(mcp_tools or []),
        messages=list(prior_messages or []),
    )
    return agent


async def run_chat_turn(
    settings: AISettings,
    user_prompt: str,
    *,
    question_context: str = "",
    mcp_clients: Optional[List["MCPClient"]] = None,
    prior_messages: Optional[list] = None,
) -> AsyncIterator[dict]:
    """Stream one chat turn, yielding Strands stream events.

    Opens each MCP client with :class:`contextlib.ExitStack` (Strands
    clients are context managers that need to be entered for the HTTP
    transport to start), collects their tool handles, builds the
    agent, and forwards every event from
    :meth:`Agent.stream_async`. Text deltas come in ``event["data"]``;
    callers yield those to the UI. The final event carries the
    updated :attr:`Agent.messages` list in ``event["complete_messages"]``
    so the caller can stash history for the next turn.

    Usage::

        async for event in run_chat_turn(settings, "Hi", ...):
            if "data" in event:
                ui_render_token(event["data"])
            if "complete_messages" in event:
                session["history"] = event["complete_messages"]

    Any exception raised by Strands / boto3 propagates — the caller
    wraps the loop in a try/except and surfaces the error via
    ``ui.notify(..., type='negative')``.
    """

    with contextlib.ExitStack() as stack:
        tools: list = []
        for client in mcp_clients or []:
            stack.enter_context(client)
            tools.extend(client.list_tools_sync())

        agent = build_agent(
            settings,
            question_context=question_context,
            mcp_tools=tools,
            prior_messages=prior_messages,
        )

        async for event in agent.stream_async(user_prompt):
            yield event

        # Emit the final message trace so the caller can persist it.
        yield {"complete_messages": list(agent.messages)}


__all__ = [
    "_compose_system_prompt",
    "build_agent",
    "run_chat_turn",
]
