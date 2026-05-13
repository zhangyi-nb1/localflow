"""WorkspaceVisualizerSkill — Skill ABC adapter.

The skill counts files in the workspace by either (a) parent directory
(when files are already organized into subfolders) or (b) file_type
category (when files are still at the root), renders a PNG bar chart,
and writes both a Markdown summary and the PNG image into the
workspace.
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
from app.skills.workspace_visualizer.planner import plan_workspace_visualization
from app.skills.workspace_visualizer.reporter import render_final_report
from app.skills.workspace_visualizer.validator import validate_workspace_visualizer_plan


class WorkspaceVisualizerSkill(Skill):
    """Rule-only skill (no LLM planner) — counting + plotting are pure
    deterministic operations, an LLM has nothing to contribute."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="workspace_visualizer",
            description=(
                "Count files by folder or category and render a PNG bar chart "
                "summary of the workspace."
            ),
            version="0.1.0",
            capabilities=[
                "scan_files",
                "count_by_folder",
                "count_by_file_type",
                "render_bar_chart_png",
                "generate_summary_md",
            ],
            required_tools=["chart_ops.bar_png"],
            allowed_actions=["mkdir", "index"],
            requires_approval=["mkdir"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return plan_workspace_visualization(task, snapshot)

    # plan_with_llm intentionally not overridden — counts + matplotlib
    # are deterministic, an LLM adds latency without adding signal.

    def validate(self, plan: ActionPlan) -> None:
        validate_workspace_visualizer_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_final_report(task=task, plan=plan, outcome=outcome, verification=verification)
