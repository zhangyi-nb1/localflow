"""Phase 32.2 — Workspace Protocol backed by an HTTP agent-server.

``AgentServerWorkspace`` implements the same Workspace surface as
``LocalWorkspace`` / ``DockerWorkspace`` / ``RemoteWorkspace``, but
delegates every operation to an ``AgentServerClient`` talking to a
running ``AgentServer``.

This is the third "remote" backend (after Docker + SSH); unlike them,
the per-op latency is the network RTT + JSON serialise, NOT a
sub-process spawn. The harness gets ~10x throughput on hot paths.

Lifecycle:

    server = AgentServer(workspace_root=Path("/wkspc"))
    server.start()
    client = AgentServerClient(base_url=server.base_url, token=server.token)
    ws = AgentServerWorkspace(client=client)
    try:
        ws.mkdir("sub/")
        ws.write_text("note.md", "hi")
    finally:
        server.stop()

In Phase 32 the server is in-process. Phase 33 will wire the server
into containers + remote machines (so the perf upgrade benefits
DockerWorkspace + RemoteWorkspace), but the Workspace interface
stays identical because of this layer.

§10.7 invariant: this is an application-layer Workspace. No imports
from ``app/harness/`` or ``app/schemas/`` — only the canonical
``WorkspaceStat`` from ``app/tools/workspace.py`` (the kernel-tier
Protocol surface).
"""

from __future__ import annotations

from pathlib import Path

from app.tools.agent_server.client import AgentServerClient
from app.tools.workspace import WorkspaceStat


class AgentServerWorkspace:
    """Workspace Protocol implementation that talks to an HTTP
    agent-server.

    The server is responsible for its own lifecycle (started by the
    caller, possibly inside a container or over an SSH tunnel). This
    class is a transport-agnostic client adapter — it never spawns
    processes, opens sockets directly, or knows about Docker / SSH.
    """

    def __init__(self, *, client: AgentServerClient) -> None:
        self._client = client
        # Cache the workspace root since it doesn't change for the
        # life of the server — saves one HTTP round-trip on the
        # frequently-called ``.root`` property.
        self._root_cache: Path | None = None

    # ── Workspace Protocol: properties ───────────────────────────────

    @property
    def root(self) -> Path:
        if self._root_cache is None:
            self._root_cache = self._client.workspace_root()
        return self._root_cache

    def is_local(self) -> bool:
        # Same answer as DockerWorkspace + RemoteWorkspace — the fs
        # the harness can see directly is NOT this workspace.
        return False

    # ── Workspace Protocol: reads ────────────────────────────────────

    def exists(self, rel_path: str) -> bool:
        return self._client.exists(rel_path)

    def stat(self, rel_path: str) -> WorkspaceStat | None:
        payload = self._client.stat(rel_path)
        if payload is None:
            return None
        return WorkspaceStat(
            rel_path=payload.rel_path,
            size_bytes=payload.size_bytes,
            is_file=payload.is_file,
            is_dir=payload.is_dir,
        )

    def sha256(self, rel_path: str) -> str | None:
        return self._client.sha256(rel_path)

    def list_dir(self, rel_path: str = "") -> list[str]:
        return self._client.list_dir(rel_path)

    def read_bytes(self, rel_path: str) -> bytes:
        return self._client.read_bytes(rel_path)

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(rel_path).decode(encoding)

    # ── Workspace Protocol: writes ───────────────────────────────────

    def mkdir(self, rel_path: str) -> bool:
        return self._client.mkdir(rel_path)

    def move(self, src_rel: str, dst_rel: str) -> Path:
        return self._client.move(src_rel, dst_rel)

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        return self._client.copy(src_rel, dst_rel)

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        # Same semantics as move at the server level.
        return self.move(src_rel, dst_rel)

    def write_text(self, rel_path: str, content: str) -> Path:
        return self.write_bytes(rel_path, content.encode("utf-8"))

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        return self._client.write_bytes(rel_path, content)

    def safe_target_rel(self, rel_path: str) -> str:
        return self._client.safe_target(rel_path)


__all__ = ["AgentServerWorkspace"]
