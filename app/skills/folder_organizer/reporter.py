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
    lines.append(f"- Rollback entries recorded: **{len(outcome.manifest.entries)}**")
    lines.append("")

    lines.append("## Verifier verdict")
    lines.append("")
    lines.append(f"**{'PASSED' if verification.passed else 'FAILED'}** — {verification.summary}")
    lines.append("")
    if verification.checks:
        lines.append("| Check | Result | Detail |")
        lines.append("|-------|--------|--------|")
        for c in verification.checks:
            badge = "ok" if c.passed else "fail"
            lines.append(f"| {c.name} | {badge} | {c.detail} |")
        lines.append("")

    if failed:
        lines.append("## Failed actions")
        lines.append("")
        for r in outcome.records:
            if r.status == ExecutionStatus.FAILED:
                lines.append(f"- `{r.action_id}` — {r.error}")
        lines.append("")

    if outcome.manifest.generated_files:
        lines.append("## Generated files")
        for p in outcome.manifest.generated_files:
            lines.append(f"- `{p}`")
        lines.append("")

    if outcome.manifest.created_dirs:
        lines.append("## Created directories")
        for d in outcome.manifest.created_dirs:
            lines.append(f"- `{d}/`")
        lines.append("")

    lines.append("## How to undo")
    lines.append("")
    lines.append(f"```bash\nlocalflow rollback --run-id {outcome.run_id}\n```")
    lines.append("")
    return "\n".join(lines)
