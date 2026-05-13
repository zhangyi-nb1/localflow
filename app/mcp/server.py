"""Phase 6.1 — MCP server bootstrap (stdio transport).

Run via :func:`run_mcp_server`. Imports the official ``mcp`` SDK
lazily inside the function so installing LocalFlow without the
``[mcp]`` optional dep still works for everyone else.

stdio transport contract: **stdout is reserved for JSON-RPC frames**.
Any incidental ``print()`` from imports would corrupt the protocol.
The harness uses only :class:`JsonlLogger` (writes to files), so the
control_loop / executor / verifier paths are safe by construction.
Rich console prints in CLI commands are NOT reached because
``cmd_mcp_serve`` is the lone entry point and does no console output
after server start.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Send any logging that happens during a tool call to stderr — never to
# stdout — so JSON-RPC framing on stdout stays uncorrupted.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger("localflow.mcp")


async def run_mcp_server() -> None:
    """Boot the MCP server on stdin/stdout.

    Blocks until the client closes the connection (or the process is
    terminated). Exceptions inside individual tool handlers are caught
    and returned as ``{"error": "..."}`` JSON payloads so a single bad
    call doesn't kill the server.
    """
    # Lazy import — keeps `python -m app.cli` import path light for
    # users who don't have the MCP SDK installed.
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    from app.mcp.tools import get_tool, visible_tools

    server: Server = Server("localflow")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        # Phase 7 / Issue 3: dangerous tools (memory_unforbid_path) are
        # hidden unless LOCALFLOW_MCP_ALLOW_DANGEROUS=1.
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in visible_tools()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        tool = get_tool(name)
        if tool is None:
            return [_error_response(f"unknown tool: {name!r}")]
        try:
            result = tool.handler(arguments or {})
        except Exception as exc:
            logger.exception("tool %s failed", name)
            return [_error_response(f"{type(exc).__name__}: {exc}")]
        try:
            payload = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.exception("tool %s returned non-JSON-safe result", name)
            return [_error_response(f"serialization failed: {exc}")]
        return [TextContent(type="text", text=payload)]

    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            server.create_initialization_options(),
        )


def _error_response(message: str):
    """Wrap an error in the same TextContent envelope tool results use,
    so MCP clients see a structured ``{"error": ...}`` body instead of
    a protocol-level exception."""
    from mcp.types import TextContent

    return TextContent(type="text", text=json.dumps({"error": message}))
