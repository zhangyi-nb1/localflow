"""folder_organizer final_report.md renderer.

v0.22: the markdown body is rendered from
``app/templates/reports/folder_organizer.md.j2`` using the locale on
``task.locale`` (zh-CN default, en-US optional). Keeps the historical
public signature so existing callers (cli.py, contract tests) keep
working.
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

    failed_actions = [
        {"action_id": r.action_id, "error": r.error}
        for r in outcome.records
        if r.status == ExecutionStatus.FAILED
    ]

    ctx = {
        "task_id": task.task_id,
        "skill": task.skill,
        "workspace_root": task.workspace_root,
        "user_goal": task.user_goal,
        "total_actions": len(outcome.records),
        "succeeded": success,
        "failed": failed,
        "skipped": skipped,
        "rollback_entries": len(outcome.manifest.entries),
        "verifier_passed": verification.passed,
        "verifier_summary": verification.summary,
        "verifier_checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in (verification.checks or [])
        ],
        "failed_actions": failed_actions,
        "generated_files": list(outcome.manifest.generated_files),
        "created_dirs": list(outcome.manifest.created_dirs),
        "run_id": outcome.run_id,
    }
    return render_report("folder_organizer", locale=task.locale, ctx=ctx)
