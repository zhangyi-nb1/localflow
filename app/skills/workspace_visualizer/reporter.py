"""workspace_visualizer final_report.md renderer.

Mirrors the structure of folder_organizer / data_reporter — a fixed
header, the verifier verdict, and a compact action table.
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
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
    skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

    lines: list[str] = []
    lines.append(f"# Final report — task `{task.task_id}`")
    lines.append("")
    lines.append(f"- Skill: `{task.skill}`")
    lines.append(f"- Workspace: `{task.workspace_root}`")
    lines.append(f"- Goal: {task.user_goal}")
    lines.append("")
    lines.append("## Execution summary")
    lines.append("")
    lines.append(f"- Total actions: **{len(outcome.records)}**")
    lines.append(f"- Succeeded: **{success}**")
    lines.append(f"- Failed: **{failed}**")
    lines.append(f"- Skipped (checkpoint): **{skipped}**")
    lines.append("")
    lines.append("## Verifier verdict")
    lines.append("")
    lines.append(f"**{'PASSED' if verification.passed else 'FAILED'}** — {verification.summary}")
    lines.append("")
    if plan.expected_outputs:
        lines.append("## Outputs")
        lines.append("")
        for out in plan.expected_outputs:
            lines.append(f"- `{out}`")
        lines.append("")
    return "\n".join(lines)
