"""data_analyzer final_report.md renderer.

The skill's markdown body (the *workspace artifact*) is built inside
the planner — that's where the analysis is run. This file produces the
shorter ``final_report.md`` that lives under ``.localflow/runs/<tid>/``
summarizing the run for the audit trail (per outline §10.6).

v0.22: renders via ``app/templates/reports/data_analyzer.md.j2`` so
the audit-trail summary respects ``task.locale``.
"""

from __future__ import annotations

from app.harness.executor import ExecutionOutcome
from app.schemas import ActionPlan, ExecutionStatus, TaskSpec, VerificationResult
from app.templates import render_report


def render_final_report(
    *,
    task: TaskSpec,
    plan: ActionPlan,
    outcome: ExecutionOutcome,
    verification: VerificationResult,
) -> str:
    succ = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    fail = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
    skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

    ctx = {
        "task_id": task.task_id,
        "workspace_root": task.workspace_root,
        "user_goal": task.user_goal,
        "total_actions": len(outcome.records),
        "succeeded": succ,
        "failed": fail,
        "skipped": skipped,
        "rollback_entries": len(outcome.manifest.entries),
        "verifier_passed": verification.passed,
        "verifier_summary": verification.summary,
        "generated_files": list(outcome.manifest.generated_files),
        "run_id": outcome.run_id,
    }
    return render_report("data_analyzer", locale=task.locale, ctx=ctx)
