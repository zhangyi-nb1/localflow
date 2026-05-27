from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.approval import ApprovalDecision
    from app.schemas import ConfirmationPolicy, ReactConfig
    from app.schemas.action import Action
    from app.tools.workspace import Workspace
    from localflow_kernel.llm import LLMClient

from app.harness.action_validator import validate_plan_structure
from app.harness.approval import ApprovalDecision, ask_approval
from app.harness.audit import AuditLogger
from app.harness.dry_run import render_dry_run_markdown
from app.harness.executor import ExecutionOutcome, Executor
from app.harness.policy_guard import assess_plan
from app.harness.trace import TraceLogger
from app.harness.verifier import Verifier
from app.schemas import (
    ActionPlan,
    ExecutionStatus,
    FailureType,
    RiskAssessment,
    TaskSpec,
    TraceEvent,
    TraceEventType,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill, SkillError
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace

# Phase 11 — hard cap on plan refinement iterations per task. After 5
# revisions the user is better off restarting with a clearer initial
# goal than continuing to chase the LLM. Kept here (not in Skill /
# RunStore) so the cap stays a harness-level invariant.
MAX_REVISIONS = 5


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
    task: TaskSpec,
    plan: ActionPlan,
    *,
    trace: TraceLogger | None = None,
) -> RiskAssessment:
    validate_plan_structure(plan)
    # v0.16 — pull fetch_allowed_domains from memory prefs (read-only;
    # never mutates). FETCH actions whose host isn't on the allowlist
    # are blocked by policy_guard before they reach the executor.
    try:
        from app.memory import MemoryStore

        fetch_allowed = tuple(MemoryStore().load().fetch_allowed_domains)
    except Exception:
        fetch_allowed = ()
    assessment = assess_plan(
        Path(task.workspace_root),
        plan,
        forbidden_actions=tuple(task.forbidden_actions),
        forbidden_paths=tuple(task.forbidden_paths),
        fetch_allowed_domains=fetch_allowed,
    )
    if trace is not None:
        # Emit one POLICY_CHECK event per blocked action so eval graders
        # can grep for failure_type=path_forbidden / policy_blocked.
        _emit_policy_trace(trace, task, plan, assessment)
    return assessment


def run_dry_run(
    task: TaskSpec,
    plan: ActionPlan,
    assessment: RiskAssessment,
    run_store: RunStore,
    *,
    trace: TraceLogger | None = None,
) -> str:
    md = render_dry_run_markdown(plan, Path(task.workspace_root), assessment)
    run_store.write_text(run_store.dry_run_path, md)
    AuditLogger(run_store.audit_log_path).log(
        "dry_run.rendered",
        plan_id=plan.plan_id,
        action_count=len(plan.actions),
        risk=assessment.risk_level.value,
    )
    if trace is not None:
        try:
            trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    run_id=run_store.task_id,
                    event_type=TraceEventType.DRY_RUN_RENDERED,
                    status="ok",
                    detail=f"{len(plan.actions)} actions, risk={assessment.risk_level.value}",
                    payload={
                        "plan_id": plan.plan_id,
                        "action_count": len(plan.actions),
                        "risk": assessment.risk_level.value,
                    },
                )
            )
        except Exception:
            pass
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
    trace: TraceLogger | None = None,
    react_mode: bool = False,
    react_config: "ReactConfig | None" = None,
    llm_client: "LLMClient | None" = None,
    confirmation_policy: "ConfirmationPolicy | None" = None,
    action_approver: "Callable[[Action], ApprovalDecision] | None" = None,
    workspace: "Workspace | None" = None,
) -> ExecutionOutcome:
    """Phase 26.2 — react_mode passthrough. Phase 27.1 — also threads
    a per-action ConfirmationPolicy + approver callback. Phase 29.2 —
    optional ``workspace=`` injection (default = LocalWorkspace on
    task.workspace_root). All None = v0.25.x batch behaviour."""
    executor = Executor(
        workspace_root=Path(task.workspace_root),
        run_store=run_store,
        forbidden_actions=tuple(task.forbidden_actions),
        forbidden_paths=tuple(task.forbidden_paths),
        trace=trace,
        workspace=workspace,
    )
    return executor.execute(
        plan,
        approved=approved,
        resume=resume,
        react_mode=react_mode,
        react_config=react_config,
        llm_client=llm_client,
        confirmation_policy=confirmation_policy,
        action_approver=action_approver,
    )


