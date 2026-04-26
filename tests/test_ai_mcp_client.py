"""Tests for :mod:`posrat.ai.mcp_client`.parse_mcp_config."""

from __future__ import annotations

import pytest

from posrat.ai.mcp_client import parse_mcp_config


def test_none_returns_empty():
    assert parse_mcp_config(None) == {}


def test_blank_returns_empty():
    assert parse_mcp_config("") == {}
    assert parse_mcp_config("   \n  ") == {}


def test_valid_config_returns_inner_dict():
    raw = '{"mcpServers": {"a": {"url": "https://x"}}}'
    assert parse_mcp_config(raw) == {"a": {"url": "https://x"}}


def test_missing_mcp_servers_returns_empty():
    assert parse_mcp_config('{"other": 1}') == {}


def test_invalid_json_raises():
    with pytest.raises(ValueError, match="Invalid MCP JSON"):
        parse_mcp_config("{not json}")


def test_top_level_not_object_raises():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_mcp_config("[]")


def test_mcp_servers_not_object_raises():
    with pytest.raises(ValueError, match='"mcpServers" must be an object'):
        parse_mcp_config('{"mcpServers": 42}')
