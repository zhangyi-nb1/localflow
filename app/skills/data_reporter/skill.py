from __future__ import annotations

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill
from app.skills.data_reporter.planner import plan_data_report
from app.skills.data_reporter.reporter import render_data_report
from app.skills.data_reporter.validator import validate_data_report_plan


class DataReporterSkill(Skill):
    """DataOps Skill (outline §13.7), TaskWeaver-inspired but **without**
    arbitrary code execution. All pandas calls are LocalFlow-owned;
    Phase 3.1 outputs a single text-only report. Phase 3.2 will add
    matplotlib chart generation; Phase 3.3 may add LLM-driven typed
    analysis specs (still no LLM-written code)."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="data_reporter",
            description=(
                "Scan .csv / .tsv / .xlsx files and emit a single Markdown "
                "report with per-table schema, basic statistics, and one "
                "auto-picked chart per table (histogram for numeric, bar "
                "for categorical)."
            ),
            version="0.2.0",
            capabilities=[
                "scan_tabular_files",
                "extract_schema",
                "compute_basic_stats",
                "synthesize_data_report",
                "generate_charts",
                "track_provenance",
            ],
            required_tools=[
                "data_ops.read_tabular",
                "data_ops.summarize_dataframe",
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
        return plan_data_report(task, snapshot)

    # plan_with_llm intentionally not overridden — Phase 3.1 is rule-only.

    def validate(self, plan: ActionPlan) -> None:
        validate_data_report_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_data_report(
            task=task, plan=plan, outcome=outcome, verification=verification
        )
