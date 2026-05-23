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
    # v0.23 — PYTHON_COMPUTE preview: lift the script summary +
    # input/output counts up to the row so reviewers can judge the
    # action without expanding the full script. Full script lives in
    # the "Compute scripts" section below the actions table.
    if action.action_type == ActionType.PYTHON_COMPUTE:
        from app.schemas.compute import ComputeAction

        try:
            compute = ComputeAction.model_validate(action.metadata or {})
        except Exception as exc:
            info["compute_error"] = f"invalid ComputeAction metadata: {exc}"
        else:
            info["compute_summary"] = compute.script_summary
            info["compute_inputs"] = [r.rel_path for r in compute.inputs]
            info["compute_outputs"] = [spec.relative_path for spec in compute.expected_outputs]
            info["compute_timeout_sec"] = compute.sandbox_policy.timeout_sec
            info["compute_script"] = compute.script
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
    # Collect compute action details for the dedicated section below.
    compute_rows: list[tuple[int, Action, dict]] = []
    for i, action in enumerate(plan.actions, start=1):
        info = simulate_action(workspace_root, action)
        src = info.get("source", "")
        tgt = info.get("effective_target", info.get("target", ""))
        if info.get("target_conflict"):
            tgt += " ⚠ renamed (conflict)"
        if info.get("dir_exists"):
            tgt += " ⚠ already exists"
        # v0.23 — PYTHON_COMPUTE shows its script_summary in the
        # reason column so the table conveys what the script does.
        if action.action_type == ActionType.PYTHON_COMPUTE:
            summary = info.get("compute_summary") or info.get("compute_error", "")
            reason = summary
            tgt = "scratch/outputs/"  # outputs land outside workspace
            compute_rows.append((i, action, info))
        else:
            reason = (action.reason or "").replace("\n", " ")
        if len(reason) > 60:
            reason = reason[:57] + "..."
        approve = "yes" if action.requires_approval else "no"
        lines.append(
            f"| {i} | {action.action_type.value} | `{src}` | `{tgt}` | {action.risk_level.value} | {approve} | {reason} |"
        )
    lines.append("")

    # v0.23 — dedicated compute-script section so reviewers can read
    # the actual code being approved. Each entry shows summary, inputs,
    # declared outputs, timeout, and the full script.
    if compute_rows:
        lines.append("## Compute scripts")
        lines.append("")
        lines.append(
            "_ComputeActions run a Python script inside an isolated scratch "
            "directory (see `docs/COMPUTE_ACTION.md`). Outputs do NOT land "
            "in the workspace — a follow-up pack stage is required to "
            "promote artefacts._"
        )
        lines.append("")
        for i, action, info in compute_rows:
            lines.append(f"### Action #{i} — `{action.action_id}`")
            if info.get("compute_error"):
                lines.append(f"- **Error:** {info['compute_error']}")
                lines.append("")
                continue
            lines.append(f"- **Summary:** {info['compute_summary']}")
            inputs = info.get("compute_inputs") or []
            outputs = info.get("compute_outputs") or []
            lines.append(
                f"- **Inputs ({len(inputs)}):** "
                + (", ".join(f"`{p}`" for p in inputs) if inputs else "_none_")
            )
            lines.append(
                f"- **Declared outputs ({len(outputs)}):** "
                + (", ".join(f"`{p}`" for p in outputs) if outputs else "_none_")
            )
            lines.append(f"- **Timeout:** {info['compute_timeout_sec']}s")
            lines.append("")
            lines.append("```python")
            script = info.get("compute_script") or ""
            # Cap the displayed script at ~4 KiB so a malformed plan
            # can't blow out the dry-run markdown. The full source is
            # always written to script.py at execute time.
            if len(script) > 4096:
                script = script[:4096] + "\n# ... (truncated; full source in scratch script.py)"
            lines.append(script.rstrip())
            lines.append("```")
            lines.append("")

    if plan.expected_outputs:
        lines.append("## Expected outputs")
        lines.extend(f"- {o}" for o in plan.expected_outputs)
        lines.append("")

    lines.append("## Risk summary")
    lines.append(plan.risk_summary or "_n/a_")
    lines.append("")
    return "\n".join(lines)
