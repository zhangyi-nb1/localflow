"""data_analyzer final_report.md renderer.

The skill's markdown body (the *workspace artifact*) is built inside
the planner — that's where the analysis is run. This file produces the
shorter ``final_report.md`` that lives under ``.localflow/runs/<tid>/``
summarizing the run for the audit trail (per outline §10.6).
"""

from __future__ import annotations

from app.harness.executor import ExecutionOutcome
from app.schemas import ActionPlan, ExecutionStatus, TaskSpec, VerificationResult


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

    lines: list[str] = []
    lines.append(f"# data_analyzer final report — task `{task.task_id}`")
    lines.append("")
    lines.append(f"- **Workspace**: `{task.workspace_root}`")
    lines.append(f"- **Goal**: {task.user_goal}")
    lines.append("")
    lines.append("## Execution")
    lines.append("")
    lines.append(f"- Total actions: **{len(outcome.records)}**")
    lines.append(f"- Succeeded: **{succ}**  ·  Failed: **{fail}**  ·  Skipped: **{skipped}**")
    lines.append(f"- Rollback entries: **{len(outcome.manifest.entries)}**")
    lines.append("")

    lines.append("## Verifier")
    lines.append("")
    badge = "PASSED" if verification.passed else "FAILED"
    lines.append(f"**{badge}** — {verification.summary}")
    lines.append("")

    if outcome.manifest.generated_files:
        lines.append("## Generated artifacts")
        for p in outcome.manifest.generated_files:
            lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## How to undo")
    lines.append("")
    lines.append("```bash")
    lines.append(f"localflow rollback --run-id {outcome.run_id}")
    lines.append("```")

    return "\n".join(lines)
