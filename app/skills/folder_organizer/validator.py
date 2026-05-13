from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType


class SkillValidationError(Exception):
    pass


def validate_folder_organizer_plan(plan: ActionPlan) -> None:
    """Folder-organizer-specific plan checks.

    The generic Pydantic and Policy Guard checks have already run; this is
    the place for invariants that are only meaningful to this skill.
    """
    move_targets: set[str] = set()
    for action in plan.actions:
        if action.action_type == ActionType.MOVE:
            if not action.target_path:
                raise SkillValidationError(f"{action.action_id}: move needs target_path")
            if action.target_path in move_targets:
                # Same target rel path for two moves — could collide; the
                # executor will auto-suffix, but flag it as a planner smell.
                # Phase 0: warn (not block) by accepting; future planner
                # versions should produce unique targets.
                pass
            move_targets.add(action.target_path)
