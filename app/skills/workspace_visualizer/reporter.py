"""workspace_visualizer final_report.md renderer.

v0.22: the markdown body is rendered from
``app/templates/reports/workspace_visualizer.md.j2`` using
``task.locale`` for bilingual output. Mirrors folder_organizer's
template shape (header + verifier verdict + expected outputs).
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
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
    skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

    ctx = {
        "task_id": task.task_id,
        "skill": task.skill,
        "workspace_root": task.workspace_root,
        "user_goal": task.user_goal,
        "total_actions": len(outcome.records),
        "succeeded": success,
        "failed": failed,
        "skipped": skipped,
        "verifier_passed": verification.passed,
        "verifier_summary": verification.summary,
        "expected_outputs": list(plan.expected_outputs or []),
    }
    return render_report("workspace_visualizer", locale=task.locale, ctx=ctx)
