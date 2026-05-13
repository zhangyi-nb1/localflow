from __future__ import annotations

from datetime import datetime, timezone

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
