"""Phase 32 — HTTP agent-server for long-lived Workspace remoting.

This package implements the building blocks for a long-lived agent
process that speaks HTTP and exposes the Workspace Protocol. The
harness opens one connection per run instead of shelling out per-op,
amortising the ~100-300 ms per-op latency that DockerWorkspace +
RemoteWorkspace currently pay.

Components:

  * ``protocol`` — Pydantic request/response models + path defence
  * ``server``   — stdlib http.server-based implementation
  * ``client``   — urllib-based client returning Pydantic models

Phase 32 ships the protocol + server + client. Wiring into Docker
and Remote backends (so they get the perf upgrade) is Phase 33.

The corresponding ``Workspace`` Protocol implementation that talks to
a running agent-server lives at ``app.tools.agent_server_workspace``
(separate module to keep this package as the on-the-wire surface).

§10.7 invariant: this is an application-layer tool. The Pydantic
models are local to this package; only ``WorkspaceStat`` is re-used
from ``app.tools.workspace`` to keep the on-the-wire shape identical
to the existing Workspace Protocol surface.
"""

from __future__ import annotations

from app.tools.agent_server.client import AgentServerClient
from app.tools.agent_server.protocol import (
    AGENT_SERVER_VERSION,
    AgentServerError,
    HealthResponse,
    ListDirResponse,
    MkdirRequest,
    MkdirResponse,
    MoveRequest,
    PathRequest,
    PathResponse,
    ReadBytesResponse,
    Sha256Response,
    StatResponse,
    WriteBytesRequest,
)
from app.tools.agent_server.server import AgentServer

__all__ = [
    "AGENT_SERVER_VERSION",
    "AgentServer",
    "AgentServerClient",
    "AgentServerError",
    "HealthResponse",
    "ListDirResponse",
    "MkdirRequest",
    "MkdirResponse",
    "MoveRequest",
    "PathRequest",
    "PathResponse",
    "ReadBytesResponse",
    "Sha256Response",
    "StatResponse",
    "WriteBytesRequest",
]
