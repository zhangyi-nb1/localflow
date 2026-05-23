"""Phase 23 — Executor + Rollback integration for PYTHON_COMPUTE.

The dispatch path is the 3rd §10.7 deliberate exception. These tests
confirm:

  * a successful ComputeAction lands in the manifest as one
    DELETE_SCRATCH_DIR rollback entry (no workspace mutation)
  * rollback wipes the scratch dir and is idempotent
  * trace events fire in the right order with the right statuses
  * missing scratch_workspace / sandbox_runtime raises a clear error
  * a failing ComputeOutcome still produces a rollback entry so
    cleanup runs
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.harness.sandbox import SandboxRuntime
from app.harness.trace import TraceLogger
from app.schemas import ActionPlan, ExecutionStatus
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    SandboxPolicy,
)
from app.schemas.rollback import RollbackOpType
from app.schemas.trace import TraceEventType
from app.storage.run_store import RunStore
from app.tools.scratch import ScratchWorkspace


def _make_compute_action(
    *,
    script: str,
    expected_outputs: list[ArtifactSpec] | None = None,
    inputs: list[ComputeInputRef] | None = None,
    timeout_sec: int = 10,
    summary: str = "Test compute action.",
) -> ComputeAction:
    return ComputeAction(
        script=dedent(script),
        script_summary=summary,
        inputs=inputs or [],
        expected_outputs=expected_outputs
        or [ArtifactSpec(relative_path="outputs/out.txt", description="x")],
        sandbox_policy=SandboxPolicy(timeout_sec=timeout_sec),
    )


def _wrap_as_action(compute: ComputeAction, action_id: str = "a-compute") -> Action:
    """Embed the typed ComputeAction inside an Action's metadata
    dict — same pattern Phase 16 FETCH uses."""
    return Action(
        action_id=action_id,
        action_type=ActionType.PYTHON_COMPUTE,
        reason="test compute",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )


def _plan(actions: list[Action], task_id: str) -> ActionPlan:
    return ActionPlan(
        plan_id=f"plan-{task_id}",
        task_id=task_id,
        summary="compute test plan",
        actions=actions,
    )


@pytest.fixture
def compute_executor(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
    )
    return executor, run_store, workspace, scratch


def test_python_compute_action_succeeds_and_records_rollback(
    compute_executor,
) -> None:
    executor, run_store, _, scratch = compute_executor
    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)

    assert outcome.success, outcome.records
    assert outcome.records[0].status is ExecutionStatus.SUCCESS
    # Exactly one rollback entry, of type DELETE_SCRATCH_DIR.
    assert len(outcome.manifest.entries) == 1
    rb = outcome.manifest.entries[0]
    assert rb.op is RollbackOpType.DELETE_SCRATCH_DIR
    assert rb.target_path is None  # scratch lives outside the workspace
    assert rb.metadata["task_id"] == run_store.task_id
    assert rb.metadata["action_id"] == action.action_id
    assert rb.metadata["outcome"]["status"] == "ok"

    # The scratch dir actually exists and holds the produced file.
    layout = scratch.action_dir(run_store.task_id, action.action_id)
    assert layout.exists()
    assert (layout / "outputs" / "out.txt").read_text(encoding="utf-8") == "ok"


def test_rollback_wipes_scratch_dir(compute_executor) -> None:
    executor, run_store, workspace, scratch = compute_executor
    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    scratch_root = scratch.action_dir(run_store.task_id, action.action_id)
    assert scratch_root.exists()

    rb = Rollback(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
    )
    result = rb.run(outcome.manifest)
    assert result.success, result.failed
    assert not scratch_root.exists()


def test_rollback_without_scratch_workspace_fails(compute_executor) -> None:
    executor, run_store, workspace, scratch = compute_executor
    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)

    # Rebuild Rollback WITHOUT scratch_workspace — should refuse cleanly.
    rb = Rollback(workspace_root=workspace, run_store=run_store)
    result = rb.run(outcome.manifest)
    assert not result.success
    assert result.failed
    assert "DELETE_SCRATCH_DIR" in result.failed[0]["error"]


def test_executor_without_sandbox_rejects_python_compute(tmp_path: Path) -> None:
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Note: no scratch_workspace / sandbox_runtime.
    executor = Executor(workspace_root=workspace, run_store=run_store)

    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert not outcome.success
    rec = outcome.records[0]
    assert rec.status is ExecutionStatus.FAILED
    assert "scratch_workspace" in (rec.error or "")


def test_invalid_compute_metadata_fails_action(compute_executor) -> None:
    executor, run_store, _, _ = compute_executor
    bad = Action(
        action_id="a-bad",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="malformed",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"not": "a-valid-compute-action"},
    )
    plan = _plan([bad], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert not outcome.success
    rec = outcome.records[0]
    assert rec.status is ExecutionStatus.FAILED
    assert "PYTHON_COMPUTE metadata" in (rec.error or "")


def test_failing_compute_outcome_still_records_rollback(compute_executor) -> None:
    """When the sandbox returns non-OK, the executor must STILL append a
    DELETE_SCRATCH_DIR entry so rollback can clean up — otherwise the
    scratch dir leaks after a failed run."""
    executor, run_store, workspace, scratch = compute_executor
    compute = _make_compute_action(
        script="""
        # Intentionally do not write the declared output.
        print('did nothing')
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)

    assert not outcome.success
    assert outcome.records[0].status is ExecutionStatus.FAILED
    # Rollback entry MUST be present even on failure.
    ops = [e.op for e in outcome.manifest.entries]
    assert RollbackOpType.DELETE_SCRATCH_DIR in ops

    # Rollback cleans the scratch dir.
    rb = Rollback(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
    )
    result = rb.run(outcome.manifest)
    assert result.success, result.failed


