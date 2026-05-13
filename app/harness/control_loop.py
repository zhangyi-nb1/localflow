from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.harness.action_validator import validate_plan_structure
from app.harness.approval import ApprovalDecision, ask_approval
from app.harness.audit import AuditLogger
from app.harness.dry_run import render_dry_run_markdown
from app.harness.executor import Executor, ExecutionOutcome
from app.harness.policy_guard import assess_plan
from app.harness.verifier import Verifier
from app.schemas import (
    ActionPlan,
    ExecutionStatus,
    RiskAssessment,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace


@dataclass
class PhaseResult:
    name: str
    ok: bool
    detail: str = ""


def run_inspect(
    workspace_root: Path,
    task_id: str,
    *,
    compute_hash: bool = True,
    compute_preview: bool = True,
) -> WorkspaceSnapshot:
    return scan_workspace(
        workspace_root,
        task_id,
        compute_hash=compute_hash,
        compute_preview=compute_preview,
    )


def run_risk_check(
    task: TaskSpec, plan: ActionPlan
) -> RiskAssessment:
    validate_plan_structure(plan)
    return assess_plan(
        Path(task.workspace_root),
        plan,
        forbidden_actions=tuple(task.forbidden_actions),
        forbidden_paths=tuple(task.forbidden_paths),
    )


def run_dry_run(
    task: TaskSpec,
    plan: ActionPlan,
    assessment: RiskAssessment,
    run_store: RunStore,
) -> str:
    md = render_dry_run_markdown(plan, Path(task.workspace_root), assessment)
    run_store.write_text(run_store.dry_run_path, md)
    AuditLogger(run_store.audit_log_path).log(
        "dry_run.rendered",
        plan_id=plan.plan_id,
        action_count=len(plan.actions),
        risk=assessment.risk_level.value,
    )
    return md


def run_approval(
    plan: ActionPlan,
    assessment: RiskAssessment,
    *,
    auto_approve: bool = False,
) -> ApprovalDecision:
    write_count = sum(1 for a in plan.actions if a.is_write())
    return ask_approval(
        risk_level=assessment.risk_level.value,
        write_action_count=write_count,
        auto_approve=auto_approve,
    )


def run_execute(
    task: TaskSpec,
    plan: ActionPlan,
    run_store: RunStore,
    *,
    approved: bool,
    resume: bool = False,
) -> ExecutionOutcome:
    executor = Executor(
        workspace_root=Path(task.workspace_root),
        run_store=run_store,
        forbidden_actions=tuple(task.forbidden_actions),
        forbidden_paths=tuple(task.forbidden_paths),
    )
    return executor.execute(plan, approved=approved, resume=resume)


def run_verify(
    task: TaskSpec,
    plan: ActionPlan,
    run_store: RunStore,
    outcome: ExecutionOutcome,
    snapshot: WorkspaceSnapshot,
) -> VerificationResult:
    verifier = Verifier(workspace_root=Path(task.workspace_root))
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    skipped = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SKIPPED}
    failed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.FAILED}
    result = verifier.verify(
        task_id=task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids=executed,
        skipped_action_ids=skipped,
        failed_action_ids=failed,
        original_snapshot=snapshot,
    )
    run_store.save_verification(result)
    return result