def run_verify(
    task: TaskSpec,
    plan: ActionPlan,
    run_store: RunStore,
    outcome: ExecutionOutcome,
    snapshot: WorkspaceSnapshot,
    *,
    trace: TraceLogger | None = None,
) -> VerificationResult:
    verifier = Verifier(workspace_root=Path(task.workspace_root), trace=trace)
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


# -- Phase 11 plan refinement loop ---------------------------------------


def run_revise(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    prior_plan: ActionPlan,
    user_hint: str,
    *,
    skill: Skill,
    run_store: RunStore,
    trace: TraceLogger | None = None,
    audit: AuditLogger | None = None,
) -> tuple[ActionPlan, int]:
    """Drive one refinement turn over an existing task.

    Walks the skill's :meth:`Skill.revise` (default delegates to
    ``plan_with_llm`` with the prior plan + user hint as a synthetic
    "your previous plan was wrong because…" repair turn), validates
    the new plan, persists ``plans/plan_v<n>.json`` (mirroring to
    ``plan.json``), appends a row to ``revisions.jsonl``, and emits
    one ``plan.revised`` trace event.

    Caps revisions at :data:`MAX_REVISIONS` — beyond that we surface
    a clear "restart with a better initial goal" message rather than
    letting the user burn LLM budget chasing a brittle prompt.

    Returns ``(new_plan, new_version)``. Raises :class:`SkillError`
    when the cap is hit or the skill rejects refinement (rule-only
    skills can't honour free-form hints).

    Critically: nothing on disk besides ``plans/`` + ``plan.json`` +
    ``revisions.jsonl`` is touched. The executor / verifier / rollback
    code paths are unaware that refinement happened. §10.7 holds.
    """
    if not user_hint or not user_hint.strip():
        raise SkillError("user_hint is required for revise — empty hint rejected")

    versions = run_store.list_plan_versions()
    if not versions:
        # Backfill v1 from the existing plan.json so the audit trail is
        # complete. Tasks created before v0.12 had no plans/ subdir.
        run_store.save_plan_version(prior_plan, 1)
        versions = [1]
    next_version = max(versions) + 1
    if next_version > MAX_REVISIONS:
        raise SkillError(
            f"plan already revised {MAX_REVISIONS} times — consider restarting "
            f"with a clearer initial goal"
        )

    new_plan = skill.revise(task, snapshot, prior_plan, user_hint.strip(), trace=trace)
    skill.validate(new_plan)
    run_store.save_plan_version(new_plan, next_version)
    _append_revision_log(run_store, next_version, user_hint.strip(), prior_plan, new_plan)

    if trace is not None:
        try:
            trace.emit_plan_revised(
                task_id=task.task_id,
                prior_plan_id=prior_plan.plan_id,
                new_plan_id=new_plan.plan_id,
                version=next_version,
                user_hint=user_hint.strip(),
            )
        except Exception:
            pass
    if audit is not None:
        try:
            audit.log(
                "plan.revised",
                task_id=task.task_id,
                version=next_version,
                prior_plan_id=prior_plan.plan_id,
                new_plan_id=new_plan.plan_id,
                hint=user_hint.strip()[:300],
            )
        except Exception:
            pass

    return new_plan, next_version


def _append_revision_log(
    run_store: RunStore,
    version: int,
    user_hint: str,
    prior_plan: ActionPlan,
    new_plan: ActionPlan,
) -> None:
    """Append one row to ``<run_dir>/revisions.jsonl``.

    Hand-rolled (not via :class:`AuditLogger` which is for cross-task
    state mutations) so the file lives next to the rest of this run's
    artifacts and travels in the run dir's tarball.
    """
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": version,
        "prior_plan_id": prior_plan.plan_id,
        "new_plan_id": new_plan.plan_id,
        "user_hint": user_hint,
        "prior_action_count": len(prior_plan.actions),
        "new_action_count": len(new_plan.actions),
    }
    line = json.dumps(row, ensure_ascii=False)
    with run_store.revisions_log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# -- Phase 13 composite: execute + verify + (optional) semantic + repair --


