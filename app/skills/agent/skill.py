"""AgentSkill — v0.9.0 default LLM-driven meta-skill.

Composes folder_organizer's organization logic with data_*'s binary-
chart pipeline so a single ActionPlan can cover compound user goals
("organize my workspace, then chart file counts as a PNG, then write
a summary report") end-to-end.

The rule planner is intentionally a thin wrapper over folder_organizer
— if the LLM is unavailable, we still want to do *something* useful
rather than crash. The LLM path is where the real composition happens.
"""

from __future__ import annotations

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill
from app.skills.agent.llm_planner import (
    AGENT_SYSTEM_PROMPT,
    render_chart_actions,
    validate_compound_goal_coverage,
)
from app.skills.agent.planner import plan_agent_fallback
from app.skills.agent.reporter import render_final_report
from app.skills.agent.validator import validate_agent_plan


class AgentSkill(Skill):
    """Default meta-skill — produces a single multi-step ActionPlan."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="agent",
            description=(
                "Autonomous LLM-driven meta-skill. Produces a single end-to-end "
                "ActionPlan covering organization, semantic rename, markdown reports, "
                "and PNG bar charts for compound user goals."
            ),
            version="0.1.0",
            capabilities=[
                "scan_files",
                "classify_files",
                "propose_moves",
                "semantic_rename",
                "generate_index",
                "render_bar_chart_png",
                "detect_duplicate_candidates",
                "decompose_compound_goal",
            ],
            required_tools=["chart_ops.bar_png"],
            allowed_actions=["mkdir", "move", "rename", "copy", "index"],
            requires_approval=["mkdir", "move", "rename", "copy"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return plan_agent_fallback(task, snapshot)

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        # Local import to avoid circular: skills -> agent -> skills via prompts.
        from app.agent import plan_with_llm as agent_plan_with_llm

        plan = agent_plan_with_llm(
            task,
            snapshot,
            system_prompt=AGENT_SYSTEM_PROMPT,
            extra_validator=validate_compound_goal_coverage,
            **kwargs,
        )
        # Post-process: render any chart_request blocks into PNG bytes
        # so the harness sees a fully-resolved ActionPlan.
        plan = render_chart_actions(plan)
        return plan

    def validate(self, plan: ActionPlan) -> None:
        validate_agent_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_final_report(task=task, plan=plan, outcome=outcome, verification=verification)
