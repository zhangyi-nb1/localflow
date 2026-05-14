"""Rule fallback for the agent skill.

When an LLM is unavailable (no API key, offline test environments,
explicit ``--planner rule`` choice from the CLI), the agent skill
needs to still produce a sane plan. The fallback is intentionally
simple: it delegates to folder_organizer's well-tested rule planner
and skips the chart generation step. The user gets organization
without visualization, plus a clear note in the plan summary that
LLM planning would have done more.

This keeps the harness's "skill must always produce a plan" contract
honest while making the trade-off explicit.
"""

from __future__ import annotations

from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.skills.folder_organizer.planner import plan_organization


def plan_agent_fallback(task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
    """Delegate to folder_organizer's rule planner. Annotate the plan
    summary so users know they're seeing the rule-only fallback (no
    chart, no semantic naming, no multi-step orchestration)."""
    plan = plan_organization(task, snapshot)
    plan.summary = (
        f"[agent rule-fallback] {plan.summary} — note: rule planning skips chart "
        "generation and multi-step synthesis. Use --planner llm (or the UI default) "
        "to unlock the full agent capabilities."
    )
    return plan