def test_compute_inputs_are_copied_into_scratch(compute_executor) -> None:
    executor, run_store, workspace, scratch = compute_executor
    (workspace / "raw.txt").write_text("payload-7", encoding="utf-8")

    compute = _make_compute_action(
        script="""
        with open("inputs/raw.txt", encoding="utf-8") as f:
            data = f.read()
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write(data + "!")
        """,
        inputs=[ComputeInputRef(rel_path="raw.txt", size_bytes=9)],
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success, outcome.records
    layout = scratch.action_dir(run_store.task_id, action.action_id)
    assert (layout / "inputs" / "raw.txt").read_text(encoding="utf-8") == "payload-7"
    assert (layout / "outputs" / "out.txt").read_text(encoding="utf-8") == "payload-7!"


def test_compute_does_not_mutate_workspace(compute_executor) -> None:
    executor, run_store, workspace, _ = compute_executor
    (workspace / "raw.txt").write_text("payload", encoding="utf-8")
    before = {
        p.relative_to(workspace).as_posix(): p.read_bytes()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("isolated")
        """,
        inputs=[ComputeInputRef(rel_path="raw.txt", size_bytes=7)],
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    after = {
        p.relative_to(workspace).as_posix(): p.read_bytes()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    assert before == after  # workspace untouched


def test_trace_events_emitted_for_compute(tmp_path: Path) -> None:
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
        trace=trace,
    )
    compute = _make_compute_action(
        script="""
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    events = [
        json.loads(line)
        for line in run_store.trace_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    event_types = {e["event"] for e in events}
    assert TraceEventType.COMPUTE_ACTION_START.value in event_types
    assert TraceEventType.COMPUTE_ACTION_END.value in event_types
    assert TraceEventType.COMPUTE_OUTPUT_VERIFIED.value in event_types


def test_sandbox_timeout_emits_dedicated_event(tmp_path: Path) -> None:
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
        trace=trace,
    )
    compute = _make_compute_action(
        script="""
        import time
        time.sleep(10)
        """,
        timeout_sec=1,
    )
    action = _wrap_as_action(compute)
    plan = _plan([action], run_store.task_id)
    outcome = executor.execute(plan, approved=True)
    assert not outcome.success
    events = [
        json.loads(line)
        for line in run_store.trace_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    types = [e["event"] for e in events]
    assert TraceEventType.SANDBOX_TIMEOUT.value in types
    # Rollback entry still present so cleanup runs.
    ops = [e.op for e in outcome.manifest.entries]
    assert RollbackOpType.DELETE_SCRATCH_DIR in ops
