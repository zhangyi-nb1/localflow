from __future__ import annotations

from app.schemas import ActionPlan, ExecutionStatus, TaskSpec, VerificationResult


def render_pdf_index_report(
    *,
    task: TaskSpec,
    plan: ActionPlan,
    outcome,
    verification: VerificationResult,
) -> str:
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)

    lines: list[str] = []
    lines.append(f"# pdf_indexer report — task `{task.task_id}`")
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
        provenance = action.metadata.get("provenance", {}) or {}
        sources = provenance.get("sources", []) or []
        lines.append("## Index sources")
        lines.append("")
        lines.append(f"Synthesized from {len(sources)} source PDF(s):")
        lines.append("")
        for src in sources:
            marker = "preview" if src.get("has_preview") else "filename-only"
            lines.append(f'- `{src["path"]}` — title: "{src["title"]}" ({marker})')
        lines.append("")
        lines.append(f"## Output\n\n- `{action.target_path}` (written; rollback restores)")
        lines.append("")

    lines.append("## How to undo")
    lines.append("")
    lines.append(f"```bash\nlocalflow rollback --run-id {outcome.run_id}\n```")
    return "\n".join(lines)
