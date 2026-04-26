"""Tests for :mod:`posrat.system.admin_ai_view` — MCP JSON validation."""

from __future__ import annotations

from posrat.system.admin_ai_view import _validate_mcp_json


def test_empty_is_valid():
    assert _validate_mcp_json("") is None
    assert _validate_mcp_json("   \n\t ") is None


def test_valid_mcp_servers_object():
    assert (
        _validate_mcp_json(
            '{"mcpServers": {"aws-knowledge": {"url": "https://x"}}}'
        )
        is None
    )


def test_valid_empty_mcp_servers():
    assert _validate_mcp_json('{"mcpServers": {}}') is None


def test_invalid_json_syntax_error():
    error = _validate_mcp_json("{not json}")
    assert error is not None
    assert "Invalid JSON" in error


def test_top_level_not_object():
    assert _validate_mcp_json("[]") == "Top-level value must be a JSON object."
    assert (
        _validate_mcp_json('"just a string"')
        == "Top-level value must be a JSON object."
    )


def test_missing_mcp_servers_key():
    assert _validate_mcp_json('{"foo": 1}') == 'Missing "mcpServers" key.'


def test_mcp_servers_not_object():
    assert (
        _validate_mcp_json('{"mcpServers": []}')
        == '"mcpServers" must be a JSON object.'
    )
