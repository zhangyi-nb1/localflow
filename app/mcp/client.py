"""v0.16 — MCP client wrapper.

Connects LocalFlow (the MCP **server** in v0.10+) to external MCP
servers (filesystem, fetch, search, etc.) so their tools can be
inventoried + (in a future phase) wired into the Phase 4.2 Tool
Registry as callable helpers for skills.

This is a thin async wrapper around the official ``mcp`` SDK's
``ClientSession``. v0.16 ships the **probe / catalog** surface — the
user registers external servers via CLI, LocalFlow can spawn each
one and list its advertised tools. Skills can't *call* those tools
yet from inside their planners; that wiring is a future phase
because it needs careful approval-token integration.

Scope honestly documented in docs/MCP.md.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any


@dataclass
class ExternalTool:
    """One tool advertised by an external MCP server."""

    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ProbeOutcome:
    """Result of probing one external server."""

    server_name: str
    command: str
    success: bool
    tools: list[ExternalTool]
    error: str | None = None


async def _probe_async(server_name: str, command: str) -> ProbeOutcome:
    """Spawn an external MCP server via stdio + call list_tools."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        return ProbeOutcome(
            server_name=server_name,
            command=command,
            success=False,
            tools=[],
            error=(
                f"mcp SDK not installed: {exc}. Install with "
                "'pip install \"localflow-agent[mcp]\"'."
            ),
        )

    cmd_parts = shlex.split(command)
    if not cmd_parts:
        return ProbeOutcome(
            server_name=server_name,
            command=command,
            success=False,
            tools=[],
            error="empty command",
        )

    params = StdioServerParameters(command=cmd_parts[0], args=cmd_parts[1:])
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.list_tools()
                tools = [
                    ExternalTool(
                        server_name=server_name,
                        name=getattr(t, "name", "?"),
                        description=getattr(t, "description", "") or "",
                        input_schema=getattr(t, "inputSchema", None) or {},
                    )
                    for t in (resp.tools or [])
                ]
                return ProbeOutcome(
                    server_name=server_name,
                    command=command,
                    success=True,
                    tools=tools,
                )
    except Exception as exc:
        return ProbeOutcome(
            server_name=server_name,
            command=command,
            success=False,
            tools=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def probe(server_name: str, command: str, *, timeout: float = 20.0) -> ProbeOutcome:
    """Synchronous wrapper around :func:`_probe_async`. Honours a
    per-probe wall-clock timeout so a misbehaving external server
    can't hang the calling process indefinitely."""
    try:
        return asyncio.run(asyncio.wait_for(_probe_async(server_name, command), timeout=timeout))
    except asyncio.TimeoutError:
        return ProbeOutcome(
            server_name=server_name,
            command=command,
            success=False,
            tools=[],
            error=f"probe timed out after {timeout}s",
        )
    except Exception as exc:  # pragma: no cover — defensive
        return ProbeOutcome(
            server_name=server_name,
            command=command,
            success=False,
            tools=[],
            error=f"{type(exc).__name__}: {exc}",
        )
