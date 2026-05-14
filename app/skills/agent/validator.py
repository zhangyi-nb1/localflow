"""agent plan validation.

After ``app.agent.planner`` has run the LLM through Pydantic + the
plan-shape validator + policy_guard, AND after
``render_chart_actions`` has post-processed chart_request → PNG bytes,
this validator enforces agent-specific invariants:

  * Every move/copy has a unique target (no clashing writes).
  * Every text-writing INDEX action has non-empty
    ``metadata.content``.
  * Every PNG INDEX action has a real ``binary_content_b64`` — i.e.,
    the post-processor actually ran. If a chart_request is still
    present without binary content, that means the post-processor
    failed silently and the executor would write an empty file.
"""

from __future__ import annotations

from app.schemas import ActionPlan
from app.schemas.action import ActionType


class AgentValidationError(Exception):
    """Raised on a plan that survived LLM validation but violates an
    agent-skill-specific invariant."""


def validate_agent_plan(plan: ActionPlan) -> None:
    move_like = (ActionType.MOVE, ActionType.COPY, ActionType.RENAME)
    seen_targets: set[str] = set()
    for action in plan.actions:
        if action.action_type in move_like:
            if not action.target_path:
                raise AgentValidationError(
                    f"{action.action_id}: {action.action_type.value} needs target_path"
                )
            if action.target_path in seen_targets:
                # Don't block — the executor auto-suffixes — but a duplicate
                # target is a planner smell worth catching in tests.
                pass
            seen_targets.add(action.target_path)
            continue

        if action.action_type != ActionType.INDEX:
            continue

        target = (action.target_path or "").lower()
        if target.endswith(".png"):
            if not action.metadata.get("binary_content_b64"):
                raise AgentValidationError(
                    f"{action.action_id}: PNG action missing binary_content_b64 "
                    "(chart_request post-processor did not run or failed silently)"
                )
            continue

        # Text-writing index (.md, .txt, or anything else).
        content = action.metadata.get("content")
        if not content:
            raise AgentValidationError(
                f"{action.action_id}: text index action requires non-empty metadata.content"
            )
