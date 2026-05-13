from __future__ import annotations

from pathlib import Path

from app.harness.policy_guard import resolve_inside
from app.schemas import ActionPlan, RiskAssessment
from app.schemas.action import Action, ActionType
from app.tools.file_ops import safe_target


def simulate_action(workspace_root: Path, action: Action) -> dict:
    """Compute what an action *would* do without touching disk."""
    info: dict = {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "reason": action.reason,
        "risk_level": action.risk_level.value,
        "requires_approval": action.requires_approval,
    }
    if action.source_path:
        info["source"] = action.source_path
    if action.target_path:
        info["target"] = action.target_path
        abs_target = resolve_inside(workspace_root, action.target_path)
        if action.action_type in {ActionType.MOVE, ActionType.COPY, ActionType.RENAME}:
            chosen = safe_target(abs_target)
            if chosen != abs_target:
                info["target_conflict"] = True
                info["effective_target"] = chosen.relative_to(workspace_root.resolve()).as_posix()
        elif action.action_type == ActionType.MKDIR:
            info["dir_exists"] = abs_target.exists()
        elif action.action_type == ActionType.INDEX:
            info["index_would_overwrite"] = abs_target.exists()
    return info


def render_dry_run_markdown(
    plan: ActionPlan,
    workspace_root: Path,
    assessment: RiskAssessment,
) -> str:
    """Format a human-readable preview. Pure function — no disk writes."""
    lines: list[str] = []
    lines.append(f"# Dry-run preview — plan {plan.plan_id}")
    lines.append("")
    lines.append(f"- Task: `{plan.task_id}`")
    lines.append(f"- Workspace: `{workspace_root}`")
    lines.append(f"- Risk: **{assessment.risk_level.value}** ({assessment.reason})")
    lines.append(f"- Summary: {plan.summary}")
    lines.append("")

    if assessment.warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in assessment.warnings)
        lines.append("")

    if not plan.actions:
        lines.append("_No actions planned._")
        return "\n".join(lines) + "\n"

    lines.append("## Actions")
    lines.append("")
    lines.append("| # | Type | Source | Target | Risk | Approve? | Reason |")
    lines.append("|---|------|--------|--------|------|----------|--------|")
    for i, action in enumerate(plan.actions, start=1):
        info = simulate_action(workspace_root, action)
        src = info.get("source", "")
        tgt = info.get("effective_target", info.get("target", ""))
        if info.get("target_conflict"):
            tgt += " ⚠ renamed (conflict)"
        if info.get("dir_exists"):
            tgt += " ⚠ already exists"
        approve = "yes" if action.requires_approval else "no"
        reason = (action.reason or "").replace("\n", " ")
        if len(reason) > 60:
            reason = reason[:57] + "..."
        lines.append(
            f"| {i} | {action.action_type.value} | `{src}` | `{tgt}` | {action.risk_level.value} | {approve} | {reason} |"
        )
    lines.append("")

    if plan.expected_outputs:
        lines.append("## Expected outputs")
        lines.extend(f"- {o}" for o in plan.expected_outputs)
        lines.append("")

    lines.append("## Risk summary")
    lines.append(plan.risk_summary or "_n/a_")
    lines.append("")
    return "\n".join(lines)
