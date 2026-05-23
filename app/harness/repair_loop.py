"""Phase 13 — auto-repair loop driven by semantic verifier rejections.

Wraps the existing :mod:`app.harness.control_loop` orchestrators in a
retry-with-repair cycle:

  1. Semantic verifier rejected the latest run → derive a hint from
     the failed verdicts.
  2. Rollback the most recent execution (force=False; user-side drift
     correctly halts the loop).
  3. ``control_loop.run_revise(prior_plan, hint)`` → plan v(N+1).
  4. Re-execute + re-verify + re-semantic-verify.
  5. Repeat until passed or attempts exhausted.

§10.7 invariant: this module is pure orchestration over existing
kernel modules. It does NOT touch ``executor.py`` / ``verifier.py`` /
``rollback.py`` — they keep working unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.harness.audit import AuditLogger
from app.harness.rollback import Rollback
from app.harness.semantic_verifier import SemanticVerifier
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    SemanticVerdict,
    SemanticVerificationResult,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill, SkillError
from app.storage.run_store import RunStore


@dataclass
class RepairAttempt:
    """One iteration of the auto-repair loop — what triggered it,
    what the planner produced, whether it stuck."""

    attempt: int
    grader: str
    suggested_hint: str
    plan_version: int
    structural_passed: bool
    semantic_passed: bool


@dataclass
class RepairOutcome:
    """Aggregate result of run_repair_loop."""

    repaired: bool
    """True iff the final state has semantic_passed=True."""

    attempts: int
    """Number of repair attempts that were executed (0..max_attempts)."""

    final_plan_version: int
    """plan version that the run finished on (1 if no repair fired)."""

    history: list[RepairAttempt] = field(default_factory=list)
    halt_reason: str = ""
    """Reason the loop stopped: 'passed' | 'exhausted' | 'rollback_drift' | ..."""


def run_repair_loop(
    task: TaskSpec,
    *,
    snapshot: WorkspaceSnapshot,
    current_plan: ActionPlan,
    current_outcome,
    current_structural: VerificationResult,
    current_semantic: SemanticVerificationResult,
    skill: Skill,
    run_store: RunStore,
    max_attempts: int,
    trace: TraceLogger | None = None,
    audit: AuditLogger | None = None,
) -> tuple[ActionPlan, "_ExecState", RepairOutcome]:
    """Iterate the repair cycle up to ``max_attempts`` times.

    Returns the FINAL ``(plan, execution_state, repair_outcome)``. The
    execution_state is a small container exposing ``outcome``,
    ``structural``, and ``semantic`` so the caller can write
    artifacts / render reports against the final state without
    knowing how many attempts ran.

    Each repair attempt:
      1. Picks the first failed verdict's ``suggested_hint``.
      2. Rolls back the most recent execution (force=False — drifted
         files halt the loop because the user has obviously touched
         the workspace).
      3. Calls :func:`control_loop.run_revise` to produce plan v(N+1).
      4. Re-runs execute + structural verify + semantic verify.

    Stops on the first attempt that passes semantic verify, or on
    drift/exhaustion. SkillError from a non-LLM skill terminates
    cleanly with halt_reason='not_revisable'.
    """
    # Local import to avoid a control_loop ↔ repair_loop circle.
    from app.harness import control_loop

    state = _ExecState(
        plan=current_plan,
        outcome=current_outcome,
        structural=current_structural,
        semantic=current_semantic,
    )
    outcome = RepairOutcome(
        repaired=current_semantic.passed,
        attempts=0,
        final_plan_version=max(run_store.list_plan_versions() or [1]),
    )
    if current_semantic.passed:
        outcome.halt_reason = "passed"
        return state.plan, state, outcome
    if max_attempts <= 0:
        outcome.halt_reason = "report_only"
        return state.plan, state, outcome
    if not current_semantic.auto_repair_eligible:
        outcome.halt_reason = "no_hint"
        return state.plan, state, outcome

    workspace_root = Path(task.workspace_root)
    rollback = Rollback(workspace_root, run_store, trace=trace)
    semantic_verifier = SemanticVerifier(workspace_root, trace=trace)

    for attempt_idx in range(1, max_attempts + 1):
        first_failed: SemanticVerdict = next(
            v for v in state.semantic.failed_verdicts if v.suggested_hint
        )
        hint = first_failed.suggested_hint or ""

        # v0.22.x — gate the rollback on revisability. Rule-only skills
        # (workspace_visualizer, folder_organizer, …) cannot honour a
        # free-form natural-language hint. Rolling back first and only
        # then discovering "revise rejected" leaves the workspace
        # empty AND offers no repair path — the worst of both worlds
        # observed in run 2026-05-22-085. Halt cleanly with the files
        # still on disk so the structural pass survives and the
        # recipe-level verifier sees the real output.
        if not skill.supports_revise():
            outcome.halt_reason = "not_revisable"
            _journal(
                run_store,
                attempt_idx,
                first_failed,
                hint,
                plan_version=None,
                structural=False,
                semantic=False,
                note=(
                    f"skill {skill.manifest.name!r} is rule-only and cannot "
                    f"honour a natural-language hint — skipping rollback to "
                    f"preserve the workspace state"
                ),
            )
            break

        if trace is not None:
            try:
                trace.emit_repair_triggered(
                    task_id=task.task_id,
                    attempt=attempt_idx,
                    max_attempts=max_attempts,
                    grader=first_failed.grader,
                    suggested_hint=hint,
                )
            except Exception:
                pass

        # 1. Rollback the most recent execution.
        rollback_result = rollback.run(state.outcome.manifest, force=False)
        if rollback_result.conflicts:
            # User-side drift — stop, surface the conflict to the user.
            outcome.halt_reason = "rollback_drift"
            _journal(
                run_store,
                attempt_idx,
                first_failed,
                hint,
                plan_version=None,
                structural=False,
                semantic=False,
                note="rollback drift",
            )
            break
        if not rollback_result.success:
            outcome.halt_reason = "rollback_failed"
            _journal(
                run_store,
                attempt_idx,
                first_failed,
                hint,
                plan_version=None,
                structural=False,
                semantic=False,
                note="rollback failed",
            )
            break

        # 2. Generate plan v(N+1) via the existing Phase 11 plumbing.
        try:
            new_plan, new_version = control_loop.run_revise(
                task,
                snapshot,
                state.plan,
                hint,
                skill=skill,
                run_store=run_store,
                trace=trace,
                audit=audit,
            )
        except SkillError as exc:
            outcome.halt_reason = "not_revisable"
            _journal(
                run_store,
                attempt_idx,
                first_failed,
                hint,
                plan_version=None,
                structural=False,
                semantic=False,
                note=f"revise rejected: {exc}",
            )
            break

        # 3. Re-execute through the standard pipeline.
        try:
            assessment = control_loop.run_risk_check(task, new_plan, trace=trace)
            if not assessment.passed:
                outcome.halt_reason = "policy_blocked_revision"
                _journal(
                    run_store,
                    attempt_idx,
                    first_failed,
                    hint,
                    plan_version=new_version,
                    structural=False,
                    semantic=False,
                    note="policy blocked",
                )
                break
            control_loop.run_dry_run(task, new_plan, assessment, run_store, trace=trace)
            new_outcome = control_loop.run_execute(
                task, new_plan, run_store, approved=True, trace=trace
            )
            new_structural = control_loop.run_verify(
                task, new_plan, run_store, new_outcome, snapshot, trace=trace
            )
        except Exception as exc:  # pragma: no cover
            outcome.halt_reason = f"execute_failed: {type(exc).__name__}"
            _journal(
                run_store,
                attempt_idx,
                first_failed,
                hint,
                plan_version=new_version,
                structural=False,
                semantic=False,
                note=str(exc),
            )
            break

        # 4. Semantic verify the new state.
        new_semantic = semantic_verifier.verify(
            task=task,
            plan=new_plan,
            execution_records=new_outcome.records,
            manifest=new_outcome.manifest,
            snapshot_before=snapshot,
            snapshot_after=None,
            structural=new_structural,
            run_id=new_outcome.run_id,
        )
        _save_semantic(run_store, new_semantic)

        # 5. Update state + journal + decide whether to continue.
        state = _ExecState(
            plan=new_plan,
            outcome=new_outcome,
            structural=new_structural,
            semantic=new_semantic,
        )
        outcome.attempts = attempt_idx
        outcome.final_plan_version = new_version
        outcome.history.append(
            RepairAttempt(
                attempt=attempt_idx,
                grader=first_failed.grader,
                suggested_hint=hint,
                plan_version=new_version,
                structural_passed=new_structural.passed,
                semantic_passed=new_semantic.passed,
            )
        )
        _journal(
            run_store,
            attempt_idx,
            first_failed,
            hint,
            plan_version=new_version,
            structural=new_structural.passed,
            semantic=new_semantic.passed,
            note="",
        )
        if audit is not None:
            try:
                audit.log(
                    "repair.attempt",
                    task_id=task.task_id,
                    attempt=attempt_idx,
                    grader=first_failed.grader,
                    plan_version=new_version,
                    structural_passed=new_structural.passed,
                    semantic_passed=new_semantic.passed,
                )
            except Exception:
                pass

        if new_semantic.passed:
            outcome.repaired = True
            outcome.halt_reason = "passed"
            return state.plan, state, outcome
        if not new_semantic.auto_repair_eligible:
            outcome.halt_reason = "no_hint"
            break

    if outcome.attempts >= max_attempts and not outcome.repaired:
        outcome.halt_reason = outcome.halt_reason or "exhausted"
    return state.plan, state, outcome


# ──────────────────────────────────── helpers


@dataclass
class _ExecState:
    """Snapshot of plan + outcomes from one iteration of the loop."""

    plan: ActionPlan
    outcome: object  # ExecutionOutcome — late-bound to avoid an import
    structural: VerificationResult
    semantic: SemanticVerificationResult


def _journal(
    run_store: RunStore,
    attempt: int,
    verdict: SemanticVerdict,
    hint: str,
    *,
    plan_version: int | None,
    structural: bool,
    semantic: bool,
    note: str,
) -> None:
    """Append one row to ``<run_dir>/repairs.jsonl``. Parallel to Phase 11's
    revisions.jsonl — revisions is user-driven, repairs is harness-driven."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempt": attempt,
        "grader": verdict.grader,
        "suggested_hint": hint,
        "plan_version": plan_version,
        "structural_passed": structural,
        "semantic_passed": semantic,
        "note": note,
    }
    with run_store.repairs_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _save_semantic(run_store: RunStore, result: SemanticVerificationResult) -> None:
    """Persist the latest semantic verification snapshot. Overwritten
    on every attempt so the file always reflects the final state the
    caller will surface to the user."""
    run_store.write_model(run_store.semantic_verify_path, result)
