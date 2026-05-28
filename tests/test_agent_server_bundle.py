"""Phase 33.1 — agent-server bundle tests.

These exercise the standalone bundle builder + verify it can be
exec'd via ``python3 -c "<bundle>"`` (the dispatch mechanism Phase
33.x DockerWorkspace + RemoteWorkspace will use).

Tests run on any machine with python3 in PATH — no Docker / no SSH.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.tools.agent_server.bundle import build_bundle, bundle_sha256


class TestBundleAssembly:
    """Static checks — the bundle builds a syntactically-valid module
    that contains every symbol the dispatch dispatch table references."""

    def test_bundle_is_valid_python_ast(self):
        src = build_bundle()
        # ast.parse raises SyntaxError on any failure; if we get back
        # a Module the bundle is well-formed.
        tree = ast.parse(src)
        assert isinstance(tree, ast.Module)

    def test_bundle_contains_agentserver_class(self):
        src = build_bundle()
        assert "class AgentServer:" in src

    def test_bundle_contains_workspacestat(self):
        src = build_bundle()
        # WorkspaceStat is inlined as a dataclass in the bundle header.
        assert "class WorkspaceStat:" in src

    def test_bundle_contains_sha256_file(self):
        src = build_bundle()
        assert "def sha256_file(" in src

    def test_bundle_contains_main_entrypoint(self):
        src = build_bundle()
        assert "def _main() -> None:" in src
        assert "if __name__ ==" in src

    def test_bundle_strips_app_imports(self):
        """No ``from app.*`` imports should survive — the bundle is
        meant to run outside the LocalFlow package layout."""
        src = build_bundle()
        for line in src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("from app."):
                pytest.fail(f"leaked app.* import in bundle: {stripped!r}")

    def test_bundle_sha_is_stable_across_calls(self):
        # build_bundle is @lru_cache'd; calling twice gives the same
        # bytes hence the same hash.
        h1 = bundle_sha256()
        h2 = bundle_sha256()
        assert h1 == h2
        assert len(h1) == 64

    def test_bundle_size_reasonable(self):
        """Sanity: bundle should be 10-100 KB. Catches accidental
        explosion (e.g. accidentally embedding the whole project)."""
        src = build_bundle()
        size = len(src.encode("utf-8"))
        assert 10_000 < size < 100_000, f"bundle is {size} bytes — out of expected range"


def _python_exec(bundle: str, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    """Spawn ``python -c "<bundle>"`` with the given env. Returns the
    Popen for the test to drive."""
    return subprocess.Popen(
        [sys.executable, "-c", bundle],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **env},
        text=True,
        cwd=cwd,
    )


def _read_handshake(proc: subprocess.Popen, *, timeout: float = 10.0) -> dict[str, str]:
    """Parse the three ``AGENT_SERVER_*`` lines the bundle writes to
    stdout on startup."""
    info: dict[str, str] = {}
    deadline = time.time() + timeout
    for _ in range(3):
        while True:
            if time.time() > deadline:
                raise TimeoutError("handshake timed out")
            line = proc.stdout.readline()
            if line:
                break
            # No data yet; check the process didn't die
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(f"bundle process exited early. stderr: {stderr}")
            time.sleep(0.05)
        k, _, v = line.strip().partition("=")
        info[k] = v
    return info


class TestBundleRuntime:
    """Exec the bundle as ``python -c "<bundle>"`` + drive via HTTP."""

    @pytest.fixture
    def bundle_proc(self, tmp_path: Path):
        bundle = build_bundle()
        env = {
            "AGENT_SERVER_WORKSPACE": str(tmp_path),
            "AGENT_SERVER_PORT": "0",
        }
        proc = _python_exec(bundle, env=env, cwd=tmp_path)
        try:
            info = _read_handshake(proc)
            yield proc, info, tmp_path
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_bundle_starts_and_announces_port_and_token(self, bundle_proc):
        proc, info, _ = bundle_proc
        assert "AGENT_SERVER_PORT" in info
        assert int(info["AGENT_SERVER_PORT"]) > 0
        assert "AGENT_SERVER_TOKEN" in info
        assert len(info["AGENT_SERVER_TOKEN"]) == 64
        assert "AGENT_SERVER_WORKSPACE" in info

    def test_bundle_serves_healthz_without_auth(self, bundle_proc):
        _, info, _ = bundle_proc
        port = info["AGENT_SERVER_PORT"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
            body = json.loads(resp.read())
        assert body["status"] == "ok"
        assert "version" in body

    def test_bundle_serves_mkdir_with_auth(self, bundle_proc):
        _, info, workspace = bundle_proc
        port = info["AGENT_SERVER_PORT"]
        token = info["AGENT_SERVER_TOKEN"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mkdir",
            data=json.dumps({"path": "sub"}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read())
        assert body["created"] is True
        assert (workspace / "sub").is_dir()

    def test_bundle_rejects_missing_token(self, bundle_proc):
        _, info, _ = bundle_proc
        port = info["AGENT_SERVER_PORT"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/workspace_root",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 401

    def test_bundle_pinned_token_honoured(self, tmp_path: Path):
        """``AGENT_SERVER_TOKEN`` env var pins the token (for cases where
        the supervising process wants a deterministic value)."""
        bundle = build_bundle()
        fixed_token = "a" * 64
        env = {
            "AGENT_SERVER_WORKSPACE": str(tmp_path),
            "AGENT_SERVER_PORT": "0",
            "AGENT_SERVER_TOKEN": fixed_token,
        }
        proc = _python_exec(bundle, env=env, cwd=tmp_path)
        try:
            info = _read_handshake(proc)
            assert info["AGENT_SERVER_TOKEN"] == fixed_token
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
