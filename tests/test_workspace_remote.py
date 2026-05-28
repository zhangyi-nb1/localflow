"""Phase 31.1 — RemoteWorkspace contract tests.

Two layers:

Layer 1 — path-defence + ctor + lifecycle without any real SSH.
    Mocks ``subprocess.run`` and asserts the exact ssh command shape
    we generate. Catches protocol regressions without needing network
    access or a real remote.

Layer 2 — ssh-actual contract tests against ``localhost``. These skip
    cleanly when:
      * ``ssh -V`` is missing (no openssh client installed), OR
      * ``ssh -o BatchMode=yes localhost true`` fails (no key-based
        auth to localhost configured).

CI mostly exercises layer 1; layer 2 is opportunistic.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from app.tools.remote_workspace import (
    DEFAULT_REMOTE_ROOT,
    DEFAULT_SSH_PORT,
    RemoteUnavailable,
    RemoteWorkspace,
    RemoteWorkspaceError,
    _ssh_available,
    _validate_rel_path,
)
from app.tools.workspace import LocalWorkspace, parse_workspace_spec

# ---------------------------------------------------------------- layer 1


class TestValidateRelPath:
    """Host-side defence — exactly the same shape as
    docker_workspace's _validate_rel_path tests; ensures the
    invariant holds across both backends."""

    def test_simple_rel_path_accepted(self):
        assert _validate_rel_path("foo.txt") == "foo.txt"

    def test_nested_rel_path_accepted(self):
        assert _validate_rel_path("sub/dir/file.md") == "sub/dir/file.md"

    def test_empty_path_returns_empty(self):
        assert _validate_rel_path("") == ""

    def test_none_path_returns_empty(self):
        # Defensive: None coerces to empty same as ""
        assert _validate_rel_path(None) == ""  # type: ignore[arg-type]

    def test_absolute_path_rejected(self):
        with pytest.raises(RemoteWorkspaceError, match="absolute or home"):
            _validate_rel_path("/etc/passwd")

    def test_home_shorthand_rejected(self):
        with pytest.raises(RemoteWorkspaceError, match="absolute or home"):
            _validate_rel_path("~/secrets")

    def test_drive_letter_rejected(self):
        with pytest.raises(RemoteWorkspaceError, match="drive-letter"):
            _validate_rel_path("C:/Users/bob")

    def test_parent_traversal_rejected(self):
        with pytest.raises(RemoteWorkspaceError, match="parent-directory"):
            _validate_rel_path("sub/../escape")

    def test_backslash_normalised(self):
        # Windows callers might pass backslash paths; normalise but
        # still apply the parent-traversal check after split.
        assert _validate_rel_path("sub\\file.md") == "sub/file.md"


class TestConstruction:
    """RemoteWorkspace ctor signature + dataclass defaults."""

    def test_minimal_ctor(self):
        ws = RemoteWorkspace(host="user@example.com")
        assert ws.host == "user@example.com"
        assert ws.port == DEFAULT_SSH_PORT
        assert ws.workspace_root_remote == DEFAULT_REMOTE_ROOT
        assert ws._started is False

    def test_full_ctor(self):
        ws = RemoteWorkspace(
            host="bob@build.example.com",
            port=2222,
            workspace_root_remote="/srv/wkspc",
        )
        assert ws.port == 2222
        assert ws.workspace_root_remote == "/srv/wkspc"

    def test_is_local_false(self):
        ws = RemoteWorkspace(host="x@y")
        assert ws.is_local() is False

    def test_root_is_remote_path(self):
        ws = RemoteWorkspace(host="x@y", workspace_root_remote="/data/ws")
        assert ws.root == Path("/data/ws")

    def test_require_started_raises_before_start(self):
        ws = RemoteWorkspace(host="x@y")
        with pytest.raises(RemoteWorkspaceError, match="not started"):
            ws._require_started()


def _make_completed(
    args: list[str], *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def fake_subprocess():
    """Replaces subprocess.run with a recorder that yields canned
    responses. Tests inspect ``calls`` to verify the ssh argv shape."""
    calls: list[dict[str, Any]] = []
    # Default response queue — overridable per-test.
    responses: list[subprocess.CompletedProcess[bytes]] = []

    def _run(argv, **kwargs):
        calls.append({"argv": list(argv), "kwargs": kwargs})
        if responses:
            return responses.pop(0)
        # Default: empty success.
        return _make_completed(argv, returncode=0, stdout=b"", stderr=b"")

    with mock.patch("app.tools.remote_workspace.subprocess.run", side_effect=_run):
        yield calls, responses


class TestSshCommandShape:
    """Layer-1 protocol tests — every method's ssh argv is asserted
    by intercepting subprocess.run. This is the core of the test
    suite and runs on every CI matrix leg with zero network/SSH."""

    def _started_ws(self, fake, *, host="bob@host", port=22, root="/tmp/ws"):
        """Build + start a workspace through the fake subprocess."""
        ws = RemoteWorkspace(host=host, port=port, workspace_root_remote=root)
        # start() probes ssh availability first. Mock _ssh_available to True.
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            ws.start()
        # Drain start's mkdir -p call from the calls log so per-test
        # assertions only see their own ops.
        calls, _ = fake
        calls.clear()
        return ws

    def test_start_creates_remote_root(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = RemoteWorkspace(host="bob@x", workspace_root_remote="/data/wkspc")
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            ws.start()
        assert ws._started is True
        # First call should be ssh ... -- mkdir -p '/data/wkspc'.
        argv = calls[0]["argv"]
        assert argv[0] == "ssh"
        assert "-o" in argv and "BatchMode=yes" in argv
        # argv tail is ``<host>, --, mkdir, -p, <quoted-path>``
        assert argv[-5] == "bob@x"
        assert argv[-4:-2] == ["--", "mkdir"]
        assert argv[-2] == "-p"
        assert "/data/wkspc" in argv[-1]

    def test_start_raises_remote_unavailable_when_ssh_missing(self, fake_subprocess):
        ws = RemoteWorkspace(host="bob@x")
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=False):
            with pytest.raises(RemoteUnavailable, match="ssh CLI not reachable"):
                ws.start()
        assert ws._started is False

    def test_start_propagates_remote_failure_as_unavailable(self, fake_subprocess):
        calls, responses = fake_subprocess
        # Pre-load a non-zero response for the mkdir call so start() fails.
        responses.append(
            _make_completed(
                ["ssh"], returncode=255, stdout=b"", stderr=b"Permission denied (publickey)."
            )
        )
        ws = RemoteWorkspace(host="bob@x")
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            with pytest.raises(RemoteUnavailable, match="ssh probe to 'bob@x' failed"):
                ws.start()
        assert ws._started is False

    def test_start_is_idempotent(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = RemoteWorkspace(host="bob@x")
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            ws.start()
            ws.start()  # second call should be a no-op
        # Only one ssh invocation occurred.
        assert len(calls) == 1

    def test_custom_port_adds_p_flag(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = RemoteWorkspace(host="bob@x", port=2222)
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            ws.start()
        argv = calls[0]["argv"]
        assert "-p" in argv and "2222" in argv

    def test_close_releases_started_flag(self, fake_subprocess):
        ws = self._started_ws(fake_subprocess)
        ws.close()
        assert ws._started is False

    def test_context_manager_lifecycle(self, fake_subprocess):
        with mock.patch("app.tools.remote_workspace._ssh_available", return_value=True):
            with RemoteWorkspace(host="bob@x") as ws:
                assert ws._started is True
            assert ws._started is False

    # ── method-level shape tests

    def test_exists_uses_test_minus_e(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        ws.exists("foo.txt")
        argv = calls[0]["argv"]
        assert "test" in argv and "-e" in argv
        # Path must be quoted
        assert any("/tmp/ws/foo.txt" in a for a in argv)

    def test_exists_returns_false_on_invalid_path(self, fake_subprocess):
        ws = self._started_ws(fake_subprocess)
        # Absolute path is rejected client-side → False without an ssh call.
        calls, _ = fake_subprocess
        assert ws.exists("/etc/passwd") is False
        assert calls == []  # zero ssh calls fired

    def test_mkdir_idempotent_returns_false_on_existing(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        # First exists check returns 0 (exists) → mkdir returns False.
        responses.append(_make_completed(["ssh"], returncode=0))
        assert ws.mkdir("already") is False

    def test_mkdir_creates_when_missing(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        # First call (exists) returns non-zero; second (mkdir -p) succeeds.
        responses.append(_make_completed(["ssh"], returncode=1))
        responses.append(_make_completed(["ssh"], returncode=0))
        assert ws.mkdir("new") is True
        # Second invocation is the mkdir -p.
        argv = calls[1]["argv"]
        assert "mkdir" in argv and "-p" in argv

    def test_write_bytes_pipes_via_stdin(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        ws.write_bytes("note.bin", b"\x00\x01\x02")
        # The write call should pass stdin_bytes (input=) to subprocess.
        write_call = calls[-1]
        assert write_call["kwargs"].get("input") == b"\x00\x01\x02"
        # Argv must include sh -c 'cat > <quoted path>'.
        argv = write_call["argv"]
        assert "sh" in argv and "-c" in argv
        cat_cmd = argv[-1]
        assert cat_cmd.startswith("'cat >") and cat_cmd.endswith("'")

    def test_write_text_encodes_utf8(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        ws.write_text("note.md", "héllo")
        assert calls[-1]["kwargs"]["input"] == "héllo".encode("utf-8")

    def test_read_text_decodes_utf8(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        responses.append(_make_completed(["ssh"], returncode=0, stdout="héllo".encode("utf-8")))
        assert ws.read_text("note.md") == "héllo"
        argv = calls[-1]["argv"]
        assert "cat" in argv

    def test_stat_parses_size_and_kind(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        responses.append(_make_completed(["ssh"], returncode=0, stdout=b"1234 regular file\n"))
        st = ws.stat("note.md")
        assert st is not None
        assert st.size_bytes == 1234
        assert st.is_file is True
        assert st.is_dir is False

    def test_stat_returns_none_on_missing(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        responses.append(_make_completed(["ssh"], returncode=1, stdout=b""))
        assert ws.stat("missing.md") is None

    def test_sha256_skips_for_dir(self, fake_subprocess):
        calls, responses = fake_subprocess
        # stat() probe returns directory → sha256 returns None without
        # firing the sha256sum call.
        responses.append(_make_completed(["ssh"], returncode=0, stdout=b"4096 directory\n"))
        ws = self._started_ws(fake_subprocess)
        assert ws.sha256("subdir") is None

    def test_list_dir_returns_sorted_lines(self, fake_subprocess):
        calls, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        responses.append(_make_completed(["ssh"], returncode=0, stdout=b"b.txt\na.txt\nc.txt\n"))
        assert ws.list_dir("sub") == ["a.txt", "b.txt", "c.txt"]

    def test_move_includes_parent_mkdir(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        ws.move("a.txt", "newdir/b.txt")
        # Two calls: mkdir -p for parent, then mv.
        assert len(calls) == 2
        assert "mkdir" in calls[0]["argv"]
        assert "mv" in calls[1]["argv"]

    def test_copy_uses_minus_R(self, fake_subprocess):
        calls, _ = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        ws.copy("src/", "dst/")
        # Second call is cp -R.
        cp_argv = calls[-1]["argv"]
        assert "cp" in cp_argv and "-R" in cp_argv

    def test_safe_target_rel_finds_free_name(self, fake_subprocess):
        _, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        # First exists() returns 0 (collision), second returns non-zero (free).
        responses.append(_make_completed(["ssh"], returncode=0))
        responses.append(_make_completed(["ssh"], returncode=1))
        assert ws.safe_target_rel("note.md") == "note (1).md"

    def test_safe_target_rel_returns_input_when_free(self, fake_subprocess):
        _, responses = fake_subprocess
        ws = self._started_ws(fake_subprocess)
        responses.append(_make_completed(["ssh"], returncode=1))
        assert ws.safe_target_rel("fresh.md") == "fresh.md"


# ---------------------------------------------------------------- parse_workspace_spec


class TestParseWorkspaceSpecSsh:
    """``parse_workspace_spec`` learns ``ssh:`` prefix in Phase 31.1."""

    def test_local_unchanged(self, tmp_path):
        ws = parse_workspace_spec("local", workspace_root=tmp_path)
        assert isinstance(ws, LocalWorkspace)

    def test_ssh_minimal(self, tmp_path):
        ws = parse_workspace_spec("ssh:user@example.com", workspace_root=tmp_path)
        assert isinstance(ws, RemoteWorkspace)
        assert ws.host == "user@example.com"
        assert ws.port == DEFAULT_SSH_PORT
        assert ws.workspace_root_remote == DEFAULT_REMOTE_ROOT

    def test_ssh_with_port(self, tmp_path):
        ws = parse_workspace_spec("ssh:user@example.com:2222", workspace_root=tmp_path)
        assert isinstance(ws, RemoteWorkspace)
        assert ws.host == "user@example.com"
        assert ws.port == 2222
        assert ws.workspace_root_remote == DEFAULT_REMOTE_ROOT

    def test_ssh_with_remote_root(self, tmp_path):
        ws = parse_workspace_spec("ssh:user@example.com:/srv/wkspc", workspace_root=tmp_path)
        assert isinstance(ws, RemoteWorkspace)
        assert ws.host == "user@example.com"
        assert ws.port == DEFAULT_SSH_PORT
        assert ws.workspace_root_remote == "/srv/wkspc"

    def test_ssh_full_grammar(self, tmp_path):
        ws = parse_workspace_spec(
            "ssh:bob@build.example.com:2222:/srv/wkspc", workspace_root=tmp_path
        )
        assert isinstance(ws, RemoteWorkspace)
        assert ws.host == "bob@build.example.com"
        assert ws.port == 2222
        assert ws.workspace_root_remote == "/srv/wkspc"

    def test_ssh_alias_host_no_user(self, tmp_path):
        # Users with ~/.ssh/config aliases pass just the alias.
        ws = parse_workspace_spec("ssh:build-vm", workspace_root=tmp_path)
        assert isinstance(ws, RemoteWorkspace)
        assert ws.host == "build-vm"

    def test_ssh_missing_host_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="missing host"):
            parse_workspace_spec("ssh:", workspace_root=tmp_path)

    def test_unrecognised_prefix_mentions_ssh(self, tmp_path):
        with pytest.raises(ValueError, match="ssh:"):
            parse_workspace_spec("ftp://example.com", workspace_root=tmp_path)


# ---------------------------------------------------------------- layer 2 (opportunistic)


def _ssh_localhost_reachable() -> bool:
    """Layer-2 gate. Tests skip when:
    * ssh -V fails (no openssh client), or
    * ssh -o BatchMode=yes localhost true fails (no key-based auth).
    """
    if not _ssh_available():
        return False
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "localhost", "true"],
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_skip_no_ssh = pytest.mark.skipif(
    not _ssh_localhost_reachable(),
    reason="ssh to localhost with BatchMode=yes not available — skipping ssh-actual contract tests",
)


@_skip_no_ssh
class TestRemoteWorkspaceAgainstLocalhost:
    """Layer-2 — these run only on machines with passwordless ssh to
    localhost. Treat as opportunistic CI signal; main coverage is
    layer 1's mock subprocess suite."""

    @pytest.fixture
    def remote_ws(self, tmp_path):
        root = str(tmp_path / "remote-ws")
        ws = RemoteWorkspace(host="localhost", workspace_root_remote=root)
        ws.start()
        try:
            yield ws
        finally:
            ws.close()
            # Best-effort cleanup of the local dir we created via ssh.
            subprocess.run(["rm", "-rf", root], capture_output=True, timeout=5)

    def test_mkdir_and_exists(self, remote_ws):
        assert remote_ws.mkdir("subdir") is True
        assert remote_ws.exists("subdir") is True

    def test_write_then_read_roundtrip(self, remote_ws):
        remote_ws.write_text("note.md", "hello remote\n")
        assert remote_ws.read_text("note.md") == "hello remote\n"
