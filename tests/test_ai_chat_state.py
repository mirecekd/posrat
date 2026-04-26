"""Tests for :mod:`posrat.ai.chat_state`.

Uses a plain ``dict`` in place of NiceGUI's ``app.storage.tab`` —
the DAO never touches NiceGUI internals, it just reads/writes keys
on whatever mapping it's handed.
"""

from __future__ import annotations

from posrat.ai.chat_state import (
    CHAT_STORAGE_KEY,
    clear_history,
    extract_user_and_assistant_text,
    load_history,
    save_history,
)


def test_load_empty_returns_empty_list():
    storage: dict = {}
    assert load_history(storage, "ctx") == []


def test_save_then_load_round_trips():
    storage: dict = {}
    save_history(storage, "ctx1", [{"role": "user", "content": [{"text": "hi"}]}])
    result = load_history(storage, "ctx1")
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_namespaced_under_single_key():
    storage: dict = {}
    save_history(storage, "ctx1", [{"role": "user", "content": []}])
    assert CHAT_STORAGE_KEY in storage
    assert "ctx1" in storage[CHAT_STORAGE_KEY]


def test_multiple_contexts_stay_independent():
    storage: dict = {}
    save_history(storage, "a", [{"role": "user", "content": [{"text": "A"}]}])
    save_history(storage, "b", [{"role": "user", "content": [{"text": "B"}]}])
    assert load_history(storage, "a")[0]["content"][0]["text"] == "A"
    assert load_history(storage, "b")[0]["content"][0]["text"] == "B"


def test_clear_single_context():
    storage: dict = {}
    save_history(storage, "a", [{"role": "user", "content": []}])
    save_history(storage, "b", [{"role": "user", "content": []}])
    clear_history(storage, "a")
    assert load_history(storage, "a") == []
    assert load_history(storage, "b") != []


def test_clear_all_contexts():
    storage: dict = {}
    save_history(storage, "a", [{"role": "user", "content": []}])
    save_history(storage, "b", [{"role": "user", "content": []}])
    clear_history(storage)
    assert load_history(storage, "a") == []
    assert load_history(storage, "b") == []


def test_save_shallow_copies_messages():
    """Save must not keep a reference to the caller's list."""

    storage: dict = {}
    messages = [{"role": "user", "content": [{"text": "first"}]}]
    save_history(storage, "ctx", messages)
    # Mutating the caller's list after save must not affect storage.
    messages.append({"role": "assistant", "content": [{"text": "leak"}]})
    loaded = load_history(storage, "ctx")
    assert len(loaded) == 1


def test_extract_flattens_text_blocks():
    messages = [
        {"role": "user", "content": [{"text": "Hello"}]},
        {"role": "assistant", "content": [{"text": "World"}, {"text": "!"}]},
    ]
    out = extract_user_and_assistant_text(messages)
    assert out == [("user", "Hello"), ("assistant", "World\n!")]


def test_extract_skips_non_text_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"name": "x"}},
                {"text": "Final answer"},
            ],
        }
    ]
    out = extract_user_and_assistant_text(messages)
    assert out == [("assistant", "Final answer")]


def test_extract_skips_system_and_tool_roles():
    messages = [
        {"role": "system", "content": [{"text": "system prompt"}]},
        {"role": "tool", "content": [{"text": "tool result"}]},
        {"role": "user", "content": [{"text": "visible"}]},
    ]
    out = extract_user_and_assistant_text(messages)
    assert out == [("user", "visible")]
