from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    skill: str = "folder_organizer"
    created_at: datetime = Field(default_factory=_utcnow)
