from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class VerificationResult(BaseModel):
    task_id: str
    run_id: str
    passed: bool
    checks: list[VerificationCheck] = Field(default_factory=list)
    failed_checks: list[VerificationCheck] = Field(default_factory=list)
    summary: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
