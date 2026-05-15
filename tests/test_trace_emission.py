"""Phase 9 — kernel emits the right TraceEvents at the right sites.

These tests construct a synthetic plan, drive it through the executor
+ verifier + rollback with a TraceLogger attached, and assert the
resulting event stream contains exactly the events we expect at each
stage. They are the canary that catches accidental removal of an
emission call.

Tests do NOT touch the LLM client (no API key needed). The LLM
emission sites are exercised separately via unit-level inspection
of LLMPlanner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness import control_loop
from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.harness.trace import TraceLogger
from app.schemas import ActionPlan, FailureType, TaskSpec, TraceEventType
from app.schemas.action import Action, ActionType, RiskLevel
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace


def _seed(root: Path) -> None:
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.pdf").write_bytes(b"%PDF-1.4 fake")


def _simple_plan(task_id: str) -> ActionPlan:
    return ActionPlan(
        plan_id="p-trace",
        task_id=task_id,
        summary="trace test",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MKDIR,
                target_path="notes",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            ),
            Action(
                action_id="a-002",
                action_type=ActionType.MOVE,
                source_path="a.txt",
                target_path="notes/a.txt",
                reason="r",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            ),
        ],
    )


# ───────────────────────────────────── back-compat: trace=None is a no-op


def test_executor_with_trace_none_writes_no_trace_file(tmp_path: Path) -> None:
    """Phase 9 invariant: kernel behaviour is unchanged when no trace
    is attached. RunStore creates trace_path but executor without a
    TraceLogger never writes to it."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed(workspace)

    store = RunStore.create(home=tmp_path / "lf")
    task = TaskSpec(
        task_id=store.task_id,
        user_goal="x",
        workspace_root=str(workspace),
        skill="folder_organizer",
        allowed_actions=["mkdir", "move", "index"],
    )
    store.save_task(task)
    plan = _simple_plan(store.task_id)
    store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    # trace_path was never opened — the file does not exist.
    assert not store.trace_path.exists()


# ───────────────────────────────────── full lifecycle: events appear


@pytest.fixture
def run_env(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed(workspace)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)
    task = TaskSpec(
        task_id=store.task_id,
        user_goal="x",
        workspace_root=str(workspace),
        skill="folder_organizer",
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete"],
    )
    store.save_task(task)
    plan = _simple_plan(store.task_id)
    store.save_plan(plan)
    snapshot = scan_workspace(
        workspace,
        task_id=store.task_id,
        compute_hash=False,
        compute_preview=False,
    )
    store.save_workspace(snapshot)
    return {
        "workspace": workspace,
        "store": store,
        "trace": trace,
        "task": task,
        "plan": plan,
        "snapshot": snapshot,
    }


def test_executor_emits_action_start_and_end(run_env) -> None:
    executor = Executor(
        workspace_root=run_env["workspace"], run_store=run_env["store"], trace=run_env["trace"]
    )
    executor.execute(run_env["plan"], approved=True)
    events = run_env["trace"].read_all()
    types = [e.event_type for e in events]
    assert TraceEventType.ACTION_START in types
    assert TraceEventType.ACTION_END in types
    # One start + one end per action (2 actions).
    starts = [e for e in events if e.event_type == TraceEventType.ACTION_START]
    ends = [e for e in events if e.event_type == TraceEventType.ACTION_END]
    assert len(starts) == 2
    assert len(ends) == 2
    # Every ACTION_END has a duration.
    for e in ends:
        assert e.duration_ms is not None and e.duration_ms >= 0


def test_control_loop_emits_dry_run_event(run_env) -> None:
    assessment = control_loop.run_risk_check(
        run_env["task"], run_env["plan"], trace=run_env["trace"]
    )
    control_loop.run_dry_run(
        run_env["task"],
        run_env["plan"],
        assessment,
        run_env["store"],
        trace=run_env["trace"],
    )
    events = run_env["trace"].read_all()
    types = [e.event_type for e in events]
    assert TraceEventType.DRY_RUN_RENDERED in types
    # And a POLICY_CHECK ok event (assessment.passed).
    assert TraceEventType.POLICY_CHECK in types


def test_verifier_emits_check_per_verification_check(run_env) -> None:
    executor = Executor(
        workspace_root=run_env["workspace"], run_store=run_env["store"], trace=run_env["trace"]
    )
    outcome = executor.execute(run_env["plan"], approved=True)
    control_loop.run_verify(
        run_env["task"],
        run_env["plan"],
        run_env["store"],
        outcome,
        run_env["snapshot"],
        trace=run_env["trace"],
    )
    events = run_env["trace"].read_all()
    verifier_events = [e for e in events if e.event_type == TraceEventType.VERIFIER_CHECK]
    # The Phase 9 verifier emits one event per check (6 checks in v0.10.0).
    assert len(verifier_events) >= 4
    # Every check has a name in the detail.
    assert all(":" in e.detail for e in verifier_events)


def test_rollback_emits_entry_per_replayed_op(run_env) -> None:
    executor = Executor(
        workspace_root=run_env["workspace"], run_store=run_env["store"], trace=run_env["trace"]
    )
    outcome = executor.execute(run_env["plan"], approved=True)
    rb = Rollback(
        workspace_root=run_env["workspace"], run_store=run_env["store"], trace=run_env["trace"]
    )
    rb.run(outcome.manifest)
    events = run_env["trace"].read_all()
    rb_events = [e for e in events if e.event_type == TraceEventType.ROLLBACK_ENTRY]
    # The plan had 2 actions; both produced rollback entries; both replay
    # successfully.
    assert len(rb_events) >= 2


# ───────────────────────────────────── policy-blocked plans emit failure_type


def test_policy_check_emits_blocked_with_failure_type(tmp_path: Path) -> None:
    """A plan with a path-traversal action should produce a
    POLICY_CHECK trace event with failure_type=path_forbidden."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)
    task = TaskSpec(
        task_id="t-bad",
        user_goal="x",
        workspace_root=str(workspace),
        skill="folder_organizer",
        allowed_actions=["mkdir", "move"],
        forbidden_paths=["private"],
    )
    bad_plan = ActionPlan(
        plan_id="p-bad",
        task_id="t-bad",
        summary="malicious",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MKDIR,
                target_path="private/secrets",
                reason="bad",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            ),
        ],
    )
    assessment = control_loop.run_risk_check(task, bad_plan, trace=trace)
    assert not assessment.passed

    events = trace.read_all()
    blocked = [
        e for e in events if e.event_type == TraceEventType.POLICY_CHECK and e.status == "blocked"
    ]
    assert blocked
    assert blocked[0].failure_type == FailureType.PATH_FORBIDDEN
