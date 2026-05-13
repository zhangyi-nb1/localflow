"""data_analyzer skill-specific plan validation.

The Harness Kernel (Pydantic + Policy Guard) already validates schema
shape and path safety. This module enforces invariants specific to
data_analyzer: ``analysis_report.md`` must be present, charts must live
under ``analysis_charts/``, every action must be ``index`` (no destructive
operations belong in a read-only analysis skill).
"""
from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType


class DataAnalyzerValidationError(ValueError):
    pass


def validate_data_analyzer_plan(plan: ActionPlan) -> None:
    if not plan.actions:
        return  # legitimate no-op (no tabular files)

    seen_report = False
    for a in plan.actions:
        if a.action_type != ActionType.INDEX:
            raise DataAnalyzerValidationError(
                f"data_analyzer only emits 'index' actions; got {a.action_type.value} "
                f"in action {a.action_id}"
            )
        if a.target_path == "analysis_report.md":
            seen_report = True
            if not a.metadata.get("content"):
                raise DataAnalyzerValidationError(
                    f"action {a.action_id} (analysis_report.md) has empty content"
                )
        else:
            # Chart action: must be under analysis_charts/ AND have binary payload.
            if not a.target_path.startswith("analysis_charts/"):
                raise DataAnalyzerValidationError(
                    f"non-report action {a.action_id} must target "
                    f"analysis_charts/...; got {a.target_path!r}"
                )
            if not a.metadata.get("binary_content_b64"):
                raise DataAnalyzerValidationError(
                    f"chart action {a.action_id} missing binary_content_b64"
                )
    if not seen_report:
        raise DataAnalyzerValidationError(
            "data_analyzer plan must contain exactly one analysis_report.md action"
        )
