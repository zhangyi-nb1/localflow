from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExecutionRecord(BaseModel):
    run_id: str
    action_id: str
    status: ExecutionStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    rollback_action: dict | None = None
    file_hash_before: str | None = None
    file_hash_after: str | None = None
