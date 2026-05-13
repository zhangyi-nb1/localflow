"""data_analyzer Skill — Phase 3.3a (rule path; LLM path coming in 3.3b).

Implements the ``Skill`` ABC from app.skills._base. Follows the same
shape as ``pdf_indexer`` / ``data_reporter``: rule-based plan() that
walks the workspace and emits ``analysis_report.md`` plus N chart PNGs
under ``analysis_charts/``.
"""
from __future__ import annotations

from app.schemas import ActionPlan, SkillManifest, TaskSpec, VerificationResult, WorkspaceSnapshot
from app.skills._base import Skill
from app.skills.data_analyzer.planner import plan_data_analysis
from app.skills.data_analyzer.reporter import render_final_report
from app.skills.data_analyzer.validator import validate_data_analyzer_plan


class DataAnalyzerSkill(Skill):
    """Targeted, deep analysis of tabular data via typed AnalysisSpec.

    Contrast with ``data_reporter`` (broad schema + auto chart per file).
    This one runs a real groupby/aggregation pipeline against each file
    and reports the *result* — not just the structure.
    """

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="data_analyzer",
            description=(
                "Phase 3.3 — typed AnalysisSpec-driven analysis. Rule planner "
                "picks a default groupby+aggregation per file; LLM planner "
                "(Phase 3.3b) will accept natural-language analysis goals."
            ),
            version="0.1.0",
            capabilities=[
                "read_csv",
                "read_xlsx",
                "run_typed_analysis_spec",
                "render_chart",
                "synthesize_markdown_report",
            ],
            required_tools=[
                "data_ops.is_supported_tabular",
                "data_ops.read_tabular",
                "data_ops.summarize_dataframe",
                "data_analysis.execute_analysis",
                "chart_ops.histogram_png",
                "chart_ops.bar_png",
            ],
            allowed_actions=["index"],
            requires_approval=["index"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return plan_data_analysis(task, snapshot)

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        """Phase 3.3b — LLM-driven path. The LLM emits a typed
        ``AnalysisSpec`` via strict tool call; LocalFlow's engine runs
        it. Same return shape as the rule ``plan()`` above so the rest
        of the harness can't tell which path produced the plan.
        """
        from app.agent.analysis_planner import plan_analysis_with_llm

        return plan_analysis_with_llm(task, snapshot, **kwargs)

    def validate(self, plan: ActionPlan) -> None:
        validate_data_analyzer_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_final_report(
            task=task, plan=plan, outcome=outcome, verification=verification,
        )
