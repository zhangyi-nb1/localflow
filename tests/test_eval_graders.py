"""Phase 9 — structural-grader unit tests.

Construct synthetic :class:`GraderContext` instances (no eval runner
involved) and assert each grader's verdict given known inputs. Keeps
graders fast + deterministic — the runner integration test exercises
the full path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.eval.graders import get as get_grader
from app.eval.schema import EvalTask, GraderContext, WorkspaceFile
from app.schemas import (
    ActionPlan,
    ExecutionRecord,
    ExecutionStatus,
    FailureType,
    RollbackManifest,
    TaskSpec,
    TraceEvent,
    TraceEventType,
    WorkspaceSnapshot,
)
from app.schemas.action import Action, ActionType, RiskLevel


def _ctx(
    *,
    tmp_path: Path,
    task: EvalTask,
    plan: ActionPlan,
    records: list[ExecutionRecord] | None = None,
    trace: list[TraceEvent] | None = None,
    seed_hashes: dict[str, str] | None = None,
) -> GraderContext:
    snap = WorkspaceSnapshot(snapshot_id="s", task_id="t", root=str(tmp_path), files=[])
    return GraderContext(
        task=task,
        task_spec=TaskSpec(
            task_id="t",
            user_goal="g",
            workspace_root=str(tmp_path),
            skill="folder_organizer",
            allowed_actions=["mkdir", "move", "index"],
        ),
        plan=plan,
        snapshot_before=snap,
        snapshot_after=None,
        execution_records=records or [],
        manifest=RollbackManifest(run_id="r", task_id="t"),
        verification=None,
        trace_events=trace or [],
        workspace_path=tmp_path,
        seed_hashes=seed_hashes or {},
    )


# ───────────────────────────────────── expected_outputs_present


def test_expected_outputs_present_all_there(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"x")
    (tmp_path / "papers" / "index.md").write_text("hi", encoding="utf-8")

    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        expected_outputs=["papers/a.pdf", "papers/index.md"],
        graders=["expected_outputs_present"],
    )
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=ActionPlan(plan_id="p", task_id="t", summary="x"))
    v = get_grader("expected_outputs_present")(ctx)
    assert v.passed
    assert "2/2 present" in v.detail


def test_expected_outputs_present_missing_one(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"x")

    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        expected_outputs=["papers/a.pdf", "papers/missing.md"],
        graders=["expected_outputs_present"],
    )
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=ActionPlan(plan_id="p", task_id="t", summary="x"))
    v = get_grader("expected_outputs_present")(ctx)
    assert not v.passed
    assert "missing" in v.detail


# ───────────────────────────────────── all_files_accounted_for


def test_all_files_accounted_when_moved(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"x")

    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        workspace_seed=[WorkspaceFile(path="a.pdf", text="x")],
        graders=["all_files_accounted_for"],
    )
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MOVE,
                source_path="a.pdf",
                target_path="papers/a.pdf",
                reason="r",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            )
        ],
    )
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=plan)
    v = get_grader("all_files_accounted_for")(ctx)
    assert v.passed


def test_all_files_accounted_detects_loss(tmp_path: Path) -> None:
    """Seeded file is gone from disk AND no move action references it
    → grader fails."""
    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        workspace_seed=[WorkspaceFile(path="a.pdf", text="x")],
        graders=["all_files_accounted_for"],
    )
    plan = ActionPlan(plan_id="p", task_id="t", summary="empty")
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=plan)
    v = get_grader("all_files_accounted_for")(ctx)
    assert not v.passed
    assert "disappeared" in v.detail


# ───────────────────────────────────── safety_no_forbidden_path


def test_safety_passes_when_no_forbidden_paths(tmp_path: Path) -> None:
    task = EvalTask(task_id="t", title="t", goal="g", graders=["safety_no_forbidden_path"])
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=ActionPlan(plan_id="p", task_id="t", summary="x"))
    v = get_grader("safety_no_forbidden_path")(ctx)
    assert v.passed


def test_safety_records_blocked_attempts_as_pass(tmp_path: Path) -> None:
    """Forbidden path attempted but BLOCKED by policy_guard → grader
    passes (kernel did its job)."""
    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        forbidden_paths=["private"],
        graders=["safety_no_forbidden_path"],
    )
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MOVE,
                source_path="private/secrets.txt",
                target_path="notes/secrets.txt",
                reason="r",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            )
        ],
    )
    # The action was BLOCKED — no success record.
    records = [
        ExecutionRecord(
            run_id="r",
            action_id="a-001",
            status=ExecutionStatus.FAILED,
            error="policy_violation: forbidden_paths",
        )
    ]
    trace = [
        TraceEvent(
            task_id="t",
            event_type=TraceEventType.POLICY_CHECK,
            status="blocked",
            failure_type=FailureType.PATH_FORBIDDEN,
            action_id="a-001",
        )
    ]
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=plan, records=records, trace=trace)
    v = get_grader("safety_no_forbidden_path")(ctx)
    assert v.passed
    assert "blocked" in v.detail


def test_safety_fails_if_forbidden_action_succeeded(tmp_path: Path) -> None:
    """If somehow an action targeting a forbidden path succeeded, the
    grader catches the kernel bug."""
    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        forbidden_paths=["private"],
        graders=["safety_no_forbidden_path"],
    )
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MOVE,
                source_path="private/secrets.txt",
                target_path="notes/secrets.txt",
                reason="r",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            )
        ],
    )
    records = [ExecutionRecord(run_id="r", action_id="a-001", status=ExecutionStatus.SUCCESS)]
    ctx = _ctx(tmp_path=tmp_path, task=task, plan=plan, records=records)
    v = get_grader("safety_no_forbidden_path")(ctx)
    assert not v.passed


# ───────────────────────────────────── rollback_restores


def test_rollback_restores_passes_when_hashes_match(tmp_path: Path) -> None:
    content = b"alpha"
    (tmp_path / "a.pdf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        workspace_seed=[WorkspaceFile(path="a.pdf", text="x")],
        graders=["rollback_restores"],
    )
    ctx = _ctx(
        tmp_path=tmp_path,
        task=task,
        plan=ActionPlan(plan_id="p", task_id="t", summary="x"),
        seed_hashes={"a.pdf": digest},
    )
    v = get_grader("rollback_restores")(ctx)
    assert v.passed


def test_rollback_restores_fails_on_drift(tmp_path: Path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"NEW_CONTENT")
    original = hashlib.sha256(b"alpha").hexdigest()

    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        workspace_seed=[WorkspaceFile(path="a.pdf", text="x")],
        graders=["rollback_restores"],
    )
    ctx = _ctx(
        tmp_path=tmp_path,
        task=task,
        plan=ActionPlan(plan_id="p", task_id="t", summary="x"),
        seed_hashes={"a.pdf": original},
    )
    v = get_grader("rollback_restores")(ctx)
    assert not v.passed
    assert "drifted" in v.detail


def test_rollback_restores_fails_when_seed_missing(tmp_path: Path) -> None:
    task = EvalTask(
        task_id="t",
        title="t",
        goal="g",
        workspace_seed=[WorkspaceFile(path="a.pdf", text="x")],
        graders=["rollback_restores"],
    )
    ctx = _ctx(
        tmp_path=tmp_path,
        task=task,
        plan=ActionPlan(plan_id="p", task_id="t", summary="x"),
        seed_hashes={"a.pdf": "0" * 64},
    )
    v = get_grader("rollback_restores")(ctx)
    assert not v.passed
    assert "missing" in v.detail
