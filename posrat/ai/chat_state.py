"""Per-tab chat conversation state.

NiceGUI's ``app.storage.tab`` is a server-side dict scoped to the
current browser tab — persists across refreshes but not across tabs
or browsers. That's exactly what we want for an ephemeral chat: the
user can iterate on the same question without losing context, but a
fresh tab starts clean and no cross-user bleed is possible.

Stored under :data:`CHAT_STORAGE_KEY` as a dict keyed by *context id*
(Designer/Runner supply their own key so switching question resets
the chat for that specific question but preserves others). Each value
is a list of Strands-style message dicts (``{"role": "user"/"assistant",
"content": [...]}``) — stored raw so :func:`posrat.ai.agent.run_chat_turn`
can feed them straight back into :class:`Agent.messages`.
"""

from __future__ import annotations

from typing import Optional


#: Top-level key under ``app.storage.tab``. Keeping it namespaced
#: prevents collisions with unrelated features (Runner session state,
#: etc.) that also grab the same dict.
CHAT_STORAGE_KEY = "ai_chat_history"


def _root(storage: dict) -> dict:
    """Return (and lazily create) the AI chat root sub-dict."""

    root = storage.get(CHAT_STORAGE_KEY)
    if not isinstance(root, dict):
        root = {}
        storage[CHAT_STORAGE_KEY] = root
    return root


def load_history(storage: dict, context_id: str) -> list[dict]:
    """Return the raw message list for ``context_id`` (or empty list)."""

    root = _root(storage)
    raw = root.get(context_id)
    if not isinstance(raw, list):
        return []
    # Shallow-copy so the caller can mutate without affecting storage
    # until they call :func:`save_history`.
    return list(raw)


def save_history(
    storage: dict, context_id: str, messages: list[dict]
) -> None:
    """Persist ``messages`` back for later turns.

    Overwrites the previous value; the UI always hands in the full
    message trace returned by :func:`posrat.ai.agent.run_chat_turn`.
    """

    root = _root(storage)
    root[context_id] = list(messages)
    storage[CHAT_STORAGE_KEY] = root


def clear_history(
    storage: dict, context_id: Optional[str] = None
) -> None:
    """Drop the history for ``context_id`` (or all when ``None``)."""

    root = _root(storage)
    if context_id is None:
        storage[CHAT_STORAGE_KEY] = {}
        return
    root.pop(context_id, None)
    storage[CHAT_STORAGE_KEY] = root


def extract_user_and_assistant_text(messages: list[dict]) -> list[tuple[str, str]]:
    """Convert Strands message dicts into ``(role, text)`` display tuples.

    Strands stores message ``content`` as a list of blocks (``[{"text":
    "..."}]`` / tool use / tool result). The chat bubble UI only needs
    the plain user / assistant texts, so we flatten to a simple list
    and drop tool-use / tool-result blocks — the user cares about the
    conversation flow, not the plumbing.
    """

    out: list[tuple[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                maybe_text = block.get("text")
                if isinstance(maybe_text, str) and maybe_text:
                    text_parts.append(maybe_text)
            elif isinstance(block, str):
                text_parts.append(block)
        if text_parts:
            out.append((str(role), "\n".join(text_parts)))
    return out


__all__ = [
    "CHAT_STORAGE_KEY",
    "clear_history",
    "extract_user_and_assistant_text",
    "load_history",
    "save_history",
]
