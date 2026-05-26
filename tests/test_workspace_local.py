"""Phase 28.0 — LocalWorkspace contract tests.

This same suite will, in Phase 29+, be parameterised across
DockerWorkspace / RemoteWorkspace so all three implementations
honour an identical contract. For now the fixture only yields
LocalWorkspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.policy_guard import PolicyViolation
from app.tools.workspace import (
    LocalWorkspace,
    Workspace,
    WorkspaceStat,
    parse_workspace_spec,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    return LocalWorkspace(ws_root)


class TestProperties:
    def test_root_is_absolute(self, workspace: Workspace):
        assert workspace.root.is_absolute()

    def test_is_local_returns_true(self, workspace: Workspace):
        assert workspace.is_local() is True

    def test_protocol_check(self, workspace: Workspace):
        # runtime_checkable Protocol — LocalWorkspace should pass.
        assert isinstance(workspace, Workspace)


class TestReads:
    def test_exists_for_present_file(self, workspace: Workspace):
        (workspace.root / "hi.txt").write_text("hi")
        assert workspace.exists("hi.txt") is True

    def test_exists_for_absent_file(self, workspace: Workspace):
        assert workspace.exists("nope.txt") is False

    def test_exists_path_traversal_returns_false(self, workspace: Workspace):
        # PolicyViolation caught → False (safer than raising on a
        # purely read-side check).
        assert workspace.exists("../escaped.txt") is False

    def test_stat_returns_size_and_kind(self, workspace: Workspace):
        (workspace.root / "f.txt").write_text("hello")
        st = workspace.stat("f.txt")
        assert st is not None
        assert isinstance(st, WorkspaceStat)
        assert st.is_file
        assert not st.is_dir
        assert st.size_bytes == 5

    def test_stat_returns_none_for_missing(self, workspace: Workspace):
        assert workspace.stat("nope.txt") is None

    def test_stat_directory(self, workspace: Workspace):
        (workspace.root / "sub").mkdir()
        st = workspace.stat("sub")
        assert st is not None
        assert st.is_dir

    def test_sha256_for_file(self, workspace: Workspace):
        (workspace.root / "f.txt").write_text("hello")
        digest = workspace.sha256("f.txt")
        assert digest is not None
        assert len(digest) == 64  # sha-256 hex
        # Stable + deterministic
        assert digest == workspace.sha256("f.txt")

    def test_sha256_returns_none_for_dir(self, workspace: Workspace):
        (workspace.root / "sub").mkdir()
        assert workspace.sha256("sub") is None

    def test_list_dir_workspace_root(self, workspace: Workspace):
        (workspace.root / "a.txt").write_text("a")
        (workspace.root / "b.txt").write_text("b")
        (workspace.root / "c_dir").mkdir()
        assert workspace.list_dir() == ["a.txt", "b.txt", "c_dir"]

    def test_list_dir_subdirectory(self, workspace: Workspace):
        (workspace.root / "sub").mkdir()
        (workspace.root / "sub" / "x.md").write_text("x")
        assert workspace.list_dir("sub") == ["x.md"]

    def test_read_text_and_bytes(self, workspace: Workspace):
        (workspace.root / "f.txt").write_text("hello", encoding="utf-8")
        assert workspace.read_text("f.txt") == "hello"
        assert workspace.read_bytes("f.txt") == b"hello"


class TestWrites:
    def test_mkdir_creates(self, workspace: Workspace):
        created = workspace.mkdir("new_dir")
        assert created is True
        assert (workspace.root / "new_dir").is_dir()

    def test_mkdir_is_idempotent(self, workspace: Workspace):
        workspace.mkdir("d")
        # Second mkdir on same path returns False per file_ops.mkdir.
        assert workspace.mkdir("d") is False

    def test_mkdir_creates_parents(self, workspace: Workspace):
        workspace.mkdir("a/b/c")
        assert (workspace.root / "a" / "b" / "c").is_dir()

    def test_write_text_and_read_back(self, workspace: Workspace):
        workspace.write_text("note.md", "# hello")
        assert (workspace.root / "note.md").read_text() == "# hello"
        assert workspace.read_text("note.md") == "# hello"

    def test_write_bytes(self, workspace: Workspace):
        workspace.write_bytes("blob.bin", b"\x01\x02\x03")
        assert (workspace.root / "blob.bin").read_bytes() == b"\x01\x02\x03"

    def test_move_file(self, workspace: Workspace):
        workspace.write_text("src.txt", "x")
        workspace.mkdir("archive")
        workspace.move("src.txt", "archive/src.txt")
        assert not (workspace.root / "src.txt").exists()
        assert (workspace.root / "archive" / "src.txt").read_text() == "x"

    def test_copy_file(self, workspace: Workspace):
        workspace.write_text("src.txt", "y")
        workspace.copy("src.txt", "copy.txt")
        # Both still exist.
        assert (workspace.root / "src.txt").read_text() == "y"
        assert (workspace.root / "copy.txt").read_text() == "y"

    def test_rename_file(self, workspace: Workspace):
        workspace.write_text("old.txt", "z")
        workspace.rename("old.txt", "new.txt")
        assert not (workspace.root / "old.txt").exists()
        assert (workspace.root / "new.txt").read_text() == "z"


class TestPathTraversalDefence:
    """All write methods must reject escapes BEFORE touching disk —
    same gate ``policy_guard.resolve_inside`` provides for the rest
    of the harness."""

    def test_mkdir_rejects_dotdot(self, workspace: Workspace):
        with pytest.raises(PolicyViolation):
            workspace.mkdir("../escaped")

    def test_write_text_rejects_absolute(self, workspace: Workspace):
        with pytest.raises(PolicyViolation):
            workspace.write_text("/tmp/escaped.txt", "x")

    def test_move_rejects_escape_in_source(self, workspace: Workspace):
        with pytest.raises(PolicyViolation):
            workspace.move("../src.txt", "dst.txt")

    def test_move_rejects_escape_in_destination(self, workspace: Workspace):
        workspace.write_text("src.txt", "x")
        with pytest.raises(PolicyViolation):
            workspace.move("src.txt", "../escaped.txt")

    def test_read_text_rejects_escape(self, workspace: Workspace):
        with pytest.raises(PolicyViolation):
            workspace.read_text("../etc/passwd")


class TestParseWorkspaceSpec:
    """Phase 29.2 — CLI / Recipe ``--workspace <spec>`` parser."""

    def test_empty_string_returns_local(self, tmp_path: Path):
        ws = parse_workspace_spec("", workspace_root=tmp_path)
        assert isinstance(ws, LocalWorkspace)

    def test_local_keyword_returns_local(self, tmp_path: Path):
        ws = parse_workspace_spec("local", workspace_root=tmp_path)
        assert isinstance(ws, LocalWorkspace)
        assert ws.root == tmp_path.resolve()

    def test_docker_prefix_returns_docker(self, tmp_path: Path):
        from app.tools.docker_workspace import DockerWorkspace

        ws = parse_workspace_spec("docker:python:3.12-slim", workspace_root=tmp_path)
        assert isinstance(ws, DockerWorkspace)
        assert ws.image == "python:3.12-slim"

    def test_docker_prefix_with_custom_image(self, tmp_path: Path):
        from app.tools.docker_workspace import DockerWorkspace

        ws = parse_workspace_spec("docker:alpine:latest", workspace_root=tmp_path)
        assert isinstance(ws, DockerWorkspace)
        assert ws.image == "alpine:latest"

    def test_docker_without_image_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc:
            parse_workspace_spec("docker:", workspace_root=tmp_path)
        assert "missing image" in str(exc.value)

    def test_unknown_prefix_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc:
            parse_workspace_spec("remote://host/path", workspace_root=tmp_path)
        assert "unrecognised" in str(exc.value)
