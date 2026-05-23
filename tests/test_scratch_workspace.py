"""Phase 23 — ScratchWorkspace lifecycle tests.

Confirms the on-disk layout and the copy/cleanup paths behave the way
the executor and rollback rely on. No subprocess work here — that's
the SandboxRuntime test's job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.compute import ComputeInputRef
from app.tools.scratch import ScratchWorkspace


def _ref(rel: str) -> ComputeInputRef:
    return ComputeInputRef(rel_path=rel, size_bytes=10)


def test_create_for_action_creates_inputs_and_outputs_dirs(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    layout = sw.create_for_action("t-001", "a-001")
    assert layout.root.exists()
    assert layout.inputs_dir.is_dir()
    assert layout.outputs_dir.is_dir()
    assert layout.script_path.parent == layout.root
    # Scratch root must be OUTSIDE any workspace; we only enforce
    # location relative to home here.
    assert layout.root == tmp_path / "scratch" / "t-001" / "a-001"


def test_create_for_action_is_idempotent_and_wipes_stale(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    first = sw.create_for_action("t-001", "a-001")
    stale = first.outputs_dir / "stale.txt"
    stale.write_text("old", encoding="utf-8")
    assert stale.exists()
    # Second call wipes the prior subtree.
    second = sw.create_for_action("t-001", "a-001")
    assert second.root == first.root
    assert not stale.exists()


def test_copy_inputs_copies_workspace_files_into_inputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data").mkdir()
    (workspace / "data" / "raw.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")

    sw = ScratchWorkspace(home=tmp_path / "home")
    layout = sw.create_for_action("t-001", "a-001")
    copied = sw.copy_inputs(layout, workspace, [_ref("data/raw.csv")])
    assert len(copied) == 1
    dst = layout.inputs_dir / "data" / "raw.csv"
    assert dst.is_file()
    assert dst.read_text(encoding="utf-8") == "col1,col2\n1,2\n"


def test_copy_inputs_rejects_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    # Symlink trick — point a workspace path at the outside file.
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    sw = ScratchWorkspace(home=tmp_path / "home")
    layout = sw.create_for_action("t-001", "a-001")
    with pytest.raises(ValueError, match="outside workspace"):
        sw.copy_inputs(layout, workspace, [_ref("link.txt")])


def test_copy_inputs_rejects_missing_input(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sw = ScratchWorkspace(home=tmp_path / "home")
    layout = sw.create_for_action("t-001", "a-001")
    with pytest.raises(FileNotFoundError):
        sw.copy_inputs(layout, workspace, [_ref("missing.csv")])


def test_cleanup_action_removes_subtree(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    layout = sw.create_for_action("t-001", "a-001")
    (layout.outputs_dir / "clean.csv").write_text("ok", encoding="utf-8")
    assert sw.cleanup_action("t-001", "a-001") is True
    assert not layout.root.exists()
    # Task dir is pruned when empty.
    assert not (tmp_path / "scratch" / "t-001").exists()


def test_cleanup_action_keeps_task_dir_when_other_actions_remain(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    sw.create_for_action("t-001", "a-001")
    sw.create_for_action("t-001", "a-002")
    sw.cleanup_action("t-001", "a-001")
    # task dir survives because a-002 is still there
    assert (tmp_path / "scratch" / "t-001" / "a-002").exists()


def test_cleanup_action_noop_when_missing(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    assert sw.cleanup_action("t-001", "a-001") is False


def test_cleanup_task_removes_entire_task_subtree(tmp_path: Path) -> None:
    sw = ScratchWorkspace(home=tmp_path)
    sw.create_for_action("t-001", "a-001")
    sw.create_for_action("t-001", "a-002")
    assert sw.cleanup_task("t-001") is True
    assert not (tmp_path / "scratch" / "t-001").exists()
