from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RollbackOpType(str, Enum):
    MOVE_BACK = "move_back"
    DELETE_CREATED_FILE = "delete_created_file"
    DELETE_CREATED_DIR = "delete_created_dir"
    RESTORE_FROM_BACKUP = "restore_from_backup"
    # v0.23 — part of the 3rd §10.7 deliberate exception
    # (PYTHON_COMPUTE). Cleans the scratch dir for one ComputeAction.
    # The target is OUTSIDE the user workspace (under <home>/scratch/),
    # so the apply path uses ScratchWorkspace.cleanup_action rather
    # than workspace-relative resolve_inside.
    DELETE_SCRATCH_DIR = "delete_scratch_dir"


class RollbackEntry(BaseModel):
    action_id: str
    op: RollbackOpType
    source_path: str | None = None
    target_path: str | None = None
    backup_path: str | None = None
    metadata: dict = Field(default_factory=dict)


class RollbackManifest(BaseModel):
    run_id: str
    task_id: str
    entries: list[RollbackEntry] = Field(default_factory=list)
    file_hashes_before: dict[str, str] = Field(default_factory=dict)
    created_dirs: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
