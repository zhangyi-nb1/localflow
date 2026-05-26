"""Phase 29.0 — DockerWorkspace tests.

Two layers:
  1. Path-defence + parameter validation — runs without Docker.
  2. Container-actual contract suite — skipped when ``docker`` CLI
     not on PATH / daemon down. Mirrors the LocalWorkspace 27-test
     contract from Phase 28 so both implementations honour an
     identical Workspace surface.
"""

from __future__ import annotations

import pytest

from app.tools.docker_workspace import (
    DEFAULT_IMAGE,
    DockerUnavailable,
    DockerWorkspace,
    DockerWorkspaceError,
    _docker_available,
    _validate_rel_path,
)
from app.tools.workspace import Workspace, WorkspaceStat

# ────────────────────────── Layer 1: cheap, no Docker required


class TestValidateRelPath:
    """Path-traversal defence is enforced HOST-SIDE before any docker
    exec — these tests run without Docker installed."""

    def test_normal_relative_path(self):
        assert _validate_rel_path("foo/bar.txt") == "foo/bar.txt"

    def test_empty_string_returns_empty(self):
        assert _validate_rel_path("") == ""

    def test_none_returns_empty(self):
        assert _validate_rel_path(None) == ""

    def test_absolute_path_rejected(self):
        with pytest.raises(DockerWorkspaceError) as exc:
            _validate_rel_path("/etc/passwd")
        assert "absolute" in str(exc.value)

    def test_home_shorthand_rejected(self):
        with pytest.raises(DockerWorkspaceError):
            _validate_rel_path("~/secrets")

    def test_drive_letter_rejected(self):
        with pytest.raises(DockerWorkspaceError) as exc:
            _validate_rel_path("C:/Windows/cmd.exe")
        assert "drive-letter" in str(exc.value)

    def test_parent_traversal_rejected(self):
        with pytest.raises(DockerWorkspaceError) as exc:
            _validate_rel_path("../escaped.txt")
        assert "parent-directory" in str(exc.value)

    def test_embedded_parent_traversal_rejected(self):
        with pytest.raises(DockerWorkspaceError):
            _validate_rel_path("foo/../bar.txt")

    def test_backslash_normalised(self):
        # Windows-style paths get normalised before split-check.
        assert _validate_rel_path("foo\\bar.txt") == "foo/bar.txt"

    def test_unc_path_rejected(self):
        with pytest.raises(DockerWorkspaceError) as exc:
            _validate_rel_path("\\\\server\\share")
        assert "absolute" in str(exc.value)


class TestConstruction:
    """Construction-time behaviour that doesn't need Docker running."""

    def test_default_image(self):
        ws = DockerWorkspace()
        assert ws.image == DEFAULT_IMAGE
        assert ws.image == "python:3.12-slim"
        assert ws.workspace_root_inside == "/workspace"

    def test_custom_image(self):
        ws = DockerWorkspace(image="alpine:latest")
        assert ws.image == "alpine:latest"

    def test_root_property_returns_container_path(self):
        from pathlib import Path

        ws = DockerWorkspace()
        assert ws.root == Path("/workspace")

    def test_is_local_returns_false(self):
        ws = DockerWorkspace()
        assert ws.is_local() is False

    def test_implements_workspace_protocol(self):
        ws = DockerWorkspace()
        # runtime_checkable Protocol means isinstance works.
        assert isinstance(ws, Workspace)

    def test_unstarted_methods_raise(self):
        ws = DockerWorkspace()
        with pytest.raises(DockerWorkspaceError) as exc:
            ws._require_started()
        assert "not started" in str(exc.value)


class TestFailGracefullyWithoutDocker:
    """When Docker isn't installed, start() must raise DockerUnavailable
    so callers can fall back to LocalWorkspace with a clear error."""

    def test_is_available_matches_helper(self):
        # Either Docker is available (CI Linux runner) or not (mac
        # dev box). Both are valid; just assert the helper is honest.
        assert isinstance(DockerWorkspace.is_available(), bool)
        assert DockerWorkspace.is_available() == _docker_available()

    @pytest.mark.skipif(_docker_available(), reason="docker IS available in this env")
    def test_start_without_docker_raises(self):
        ws = DockerWorkspace()
        with pytest.raises(DockerUnavailable):
            ws.start()


# ────────────────────────── Layer 2: container-actual contract tests


# CLASS-level skip marker so only the container-actual tests skip
# when Docker isn't reachable — the Layer-1 path-defence + ctor tests
# above always run. Mirrors Phase 28's LocalWorkspace contract suite
# so a future parameterised fixture can run the SAME bodies across
# both impls.
_skip_no_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available — skipping container-actual tests",
)


@pytest.fixture
def workspace() -> DockerWorkspace:
    """Fresh container per test. Cleans up via close()."""
    ws = DockerWorkspace(image=DEFAULT_IMAGE)
    ws.start()
    try:
        yield ws
    finally:
        ws.close()


