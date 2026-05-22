"""data_reporter final_report.md renderer.

v0.22: rendered via ``app/templates/reports/data_reporter.md.j2`` so
the "Sources analyzed" + "Outcome" sections honour ``task.locale``.
"""

from __future__ import annotations

from app.schemas import ActionPlan, ExecutionStatus, TaskSpec, VerificationResult
from app.templates import render_report


def render_data_report(
    *,
    task: TaskSpec,
    plan: ActionPlan,
    outcome,
    verification: VerificationResult,
) -> str:
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)

    first_action = None
    sources: list[dict] = []
    if plan.actions:
        first_action = plan.actions[0]
        prov = first_action.metadata.get("provenance", {}) or {}
        sources = prov.get("sources", []) or []
    ok_sources = [s for s in sources if not s.get("error")]
    bad_sources = [s for s in sources if s.get("error")]

    ctx = {
        "task_id": task.task_id,
        "workspace_root": task.workspace_root,
        "user_goal": task.user_goal,
        "total_actions": len(plan.actions),
        "succeeded": success,
        "failed": failed,
        "verifier_passed": verification.passed,
        "verifier_summary": verification.summary,
        "first_action": first_action is not None,
        "first_action_target": first_action.target_path if first_action else "",
        "sources_total": len(sources),
        "sources_ok": len(ok_sources),
        "sources_bad": len(bad_sources),
        "ok_sources": ok_sources,
        "bad_sources": bad_sources,
        "run_id": outcome.run_id,
    }
    return render_report("data_reporter", locale=task.locale, ctx=ctx)
