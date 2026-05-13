"""Phase 6.1 — LocalFlow as an MCP server.

Exposes existing LocalFlow capabilities (inspect / plan / dry-run /
execute / verify / rollback / memory / catalog) as MCP tools over
stdio JSON-RPC, so Claude Code or any MCP client can drive LocalFlow
without re-implementing its safety machinery.

Public API:
  * ``TOOLS`` — the ``ToolDef`` table (one entry per MCP tool)
  * ``get_tool(name)`` — lookup a single ToolDef by name
  * ``run_mcp_server()`` — async entry point that boots stdio transport
  * ``to_jsonable(obj)`` — serialization helper used by handlers
"""

from app.mcp._serialize import to_jsonable
from app.mcp.tools import TOOLS, ToolDef, get_tool

__all__ = [
    "TOOLS",
    "ToolDef",
    "get_tool",
    "run_mcp_server",
    "to_jsonable",
]


def run_mcp_server():  # pragma: no cover — lazy proxy
    """Lazy proxy to :func:`app.mcp.server.run_mcp_server` so importing
    this package doesn't import the optional ``mcp`` SDK eagerly."""
    from app.mcp.server import run_mcp_server as _run

    return _run()