@_skip_no_docker
class TestContainerLifecycle:
    def test_start_creates_container(self, workspace: DockerWorkspace):
        assert workspace._started is True
        assert workspace.container_id is not None
        assert workspace.container_name is not None

    def test_close_is_idempotent(self, workspace: DockerWorkspace):
        workspace.close()
        # Second close must not raise.
        workspace.close()

    def test_context_manager_lifecycle(self):
        with DockerWorkspace() as ws:
            assert ws._started is True
            cid = ws.container_id
            assert cid is not None
        # After context exit, the container should be gone.
        assert ws._started is False


@_skip_no_docker
class TestContainerReads:
    def test_exists_for_present_file(self, workspace: DockerWorkspace):
        workspace.write_text("hi.txt", "hi")
        assert workspace.exists("hi.txt") is True

    def test_exists_for_absent_file(self, workspace: DockerWorkspace):
        assert workspace.exists("nope.txt") is False

    def test_stat_returns_size(self, workspace: DockerWorkspace):
        workspace.write_text("f.txt", "hello")
        st = workspace.stat("f.txt")
        assert st is not None
        assert isinstance(st, WorkspaceStat)
        assert st.is_file
        assert st.size_bytes == 5

    def test_stat_missing_returns_none(self, workspace: DockerWorkspace):
        assert workspace.stat("nope.txt") is None

    def test_stat_directory(self, workspace: DockerWorkspace):
        workspace.mkdir("sub")
        st = workspace.stat("sub")
        assert st is not None
        assert st.is_dir

    def test_sha256_for_file(self, workspace: DockerWorkspace):
        workspace.write_text("f.txt", "hello")
        digest = workspace.sha256("f.txt")
        assert digest is not None
        assert len(digest) == 64
        assert digest == workspace.sha256("f.txt")  # stable

    def test_sha256_returns_none_for_dir(self, workspace: DockerWorkspace):
        workspace.mkdir("sub")
        assert workspace.sha256("sub") is None

    def test_list_dir(self, workspace: DockerWorkspace):
        workspace.write_text("a.txt", "a")
        workspace.write_text("b.txt", "b")
        workspace.mkdir("c_dir")
        assert workspace.list_dir() == ["a.txt", "b.txt", "c_dir"]

    def test_read_text_and_bytes(self, workspace: DockerWorkspace):
        workspace.write_text("f.txt", "hello")
        assert workspace.read_text("f.txt") == "hello"
        assert workspace.read_bytes("f.txt") == b"hello"


@_skip_no_docker
class TestContainerWrites:
    def test_mkdir_creates(self, workspace: DockerWorkspace):
        assert workspace.mkdir("new_dir") is True
        assert workspace.exists("new_dir")

    def test_mkdir_idempotent(self, workspace: DockerWorkspace):
        workspace.mkdir("d")
        assert workspace.mkdir("d") is False

    def test_mkdir_with_parents(self, workspace: DockerWorkspace):
        workspace.mkdir("a/b/c")
        assert workspace.exists("a/b/c")

    def test_write_bytes(self, workspace: DockerWorkspace):
        workspace.write_bytes("blob.bin", b"\x01\x02\x03")
        assert workspace.read_bytes("blob.bin") == b"\x01\x02\x03"

    def test_move(self, workspace: DockerWorkspace):
        workspace.write_text("src.txt", "x")
        workspace.mkdir("archive")
        workspace.move("src.txt", "archive/src.txt")
        assert not workspace.exists("src.txt")
        assert workspace.read_text("archive/src.txt") == "x"

    def test_copy(self, workspace: DockerWorkspace):
        workspace.write_text("src.txt", "y")
        workspace.copy("src.txt", "copy.txt")
        assert workspace.exists("src.txt")
        assert workspace.read_text("copy.txt") == "y"

    def test_safe_target_rel_no_collision(self, workspace: DockerWorkspace):
        # Free name → returned as-is.
        assert workspace.safe_target_rel("free.txt") == "free.txt"

    def test_safe_target_rel_auto_suffix(self, workspace: DockerWorkspace):
        workspace.write_text("foo.txt", "x")
        new = workspace.safe_target_rel("foo.txt")
        assert new == "foo (1).txt"


@_skip_no_docker
class TestPathDefenceWithRealContainer:
    """Even with a real container, host-side validation must reject
    path-traversal BEFORE the exec — otherwise a clever rel_path
    would let a misbehaving caller escape /workspace inside the
    container."""

    def test_mkdir_rejects_dotdot(self, workspace: DockerWorkspace):
        with pytest.raises(DockerWorkspaceError):
            workspace.mkdir("../escaped")

    def test_write_text_rejects_absolute(self, workspace: DockerWorkspace):
        with pytest.raises(DockerWorkspaceError):
            workspace.write_text("/tmp/escaped.txt", "x")

    def test_read_text_rejects_escape(self, workspace: DockerWorkspace):
        with pytest.raises(DockerWorkspaceError):
            workspace.read_text("../etc/passwd")
