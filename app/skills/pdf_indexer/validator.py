from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType
from app.skills._base import SkillError


class PdfIndexerValidationError(SkillError):
    pass


def validate_pdf_index_plan(plan: ActionPlan) -> None:
    """pdf_indexer-specific invariants beyond Pydantic + Policy Guard.

    The plan must consist of a single ``index`` action whose metadata
    carries non-empty content and a provenance block.
    """
    if not plan.actions:
        # An empty plan is legitimate (no PDFs in workspace). Skip
        # downstream checks but let the harness know this is a no-op.
        return

    if len(plan.actions) != 1:
        raise PdfIndexerValidationError(
            f"pdf_indexer should produce exactly 1 action, got {len(plan.actions)}"
        )

    action = plan.actions[0]
    if action.action_type != ActionType.INDEX:
        raise PdfIndexerValidationError(
            f"pdf_indexer action_type must be 'index', got {action.action_type.value!r}"
        )
    if not action.target_path:
        raise PdfIndexerValidationError("pdf_indexer action requires target_path")
    if not action.metadata.get("content"):
        raise PdfIndexerValidationError(
            "pdf_indexer action requires metadata.content (the markdown body)"
        )
    if "provenance" not in action.metadata:
        raise PdfIndexerValidationError(
            "pdf_indexer action requires metadata.provenance (source tracking)"
        )
