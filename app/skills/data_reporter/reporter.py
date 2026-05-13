from __future__ import annotations

from app.schemas import ActionPlan, ExecutionStatus, TaskSpec, VerificationResult


def render_data_report(
    *,
    task: TaskSpec,
    plan: ActionPlan,
    outcome,
    verification: VerificationResult,
) -> str:
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)

    lines: list[str] = []
    lines.append(f"# data_reporter report — task `{task.task_id}`")
    lines.append("")
    lines.append(f"- Workspace: `{task.workspace_root}`")
    lines.append(f"- Goal: {task.user_goal}")
    lines.append("")
    lines.append("## Outcome")
    lines.append(f"- Actions: {len(plan.actions)}  ·  succeeded: {success}  ·  failed: {failed}")
    lines.append(
        f"- Verifier: **{'PASSED' if verification.passed else 'FAILED'}** — {verification.summary}"
    )
    lines.append("")

    if plan.actions:
        action = plan.actions[0]
        prov = action.metadata.get("provenance", {}) or {}
        sources = prov.get("sources", []) or []
        ok_sources = [s for s in sources if not s.get("error")]
        bad_sources = [s for s in sources if s.get("error")]
        lines.append("## Sources analyzed")
        lines.append("")
        lines.append(
            f"Scanned {len(sources)} tabular file(s); "
            f"{len(ok_sources)} parsed, {len(bad_sources)} skipped."
        )
        lines.append("")
        for s in ok_sources:
            trunc = " (truncated)" if s.get("truncated") else ""
            lines.append(f"- `{s['path']}` — {s['rows_read']} rows × {s['cols']} cols{trunc}")
        for s in bad_sources:
            lines.append(f"- `{s['path']}` — **error**: {s.get('error')}")
        lines.append("")
        lines.append(f"## Output\n\n- `{action.target_path}` (rollback restores)")
        lines.append("")

    lines.append("## How to undo")
    lines.append("")
    lines.append(f"```bash\nlocalflow rollback --run-id {outcome.run_id}\n```")
    return "\n".join(lines)
