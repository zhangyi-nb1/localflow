from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType


class PlanValidationError(Exception):
    pass


def validate_plan_structure(plan: ActionPlan) -> None:
    """Pydantic-level checks that go beyond field validation.

    Policy Guard handles path / forbidden checks. This module is about
    *plan well-formedness*: unique IDs, type/field coherence, etc.
    """
    if not plan.plan_id:
        raise PlanValidationError("plan_id is required")
    if not plan.task_id:
        raise PlanValidationError("task_id is required")

    ids: set[str] = set()
    for action in plan.actions:
        if action.action_id in ids:
            raise PlanValidationError(f"duplicate action_id: {action.action_id}")
        ids.add(action.action_id)

        if action.action_type == ActionType.MKDIR and action.source_path is not None:
            raise PlanValidationError(
                f"{action.action_id}: mkdir must not have source_path"
            )
        if action.action_type in {ActionType.MOVE, ActionType.RENAME, ActionType.COPY}:
            if action.source_path == action.target_path:
                raise PlanValidationError(
                    f"{action.action_id}: source_path equals target_path"
                )
