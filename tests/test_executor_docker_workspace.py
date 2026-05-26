"""Phase 29.1 — Executor uses DockerWorkspace as a drop-in.

Proves the Phase 28 abstraction does what it promised: injecting a
DockerWorkspace instead of the default LocalWorkspace runs the SAME
plan with the SAME ExecutionOutcome shape — only the file mutations
happen inside a container instead of on host disk.

Tests skip cleanly when Docker isn't reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.executor import Executor
from app.schemas import Action, ActionPlan, ActionType, RiskLevel
from app.storage.run_store import RunStore
from app.tools.docker_workspace import DockerWorkspace, _docker_available

_skip_no_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available — skipping container-actual tests",
)


def _mkdir(action_id: str, target: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MKDIR,
        target_path=target,
        reason="phase 29.1 mkdir",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
    )


def _index(action_id: str, target: str, content: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.INDEX,
        target_path=target,
        reason="phase 29.1 index",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={"content": content},
    )


def _plan(task_id: str, actions: list[Action]) -> ActionPlan:
    return ActionPlan(
        plan_id=f"plan-{task_id}",
        task_id=task_id,
        summary="phase 29.1 docker workspace integration",
        actions=actions,
    )


@pytest.fixture
def executor_with_docker(tmp_path: Path):
    """Executor backed by DockerWorkspace. Note workspace_root is the
    container-side path; the host filesystem is untouched."""
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    ws = DockerWorkspace()
    ws.start()
    # Executor's workspace_root is only used by the default
    # LocalWorkspace factory + by _record_implicit_parents'
    # resolve_inside calls — when we inject DockerWorkspace, the
    # injected instance's `root` (container path) takes over for
    # disk operations. We point workspace_root at a host tmp dir so
    # _record_implicit_parents has something to walk against (its
    # output is metadata-only when paths exist only in the container).
    host_ws = tmp_path / "host-ws"
    host_ws.mkdir()
    ex = Executor(workspace_root=host_ws, run_store=run_store, workspace=ws)
    try:
        yield ex, ws
    finally:
        ws.close()


@_skip_no_docker
class TestExecutorWithDockerWorkspace:
    """End-to-end: same Executor API + same plan + DockerWorkspace
    injection → all action types resolve inside the container."""

    def test_mkdir_plan_runs_in_container(self, executor_with_docker):
        ex, ws = executor_with_docker
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "sub/")])
        outcome = ex.execute(plan, approved=True)
        assert outcome.success, [r.error for r in outcome.records]
        # File created inside the container, NOT on the host.
        assert ws.exists("sub")
        # ExecutionRecord shape is identical to LocalWorkspace runs.
        assert len(outcome.records) == 1
        assert outcome.records[0].action_id == "a-1"

    def test_index_writes_to_container_filesystem(self, executor_with_docker):
        ex, ws = executor_with_docker
        plan = _plan(
            ex.run_store.task_id,
            [_index("a-1", "report.md", "# Hello from container")],
        )
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        # File contents readable through the container facade.
        assert "Hello from container" in ws.read_text("report.md")

    def test_multi_action_plan_preserves_order(self, executor_with_docker):
        """Multiple actions still execute in plan order through the
        container, and the rollback manifest gets the same shape."""
        ex, ws = executor_with_docker
        plan = _plan(
            ex.run_store.task_id,
            [
                _mkdir("a-1", "docs"),
                _index("a-2", "docs/note.md", "first note"),
                _index("a-3", "docs/note2.md", "second note"),
            ],
        )
        outcome = ex.execute(plan, approved=True)
        assert outcome.success, [r.error for r in outcome.records]
        # All three landed.
        assert ws.exists("docs")
        assert ws.read_text("docs/note.md") == "first note"
        assert ws.read_text("docs/note2.md") == "second note"
        # Manifest has entries for the mkdir + two file writes.
        manifest_ops = [e.op.value for e in outcome.manifest.entries]
        assert "delete_created_dir" in manifest_ops
        assert manifest_ops.count("delete_created_file") >= 2

    def test_host_filesystem_untouched(self, executor_with_docker, tmp_path: Path):
        """The host's workspace_root must stay empty — DockerWorkspace
        is full isolation, no bind mount in the default config."""
        ex, ws = executor_with_docker
        plan = _plan(
            ex.run_store.task_id,
            [_index("a-1", "should_be_isolated.md", "container only")],
        )
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        # File exists in the container.
        assert ws.exists("should_be_isolated.md")
        # File does NOT exist on the host's workspace_root.
        host_ws = ex.workspace_root
        assert not (host_ws / "should_be_isolated.md").exists()
