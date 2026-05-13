from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas import TaskSpec
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A small synthetic messy workspace inside tmp_path."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.pdf").write_text("paper a content", encoding="utf-8")
    (root / "b.pdf").write_text("paper b content", encoding="utf-8")
    (root / "c.txt").write_text("note c", encoding="utf-8")
    (root / "d.csv").write_text("col1,col2\n1,2\n", encoding="utf-8")
    (root / "e.jpg").write_bytes(b"\xff\xd8fake")
    # duplicate of a.pdf for dedup detection
    (root / "subdir").mkdir()
    (root / "subdir" / "a_copy.pdf").write_text("paper a content", encoding="utf-8")
    return root


@pytest.fixture()
def run_store(tmp_path: Path) -> RunStore:
    """Isolated RunStore rooted at tmp_path/.localflow."""
    home = tmp_path / ".localflow"
    return RunStore.create(home=home)


@pytest.fixture()
def task(workspace: Path, run_store: RunStore) -> TaskSpec:
    return TaskSpec(
        task_id=run_store.task_id,
        user_goal="test goal",
        workspace_root=str(workspace),
        constraints=["no delete"],
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )


@pytest.fixture()
def snapshot(workspace: Path, task: TaskSpec):
    return scan_workspace(workspace, task.task_id, compute_hash=True)
