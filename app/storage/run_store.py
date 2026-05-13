from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.schemas import (
    ActionPlan,
    RollbackManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)

T = TypeVar("T", bound=BaseModel)


def localflow_home(base: Path | None = None) -> Path:
    """Resolve the LocalFlow state directory.

    Order of precedence:
      1. ``base`` argument (used by tests).
      2. ``LOCALFLOW_HOME`` environment variable.
      3. ``<cwd>/.localflow``.
    """
    if base is not None:
        return Path(base)
    env = os.environ.get("LOCALFLOW_HOME")
    if env:
        return Path(env)
    return Path.cwd() / ".localflow"


class RunStore:
    """Owns the on-disk layout of a single task/run.

    Layout::

        <home>/runs/<task_id>/
            task.json
            workspace_snapshot.json
            plan.json
            dry_run.md
            actions.json
            execution_log.jsonl
            rollback_manifest.json
            verify_report.json
            final_report.md
    """

    TASK_JSON = "task.json"
    WORKSPACE_JSON = "workspace_snapshot.json"
    PLAN_JSON = "plan.json"
    DRY_RUN_MD = "dry_run.md"
    ACTIONS_JSON = "actions.json"
    EXECUTION_LOG = "execution_log.jsonl"
    AUDIT_LOG = "audit.jsonl"
    ROLLBACK_JSON = "rollback_manifest.json"
    VERIFY_JSON = "verify_report.json"
    FINAL_REPORT_MD = "final_report.md"
    BACKUPS_DIR = "backups"

    def __init__(self, task_id: str, home: Path | None = None) -> None:
        self.task_id = task_id
        self.home = localflow_home(home)
        self.run_dir = self.home / "runs" / task_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / self.BACKUPS_DIR).mkdir(exist_ok=True)

    @classmethod
    def create(cls, home: Path | None = None, now: datetime | None = None) -> "RunStore":
        """Allocate a fresh ``<YYYY-MM-DD>-NNN`` task id and return its store."""
        home_path = localflow_home(home)
        runs_root = home_path / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        today = (now or datetime.now()).strftime("%Y-%m-%d")
        existing = sorted(p.name for p in runs_root.glob(f"{today}-*") if p.is_dir())
        next_seq = 1
        if existing:
            try:
                next_seq = max(int(name.rsplit("-", 1)[-1]) for name in existing) + 1
            except ValueError:
                next_seq = len(existing) + 1
        task_id = f"{today}-{next_seq:03d}"
        return cls(task_id=task_id, home=home_path)

    # -- path helpers --------------------------------------------------

    def path(self, name: str) -> Path:
        return self.run_dir / name

    @property
    def task_path(self) -> Path:
        return self.path(self.TASK_JSON)

    @property
    def workspace_path(self) -> Path:
        return self.path(self.WORKSPACE_JSON)

    @property
    def plan_path(self) -> Path:
        return self.path(self.PLAN_JSON)

    @property
    def dry_run_path(self) -> Path:
        return self.path(self.DRY_RUN_MD)

    @property
    def actions_path(self) -> Path:
        return self.path(self.ACTIONS_JSON)

    @property
    def execution_log_path(self) -> Path:
        return self.path(self.EXECUTION_LOG)

    @property
    def audit_log_path(self) -> Path:
        return self.path(self.AUDIT_LOG)

    @property
    def rollback_path(self) -> Path:
        return self.path(self.ROLLBACK_JSON)

    @property
    def verify_path(self) -> Path:
        return self.path(self.VERIFY_JSON)

    @property
    def final_report_path(self) -> Path:
        return self.path(self.FINAL_REPORT_MD)

    @property
    def backups_dir(self) -> Path:
        return self.path(self.BACKUPS_DIR)

    # -- generic IO ----------------------------------------------------

    def write_model(self, path: Path, model: BaseModel) -> None:
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    def read_model(self, path: Path, cls: type[T]) -> T:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def write_text(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def write_json(self, path: Path, data: dict | list) -> None:
        path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    def read_json(self, path: Path) -> dict | list:
        return json.loads(path.read_text(encoding="utf-8"))

    # -- typed helpers -------------------------------------------------

    def save_task(self, task: TaskSpec) -> None:
        self.write_model(self.task_path, task)

    def load_task(self) -> TaskSpec:
        return self.read_model(self.task_path, TaskSpec)

    def save_workspace(self, snap: WorkspaceSnapshot) -> None:
        self.write_model(self.workspace_path, snap)

    def load_workspace(self) -> WorkspaceSnapshot:
        return self.read_model(self.workspace_path, WorkspaceSnapshot)

    def save_plan(self, plan: ActionPlan) -> None:
        self.write_model(self.plan_path, plan)

    def load_plan(self) -> ActionPlan:
        return self.read_model(self.plan_path, ActionPlan)

    def save_rollback(self, manifest: RollbackManifest) -> None:
        self.write_model(self.rollback_path, manifest)

    def load_rollback(self) -> RollbackManifest:
        return self.read_model(self.rollback_path, RollbackManifest)

    def save_verification(self, result: VerificationResult) -> None:
        self.write_model(self.verify_path, result)

    def load_verification(self) -> VerificationResult:
        return self.read_model(self.verify_path, VerificationResult)

    def exists(self, name: str) -> bool:
        return self.path(name).exists()
