"""Phase 32.2 — agent-server end-to-end tests.

These tests start a real ``AgentServer`` on an ephemeral port, point
an ``AgentServerClient`` at it, and verify the contract end-to-end.
No Docker / no SSH — runs everywhere.

Three classes:

  * ``TestAgentServerLifecycle`` — start / stop / context manager
  * ``TestEndpoints`` — every endpoint via the client
  * ``TestAgentServerWorkspace`` — the Workspace Protocol adapter
    driving the full Executor on a real plan
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.harness.executor import Executor
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    ExecutionStatus,
    RiskLevel,
)
from app.storage.run_store import RunStore
from app.tools.agent_server import AgentServer, AgentServerClient
from app.tools.agent_server.protocol import AgentServerError
from app.tools.agent_server_workspace import AgentServerWorkspace


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "agent-ws"
    root.mkdir()
    return root


@pytest.fixture
def server(workspace_root: Path):
    """A running AgentServer on an ephemeral port. Cleaned up after the test."""
    srv = AgentServer(workspace_root=workspace_root)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def client(server: AgentServer) -> AgentServerClient:
    return AgentServerClient(base_url=server.base_url, token=server.token)


class TestAgentServerLifecycle:
    """Server can start, accept a healthcheck, and stop cleanly."""

    def test_port_available_after_start(self, workspace_root: Path):
        srv = AgentServer(workspace_root=workspace_root)
        try:
            srv.start()
            assert srv.port > 0
            assert srv.base_url == f"http://127.0.0.1:{srv.port}"
        finally:
            srv.stop()

    def test_port_unavailable_before_start(self, workspace_root: Path):
        srv = AgentServer(workspace_root=workspace_root)
        with pytest.raises(RuntimeError, match="not started"):
            _ = srv.port

    def test_start_stop_is_idempotent(self, workspace_root: Path):
        srv = AgentServer(workspace_root=workspace_root)
        srv.start()
        srv.start()  # second call no-ops; doesn't raise
        srv.stop()
        srv.stop()  # second call no-ops; doesn't raise

    def test_context_manager(self, workspace_root: Path):
        with AgentServer(workspace_root=workspace_root) as srv:
            assert srv.port > 0
        # After exit, accessing port raises again
        with pytest.raises(RuntimeError, match="not started"):
            _ = srv.port

    def test_workspace_root_created_on_init(self, tmp_path: Path):
        missing = tmp_path / "auto-created"
        assert not missing.exists()
        srv = AgentServer(workspace_root=missing)
        try:
            srv.start()
            assert missing.is_dir()
        finally:
            srv.stop()

    def test_default_token_is_64_hex(self, workspace_root: Path):
        srv = AgentServer(workspace_root=workspace_root)
        # 32 bytes = 64 hex chars
        assert len(srv.token) == 64
        assert all(c in "0123456789abcdef" for c in srv.token)


class TestEndpoints:
    """Drive every endpoint via the client; assert the on-disk effect
    inside the workspace_root."""

    def test_healthz_no_auth(self, server: AgentServer):
        # Healthz works without a token (no auth header).
        req = urllib.request.Request(f"{server.base_url}/healthz")
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read())
        assert body["status"] == "ok"
        assert "version" in body

    def test_auth_rejected_when_missing(self, server: AgentServer):
        req = urllib.request.Request(f"{server.base_url}/workspace_root")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 401

    def test_auth_rejected_when_wrong(self, server: AgentServer):
        req = urllib.request.Request(
            f"{server.base_url}/workspace_root",
            headers={"Authorization": "Bearer wrong-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 401

    def test_workspace_root(self, server: AgentServer, client: AgentServerClient):
        assert client.workspace_root() == server.workspace_root

    def test_healthz_via_client(self, client: AgentServerClient):
        resp = client.healthz()
        assert resp.status == "ok"

    def test_mkdir_creates_directory(self, client: AgentServerClient, workspace_root: Path):
        assert client.mkdir("subdir") is True
        assert (workspace_root / "subdir").is_dir()

    def test_mkdir_idempotent(self, client: AgentServerClient, workspace_root: Path):
        (workspace_root / "exists").mkdir()
        assert client.mkdir("exists") is False

    def test_write_then_read_text(self, client: AgentServerClient, workspace_root: Path):
        # write_bytes via client (text handled by Workspace adapter)
        client.write_bytes("note.md", "héllo\n".encode("utf-8"))
        on_disk = (workspace_root / "note.md").read_bytes()
        assert on_disk == "héllo\n".encode("utf-8")
        read_back = client.read_bytes("note.md")
        assert read_back.decode("utf-8") == "héllo\n"

    def test_write_binary_roundtrip(self, client: AgentServerClient):
        payload = b"\x00\x01\xff\xfe"
        client.write_bytes("data.bin", payload)
        assert client.read_bytes("data.bin") == payload

    def test_exists_true_and_false(self, client: AgentServerClient):
        assert client.exists("nope.md") is False
        client.write_bytes("nope.md", b"now exists")
        assert client.exists("nope.md") is True

    def test_stat_returns_payload(self, client: AgentServerClient):
        client.write_bytes("note.md", b"hi")
        stat = client.stat("note.md")
        assert stat is not None
        assert stat.size_bytes == 2
        assert stat.is_file is True
        assert stat.is_dir is False

    def test_stat_returns_none_for_missing(self, client: AgentServerClient):
        assert client.stat("missing") is None

    def test_sha256_for_file(self, client: AgentServerClient):
        client.write_bytes("hello.txt", b"hello\n")
        digest = client.sha256("hello.txt")
        # echo -n "hello\n" | shasum -a 256 → 5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03
        assert digest == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"

    def test_sha256_none_for_directory(self, client: AgentServerClient):
        client.mkdir("subdir")
        assert client.sha256("subdir") is None

    def test_list_dir_sorted(self, client: AgentServerClient, workspace_root: Path):
        (workspace_root / "b.md").write_text("b")
        (workspace_root / "a.md").write_text("a")
        (workspace_root / "c.md").write_text("c")
        assert client.list_dir("") == ["a.md", "b.md", "c.md"]

    def test_move_creates_parent(self, client: AgentServerClient, workspace_root: Path):
        client.write_bytes("source.md", b"hi")
        path = client.move("source.md", "subdir/dest.md")
        assert (workspace_root / "subdir" / "dest.md").is_file()
        # Returned path is absolute (server-side)
        assert str(path).endswith("subdir/dest.md")

    def test_copy_file_preserves_source(self, client: AgentServerClient, workspace_root: Path):
        client.write_bytes("source.md", b"hi")
        client.copy("source.md", "copy.md")
        assert (workspace_root / "source.md").is_file()
        assert (workspace_root / "copy.md").is_file()

    def test_safe_target_returns_input_when_free(self, client: AgentServerClient):
        assert client.safe_target("free.md") == "free.md"

    def test_safe_target_suffixes_on_collision(
        self, client: AgentServerClient, workspace_root: Path
    ):
        (workspace_root / "f.md").write_text("first")
        assert client.safe_target("f.md") == "f (1).md"
        (workspace_root / "f (1).md").write_text("second")
        assert client.safe_target("f.md") == "f (2).md"


class TestErrorMapping:
    """Server returns proper status codes; client wraps them in
    AgentServerError with diagnostic info."""

    def test_absolute_path_returns_400(self, client: AgentServerClient):
        with pytest.raises(AgentServerError) as exc:
            client.exists("/etc/passwd")
        assert exc.value.status == 400
        assert "absolute" in str(exc.value).lower()

    def test_parent_traversal_returns_400(self, client: AgentServerClient):
        with pytest.raises(AgentServerError) as exc:
            client.write_bytes("../escape", b"x")
        assert exc.value.status == 400

    def test_read_missing_returns_500(self, client: AgentServerClient):
        # OS error → 500 with the OS message in detail
        with pytest.raises(AgentServerError) as exc:
            client.read_bytes("doesnt-exist.md")
        # Some platforms raise FileNotFoundError (→ 404), others
        # raise generic OSError (→ 500). Accept either.
        assert exc.value.status in (404, 500)


class TestAgentServerWorkspace:
    """End-to-end Workspace Protocol drive — same shape as the
    LocalWorkspace + DockerWorkspace integration tests."""

    def test_protocol_methods_through_workspace(self, server: AgentServer, workspace_root: Path):
        client = AgentServerClient(base_url=server.base_url, token=server.token)
        ws = AgentServerWorkspace(client=client)

        # root + is_local
        assert ws.root == server.workspace_root
        assert ws.is_local() is False

        # write_text + read_text
        ws.write_text("note.md", "hello agent\n")
        assert ws.read_text("note.md") == "hello agent\n"

        # mkdir + exists + list_dir
        assert ws.mkdir("sub") is True
        assert ws.exists("sub") is True
        assert ws.list_dir("") == ["note.md", "sub"]

        # stat
        st = ws.stat("note.md")
        assert st is not None
        assert st.is_file is True
        assert st.size_bytes == len("hello agent\n")

        # sha256
        digest = ws.sha256("note.md")
        assert digest is not None
        assert len(digest) == 64

        # move + copy
        ws.write_text("a.md", "a")
        ws.move("a.md", "sub/a.md")
        assert (workspace_root / "sub" / "a.md").read_text() == "a"
        ws.copy("sub/a.md", "sub/a-copy.md")
        assert (workspace_root / "sub" / "a-copy.md").read_text() == "a"

        # safe_target_rel
        assert ws.safe_target_rel("note.md") == "note (1).md"

    def test_executor_runs_full_plan_through_agent_server(
        self,
        server: AgentServer,
        workspace_root: Path,
        tmp_path: Path,
    ):
        """The crowning integration test: a real Executor + a real
        ActionPlan, driven through the agent-server. If this passes,
        the abstraction is genuinely drop-in for the LocalWorkspace
        / DockerWorkspace / RemoteWorkspace siblings."""
        client = AgentServerClient(base_url=server.base_url, token=server.token)
        ws = AgentServerWorkspace(client=client)
        run_store = RunStore.create(home=tmp_path / ".localflow")

        plan = ActionPlan(
            plan_id="phase-32-e2e",
            task_id=run_store.task_id,
            summary="agent-server end-to-end",
            actions=[
                Action(
                    action_id="a-1",
                    action_type=ActionType.MKDIR,
                    target_path="reports",
                    reason="set up reports dir",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=False,
                ),
                Action(
                    action_id="a-2",
                    action_type=ActionType.INDEX,
                    target_path="reports/summary.md",
                    reason="write summary",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=False,
                    metadata={"content": "# Summary\nrun-through-agent-server\n"},
                ),
            ],
        )
        ex = Executor(
            workspace_root=workspace_root,
            run_store=run_store,
            workspace=ws,
        )
        outcome = ex.execute(plan, approved=True)
        assert outcome.success, [r.error for r in outcome.records]
        assert (workspace_root / "reports" / "summary.md").is_file()
        assert (
            workspace_root / "reports" / "summary.md"
        ).read_text() == "# Summary\nrun-through-agent-server\n"
        # Manifest entries match what LocalWorkspace would produce.
        assert any(e.op.value == "delete_created_dir" for e in outcome.manifest.entries)
        assert any(e.op.value == "delete_created_file" for e in outcome.manifest.entries)
        # All records succeeded
        for record in outcome.records:
            assert record.status == ExecutionStatus.SUCCESS
