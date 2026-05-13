"""workspace_visualizer plan validation.

Beyond the generic action-validator + policy_guard checks, ensure:
  * The chart action carries ``binary_content_b64`` so the executor
    knows to decode + write bytes (writing an empty file would silently
    produce a 0-byte PNG that confuses image viewers).
  * The chart action targets a ``.png`` path.
  * There is exactly one chart action — the planner emits one PNG, no
    less and no more.
"""

from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType


class WorkspaceVisualizerValidationError(Exception):
    pass


def validate_workspace_visualizer_plan(plan: ActionPlan) -> None:
    png_actions = [
        a
        for a in plan.actions
        if a.action_type == ActionType.INDEX
        and a.target_path is not None
        and a.target_path.lower().endswith(".png")
    ]
    if len(png_actions) != 1:
        raise WorkspaceVisualizerValidationError(
            f"expected exactly 1 PNG chart action, got {len(png_actions)}"
        )

    chart_action = png_actions[0]
    if not chart_action.metadata.get("binary_content_b64"):
        raise WorkspaceVisualizerValidationError(
            f"chart action {chart_action.action_id} missing metadata.binary_content_b64"
        )

    md_actions = [
        a
        for a in plan.actions
        if a.action_type == ActionType.INDEX
        and a.target_path is not None
        and a.target_path.lower().endswith(".md")
    ]
    if not md_actions:
        raise WorkspaceVisualizerValidationError(
            "expected at least one markdown summary action — none found"
        )
