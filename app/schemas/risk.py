from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RiskVerdict(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class RiskAssessment(BaseModel):
    plan_id: str
    passed: bool
    blocked_actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    risk_level: RiskVerdict = RiskVerdict.LOW
    reason: str = ""
