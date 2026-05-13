from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType
from app.skills._base import SkillError


class DataReporterValidationError(SkillError):
    pass


def validate_data_report_plan(plan: ActionPlan) -> None:
    """data_reporter plan invariants (Phase 3.2):
      * Either empty (no tabular files) OR
      * Exactly 1 markdown report action (the synthesis) PLUS zero or
        more chart actions (one per table that had a chartable column).
      * All chart actions must be ``index`` type with binary content.
    """
    if not plan.actions:
        return  # legitimate no-op

    # Find the markdown report — must be exactly one with text content.
    text_actions = [
        a for a in plan.actions
        if a.action_type == ActionType.INDEX and a.metadata.get("content") is not None
        and a.metadata.get("binary_content_b64") is None
    ]
    if len(text_actions) != 1:
        raise DataReporterValidationError(
            f"data_reporter should emit exactly 1 markdown report action, "
            f"got {len(text_actions)} text + "
            f"{len(plan.actions) - len(text_actions)} other"
        )

    report = text_actions[0]
    if not report.target_path:
        raise DataReporterValidationError("data_reporter report action requires target_path")
    if not report.metadata.get("content"):
        raise DataReporterValidationError(
            "data_reporter report action requires metadata.content (markdown body)"
        )
    prov = report.metadata.get("provenance")
    if not prov:
        raise DataReporterValidationError(
            "data_reporter report action requires metadata.provenance"
        )
    if prov.get("synthesis_kind") != "data_report":
        raise DataReporterValidationError(
            f"provenance.synthesis_kind must be 'data_report', got {prov.get('synthesis_kind')!r}"
        )

    # All other actions must be chart writes (index + binary).
    for a in plan.actions:
        if a is report:
            continue
        if a.action_type != ActionType.INDEX:
            raise DataReporterValidationError(
                f"action {a.action_id}: non-report actions must be 'index', got {a.action_type.value!r}"
            )
        if not a.metadata.get("binary_content_b64"):
            raise DataReporterValidationError(
                f"action {a.action_id}: chart action requires metadata.binary_content_b64"
            )
        if not a.metadata.get("chart_spec"):
            raise DataReporterValidationError(
                f"action {a.action_id}: chart action requires metadata.chart_spec"
            )
