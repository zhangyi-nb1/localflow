from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.action import Action


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActionPlan(BaseModel):
    plan_id: str
    task_id: str
    summary: str
    actions: list[Action] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    risk_summary: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    # ---------------------------------------------------------------
    # Phase 25.1 — LLM provenance (optional, populated only by LLM planner)
    # ---------------------------------------------------------------
    #
    # When the planner is the LLM (not rule-based), capture the LLM's
    # thought + reasoning + raw tool_use so the executor can fold them
    # into each ActionTraceEvent emitted for the plan's actions. This
    # is the foundational data the Phase 25 refactor needs — once
    # available on the ActionPlan, the executor doesn't need any new
    # arguments at call sites.
    #
    # Backward-compat: all three default to None. Rule-based planners,
    # legacy serialised plans, and test fixtures keep working unchanged.

    llm_thought: str | None = Field(
        default=None,
        description=(
            "Human-readable narrative from the LLM (Anthropic's flattened "
            "``thinking`` text or OpenAI's ``reasoning_content``) that "
            "produced these actions. None when the planner was rule-based."
        ),
    )
    llm_reasoning: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Structured ``thinking_blocks`` from the model API (Anthropic "
            "extended thinking; OpenAI o-series reasoning). Kept distinct "
            "from ``llm_thought`` so callers can inspect the raw blocks "
            "without re-parsing the flattened narrative."
        ),
    )
    llm_tool_call_raw: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The raw ``tool_use`` block (``name`` + ``input``) from the LLM "
            "API response, before the harness coerced ``input`` into typed "
            "Actions. Useful for debugging schema-validation failures."
        ),
    )
