"""Parse admin-supplied MCP JSON and build Strands MCP clients.

The admin pastes a Claude Desktop-compatible blob:

.. code-block:: json

    {
      "mcpServers": {
        "aws-knowledge": {"url": "https://knowledge-mcp.global.api.aws"},
        "my-stdio": {"command": "uvx", "args": ["some-mcp"]}
      }
    }

Each server entry is dispatched to a transport:

* ``url`` → :func:`mcp.client.streamable_http.streamablehttp_client`
  (used by aws-knowledge-mcp, which is HTTPS-streamable).
* ``command`` → :func:`mcp.stdio_client` with
  :class:`mcp.StdioServerParameters` (used by local MCP servers).

The builder returns a list of un-entered :class:`strands.tools.mcp.MCPClient`
objects. The caller is responsible for entering each one via
``with client:`` (or :class:`contextlib.ExitStack` for several) to start
the transport session before asking the agent. Strands handles the
``list_tools_sync`` → agent-tool conversion internally.

Strands / mcp imports are done lazily inside the function bodies so a
``pip install posrat`` without the optional AI stack still imports
cleanly — the error only surfaces when the admin enables the chat.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from strands.tools.mcp import MCPClient


def parse_mcp_config(raw_json: Optional[str]) -> dict:
    """Parse the admin JSON blob into a ``{"mcpServers": {...}}`` dict.

    Returns an empty ``{}`` when ``raw_json`` is ``None`` or
    whitespace-only (MCP is optional). Raises :class:`ValueError` on
    malformed JSON or when the top-level structure doesn't match the
    Claude Desktop schema — callers surface this via ``ui.notify``.
    """

    if raw_json is None:
        return {}
    stripped = raw_json.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MCP JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("MCP config must be a JSON object")
    servers = parsed.get("mcpServers")
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise ValueError('"mcpServers" must be an object')
    return servers


def build_mcp_clients(
    servers: dict,
) -> List["MCPClient"]:
    """Build an un-entered :class:`MCPClient` for every server entry.

    ``servers`` is the inner ``mcpServers`` dict returned by
    :func:`parse_mcp_config`. Each value is inspected: ``url`` picks the
    HTTP-streamable transport, ``command`` picks stdio. Anything else
    raises :class:`ValueError` with the server name so the operator
    knows which entry is broken.

    The returned clients are not entered — the caller must do
    ``with client:`` (or an :class:`~contextlib.ExitStack`) to start
    the transports before invoking the agent. Strands clients are
    single-session: each ``with`` enters one live transport, which
    is exactly what we want per chat turn.
    """

    from mcp import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    clients: List["MCPClient"] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"MCP server {name!r} must be a JSON object"
            )
        if "url" in entry:
            url = entry["url"]
            if not isinstance(url, str) or not url:
                raise ValueError(
                    f"MCP server {name!r} has invalid 'url'"
                )
            # ``streamablehttp_client`` takes optional headers; we pass
            # any ``headers`` dict the admin included straight through
            # so private MCP endpoints with auth tokens work.
            headers = entry.get("headers")
            if headers is not None and not isinstance(headers, dict):
                raise ValueError(
                    f"MCP server {name!r}: 'headers' must be an object"
                )

            def _factory(
                u: str = url,
                h: Optional[dict] = headers,
            ):
                return streamablehttp_client(u, headers=h)

            clients.append(MCPClient(_factory))
        elif "command" in entry:
            command = entry["command"]
            if not isinstance(command, str) or not command:
                raise ValueError(
                    f"MCP server {name!r} has invalid 'command'"
                )
            args = entry.get("args") or []
            if not isinstance(args, list) or not all(
                isinstance(a, str) for a in args
            ):
                raise ValueError(
                    f"MCP server {name!r}: 'args' must be a list of strings"
                )
            env = entry.get("env")
            if env is not None and not isinstance(env, dict):
                raise ValueError(
                    f"MCP server {name!r}: 'env' must be an object"
                )

            def _factory(
                c: str = command,
                a: list = args,
                e: Optional[dict] = env,
            ):
                return stdio_client(
                    StdioServerParameters(command=c, args=a, env=e)
                )

            clients.append(MCPClient(_factory))
        else:
            raise ValueError(
                f"MCP server {name!r} must define either 'url' or 'command'"
            )
    return clients


__all__ = ["build_mcp_clients", "parse_mcp_config"]