def run_with_auto_repair(
    task: TaskSpec,
    plan: ActionPlan,
    snapshot: WorkspaceSnapshot,
    *,
    skill: Skill,
    run_store: RunStore,
    approved: bool,
    enable_semantic: bool,
    max_auto_repairs: int,
    resume: bool = False,
    trace: TraceLogger | None = None,
    audit: AuditLogger | None = None,
):
    """Composite orchestrator that runs the full execute → verify →
    (optional) semantic verify → (optional) auto-repair pipeline.

    Returns a tuple ``(plan, outcome, verification, semantic_or_none,
    repair_or_none)``. The first value is the *final* plan (which may
    differ from the input plan after one or more repair iterations).

    When ``enable_semantic=False``, this collapses to the existing
    execute + verify path — no semantic verification, no repair —
    so existing v0.11 / v0.12 callers see no behaviour change when
    they wire through this helper.
    """
    # Local imports — defer the runtime layer until callers actually
    # opt in, and avoid a control_loop ↔ semantic_verifier ↔ control_loop
    # import cycle.
    from app.harness.repair_loop import run_repair_loop
    from app.harness.semantic_verifier import SemanticVerifier

    outcome = run_execute(task, plan, run_store, approved=approved, resume=resume, trace=trace)
    verification = run_verify(task, plan, run_store, outcome, snapshot, trace=trace)

    if not enable_semantic:
        return plan, outcome, verification, None, None

    if not verification.passed:
        # Don't bother running semantic verifier on a structurally
        # failed run — the user will already see the structural fail.
        return plan, outcome, verification, None, None

    semantic_verifier = SemanticVerifier(Path(task.workspace_root), trace=trace)
    semantic = semantic_verifier.verify(
        task=task,
        plan=plan,
        execution_records=outcome.records,
        manifest=outcome.manifest,
        snapshot_before=snapshot,
        snapshot_after=None,
        structural=verification,
        run_id=outcome.run_id,
    )
    run_store.write_model(run_store.semantic_verify_path, semantic)

    if semantic.passed or not semantic.auto_repair_eligible or max_auto_repairs <= 0:
        return plan, outcome, verification, semantic, None

    final_plan, state, repair_outcome = run_repair_loop(
        task,
        snapshot=snapshot,
        current_plan=plan,
        current_outcome=outcome,
        current_structural=verification,
        current_semantic=semantic,
        skill=skill,
        run_store=run_store,
        max_attempts=max_auto_repairs,
        trace=trace,
        audit=audit,
    )
    return final_plan, state.outcome, state.structural, state.semantic, repair_outcome


# -- Phase 9 trace emission helpers (no-op when trace is None) -----------


def _emit_policy_trace(
    trace: TraceLogger,
    task: TaskSpec,
    plan: ActionPlan,
    assessment: RiskAssessment,
) -> None:
    """Emit one POLICY_CHECK event per blocked action so eval graders
    have per-action visibility into what failed and why. Emits a single
    aggregate event when the plan passed (status=ok)."""
    try:
        if assessment.passed:
            trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    event_type=TraceEventType.POLICY_CHECK,
                    status="ok",
                    detail=f"{len(plan.actions)} actions passed policy guard",
                )
            )
            return
        blocked = set(assessment.blocked_actions)
        for action in plan.actions:
            if action.action_id not in blocked:
                continue
            # Failure-type classification — same heuristic as Executor's
            # _classify_policy_reason; the two stay in sync because both
            # consume the same warning strings.
            joined = " ".join(assessment.warnings).lower()
            ftype = (
                FailureType.PATH_FORBIDDEN
                if "forbidden" in joined and "path" in joined
                else FailureType.POLICY_BLOCKED
            )
            trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    event_type=TraceEventType.POLICY_CHECK,
                    status="blocked",
                    failure_type=ftype,
                    action_id=action.action_id,
                    detail=f"{action.action_type.value} {action.target_path or ''}",
                    payload={"warnings": list(assessment.warnings)},
                )
            )
    except Exception:
        pass
