"""Phase 30.2 — end-to-end kernel package smoke test.

Proves the facade in ``localflow_kernel`` is wired so a downstream
consumer can build and run a full plan/execute/verify cycle without
importing from ``app.*``. The only ``app.*`` imports allowed in this
file are inside the static-analysis sanity check at the bottom; the
real test exercises the kernel through its public surface.

If this test breaks, ``localflow_kernel.__init__`` lost a public symbol
or the underlying implementation moved without a corresponding facade
update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Phase 30.2 contract: every symbol below must be reachable from the
# kernel package alone. No ``from app.*`` imports are needed.
from localflow_kernel import (
    Action,
    ActionPlan,
    ActionType,
    Executor,
    LocalWorkspace,
    RiskLevel,
    RunStore,
)
from localflow_kernel.harness import Verifier
from localflow_kernel.schemas import ExecutionStatus, TaskSpec, WorkspaceSnapshot
from localflow_kernel.workspace import parse_workspace_spec


@pytest.fixture
def task_setup(tmp_path: Path):
    """Set up a minimal kernel-only task: workspace + run_store + task."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    task = TaskSpec(
        task_id=run_store.task_id,
        user_goal="kernel package smoke test",
        workspace_root=str(workspace_root),
    )
    return workspace_root, run_store, task


class TestKernelPackageSurface:
    """Top-level facade re-exports the names the README + KERNEL_PACKAGE.md
    promise. If anything goes missing this trips."""

    def test_version_attribute_present(self):
        import localflow_kernel

        assert isinstance(localflow_kernel.__version__, str)
        assert localflow_kernel.__version__.count(".") >= 2  # "X.Y.Z" or "X.Y.Z.devN"

    def test_all_lists_match_actual_exports(self):
        import localflow_kernel

        for name in localflow_kernel.__all__:
            assert hasattr(localflow_kernel, name), (
                f"localflow_kernel.__all__ promises {name!r} but it's missing"
            )

    def test_submodule_facades_loadable(self):
        # Every submodule should import without pulling in app-layer
        # things via side-effect.
        import localflow_kernel.harness  # noqa: F401
        import localflow_kernel.llm  # noqa: F401
        import localflow_kernel.schemas  # noqa: F401
        import localflow_kernel.storage  # noqa: F401
        import localflow_kernel.workspace  # noqa: F401


class TestKernelPackageEndToEnd:
    """Build a real plan, execute it, verify it — using ONLY the kernel
    package. If the facade is incomplete this test fails."""

    def test_mkdir_plan_runs_through_kernel_only(self, task_setup):
        workspace_root, run_store, task = task_setup
        plan = ActionPlan(
            plan_id="kernel-test-plan",
            task_id=task.task_id,
            summary="phase 30 kernel-only test",
            actions=[
                Action(
                    action_id="a-1",
                    action_type=ActionType.MKDIR,
                    target_path="subdir/",
                    reason="kernel package facade smoke test",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=False,
                )
            ],
        )

        ws = LocalWorkspace(workspace_root)
        ex = Executor(
            workspace_root=workspace_root,
            run_store=run_store,
            workspace=ws,
        )
        outcome = ex.execute(plan, approved=True)

        assert outcome.success
        assert (workspace_root / "subdir").is_dir()
        assert len(outcome.records) == 1
        assert outcome.records[0].status == ExecutionStatus.SUCCESS

    def test_index_plan_writes_file_then_verifier_passes(self, task_setup):
        workspace_root, run_store, task = task_setup
        plan = ActionPlan(
            plan_id="kernel-test-plan-2",
            task_id=task.task_id,
            summary="phase 30 kernel-only write+verify",
            actions=[
                Action(
                    action_id="a-1",
                    action_type=ActionType.INDEX,
                    target_path="note.md",
                    reason="kernel facade end-to-end",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=False,
                    metadata={"content": "kernel-only write\n"},
                )
            ],
        )

        ws = LocalWorkspace(workspace_root)
        ex = Executor(
            workspace_root=workspace_root,
            run_store=run_store,
            workspace=ws,
        )
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        assert (workspace_root / "note.md").read_text() == "kernel-only write\n"

        # Minimal empty snapshot — fresh workspace had no pre-existing files.
        snapshot = WorkspaceSnapshot(
            snapshot_id="kernel-test-snap",
            task_id=task.task_id,
            root=str(workspace_root),
        )
        verifier = Verifier(workspace_root=workspace_root)
        result = verifier.verify(
            task_id=task.task_id,
            run_id=outcome.run_id,
            plan=plan,
            manifest=outcome.manifest,
            executed_action_ids={"a-1"},
            skipped_action_ids=set(),
            failed_action_ids=set(),
            original_snapshot=snapshot,
        )
        assert result.passed, [c.detail for c in result.checks if not c.passed]


class TestParseWorkspaceSpecFromKernel:
    """The factory should also be reachable + functional through the
    kernel namespace."""

    def test_local_default(self, tmp_path: Path):
        ws = parse_workspace_spec("local", workspace_root=tmp_path)
        assert isinstance(ws, LocalWorkspace)
        assert ws.is_local()

    def test_empty_string_means_local(self, tmp_path: Path):
        ws = parse_workspace_spec("", workspace_root=tmp_path)
        assert isinstance(ws, LocalWorkspace)

    def test_unrecognised_prefix_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            parse_workspace_spec("nonsense:foo", workspace_root=tmp_path)
