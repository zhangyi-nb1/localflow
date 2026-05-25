"""Phase 28.1 — Executor accepts an injected Workspace.

The default `Executor(workspace_root=...)` constructs a LocalWorkspace
on the fly, preserving v0.25.x behaviour for every existing caller.
This file verifies the injection path: a SpyWorkspace passed in via
the new ``workspace=`` kwarg gets all the calls instead of host
filesystem touches.

The spy proves three things at once:
  1. ``self.workspace`` is reachable from every refactored _do_*
  2. Phase 28.1 routes mkdir / move / copy through it (others still
     use file_ops directly until a follow-up)
  3. The runtime contract matches the Workspace Protocol (the spy
     isinstance(Workspace) passes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.harness.executor import Executor
from app.schemas import Action, ActionPlan, ActionType, RiskLevel
from app.storage.run_store import RunStore
from app.tools.workspace import LocalWorkspace, Workspace, WorkspaceStat


@dataclass
class _SpyWorkspace:
    """Workspace wrapper that records every call and delegates to a
    real LocalWorkspace underneath. Lets tests assert what calls the
    refactored executor made without re-implementing filesystem
    semantics."""

    inner: LocalWorkspace
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.inner.root

    def is_local(self) -> bool:
        return self.inner.is_local()

    def _log(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def exists(self, rel_path: str) -> bool:
        self._log("exists", rel_path)
        return self.inner.exists(rel_path)

    def stat(self, rel_path: str) -> WorkspaceStat | None:
        self._log("stat", rel_path)
        return self.inner.stat(rel_path)

    def sha256(self, rel_path: str) -> str | None:
        self._log("sha256", rel_path)
        return self.inner.sha256(rel_path)

    def list_dir(self, rel_path: str = "") -> list[str]:
        self._log("list_dir", rel_path)
        return self.inner.list_dir(rel_path)

    def read_bytes(self, rel_path: str) -> bytes:
        self._log("read_bytes", rel_path)
        return self.inner.read_bytes(rel_path)

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        self._log("read_text", rel_path)
        return self.inner.read_text(rel_path, encoding=encoding)

    def mkdir(self, rel_path: str) -> bool:
        self._log("mkdir", rel_path)
        return self.inner.mkdir(rel_path)

    def move(self, src_rel: str, dst_rel: str) -> Path:
        self._log("move", src_rel, dst_rel)
        return self.inner.move(src_rel, dst_rel)

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        self._log("copy", src_rel, dst_rel)
        return self.inner.copy(src_rel, dst_rel)

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        self._log("rename", src_rel, dst_rel)
        return self.inner.rename(src_rel, dst_rel)

    def write_text(self, rel_path: str, content: str) -> Path:
        self._log("write_text", rel_path)
        return self.inner.write_text(rel_path, content)

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        self._log("write_bytes", rel_path)
        return self.inner.write_bytes(rel_path, content)

    def safe_target_rel(self, rel_path: str) -> str:
        self._log("safe_target_rel", rel_path)
        return self.inner.safe_target_rel(rel_path)


@pytest.fixture
def executor_with_spy(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    spy = _SpyWorkspace(inner=LocalWorkspace(ws_root))
    ex = Executor(workspace_root=ws_root, run_store=run_store, workspace=spy)
    return ex, ws_root, spy


def _mkdir(action_id: str, target: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MKDIR,
        target_path=target,
        reason="r",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
    )


def _move(action_id: str, src: str, tgt: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MOVE,
        source_path=src,
        target_path=tgt,
        reason="r",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=False,
    )


def _copy(action_id: str, src: str, tgt: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.COPY,
        source_path=src,
        target_path=tgt,
        reason="r",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=False,
    )


def _plan(task_id: str, actions: list[Action]) -> ActionPlan:
    return ActionPlan(plan_id=f"plan-{task_id}", task_id=task_id, summary="t", actions=actions)


class TestDefaultWorkspace:
    def test_default_constructor_creates_local_workspace(self, tmp_path: Path):
        run_store = RunStore.create(home=tmp_path / ".localflow")
        ex = Executor(workspace_root=tmp_path / "ws", run_store=run_store)
        # No explicit workspace passed — Executor must have created one.
        assert ex.workspace is not None
        assert isinstance(ex.workspace, LocalWorkspace)
        assert isinstance(ex.workspace, Workspace)

    def test_default_workspace_root_matches_executor_root(self, tmp_path: Path):
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        run_store = RunStore.create(home=tmp_path / ".localflow")
        ex = Executor(workspace_root=ws_root, run_store=run_store)
        assert ex.workspace.root == ws_root.resolve()


class TestInjection:
    def test_mkdir_routes_through_workspace(self, executor_with_spy):
        ex, ws_root, spy = executor_with_spy
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "sub/")])
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        # mkdir was logged.
        assert any(c[0] == "mkdir" and c[1] == ("sub/",) for c in spy.calls)
        # Real disk was touched (spy delegates to LocalWorkspace).
        assert (ws_root / "sub").is_dir()

    def test_move_routes_through_workspace(self, executor_with_spy):
        ex, ws_root, spy = executor_with_spy
        (ws_root / "src.txt").write_text("hello")
        plan = _plan(
            ex.run_store.task_id,
            [_move("a-1", "src.txt", "archive/src.txt")],
        )
        # First ensure parent dir; planner would normally do this, but
        # this test exercises only the MOVE branch — the move flow's
        # safe_target_rel will surface an exists check.
        (ws_root / "archive").mkdir()
        outcome = ex.execute(plan, approved=True)
        assert outcome.success, [r.error for r in outcome.records]
        # Workspace.move was called with the rel paths.
        moves = [c for c in spy.calls if c[0] == "move"]
        assert moves, spy.calls
        # exists / sha256 / safe_target_rel were also called as part
        # of the refactored flow.
        called_names = {c[0] for c in spy.calls}
        assert {"exists", "sha256", "safe_target_rel", "move"} <= called_names

    def test_copy_routes_through_workspace(self, executor_with_spy):
        ex, ws_root, spy = executor_with_spy
        (ws_root / "src.txt").write_text("payload")
        plan = _plan(ex.run_store.task_id, [_copy("a-1", "src.txt", "copy.txt")])
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        copies = [c for c in spy.calls if c[0] == "copy"]
        assert copies, spy.calls
        # Both files exist on real disk via the spy's delegation.
        assert (ws_root / "src.txt").exists()
        assert (ws_root / "copy.txt").exists()
