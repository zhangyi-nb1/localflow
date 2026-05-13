from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FileMeta(BaseModel):
    path: str
    file_type: str
    size_bytes: int
    modified_at: datetime
    sha256: str | None = None
    text_preview: str | None = None


class WorkspaceSnapshot(BaseModel):
    snapshot_id: str
    task_id: str
    root: str
    files: list[FileMeta] = Field(default_factory=list)
    total_files: int = 0
    total_size_bytes: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
