from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Locale = Literal["zh-CN", "en-US"]
DEFAULT_LOCALE: Locale = "zh-CN"


class TaskSpec(BaseModel):
    task_id: str
    user_goal: str
    workspace_root: str
    constraints: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Phase 5 — workspace-relative paths the agent must never touch. "
            "Enforced kernel-side by policy_guard for every action."
        ),
    )
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Phase 5 — skill-consumable preferences (e.g. naming_style). "
            "Documented keys live in app/memory/_schema.py."
        ),
    )
    expected_outputs: list[str] = Field(
        default_factory=list,
        description=(
            "Phase 20 — workspace-relative paths the caller (recipe / task) "
            "expects this skill to produce. The agent meta-skill surfaces "
            "these to the LLM in the user prompt so it knows exactly which "
            "deliverables to generate (e.g. README.md AND SOURCES.md). "
            "Per-skill graders also consume this. Empty when the caller has "
            "no specific contract — same default as before Phase 20."
        ),
    )
    skill: str = "folder_organizer"
    locale: Locale = Field(
        default=DEFAULT_LOCALE,
        description=(
            "v0.22 — language for user-facing generated content (plan "
            "summaries, action reasons, README/SOURCES, verifier rationales, "
            "repair hints). Defaults to zh-CN. Routed into every LLM prompt "
            "via app.agent.locale_prompts.locale_instruction(). Internal "
            "schema names (ActionType, verifier codes) stay English; only "
            "the prose facing the user respects this field."
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow)
