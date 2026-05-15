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
    TRACE_JSONL = "trace.jsonl"  # Phase 9 — structured kernel-event stream
    ROLLBACK_JSON = "rollback_manifest.json"
    VERIFY_JSON = "verify_report.json"
    FINAL_REPORT_MD = "final_report.md"
    BACKUPS_DIR = "backups"
    # Phase 10 — multi-stage execution artifacts. Each stage's plan /
    # dry-run / actions live under <run_dir>/stages/<stage_id>/.
    STAGES_DIR = "stages"
    TASKGRAPH_JSON = "taskgraph.json"
    TASKGRAPH_RESULT_JSON = "taskgraph_result.json"
    # Phase 11 — plan refinement loop. Each revise produces a new
    # plan_vN.json under plans/; plan.json mirrors the latest version
    # so every existing reader (executor / verifier / rollback) keeps
    # working unchanged.
    PLANS_DIR = "plans"
    REVISIONS_LOG = "revisions.jsonl"
    # Phase 13 — semantic verification result + auto-repair journal.
    # Parallel to verify_report.json (structural); kernel modules read
    # the structural file only, so adding this artifact is invisible to
    # executor / verifier / rollback.
    SEMANTIC_VERIFY_JSON = "semantic_verify.json"
    REPAIRS_LOG = "repairs.jsonl"

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
    def trace_path(self) -> Path:
        """Phase 9 — structured kernel-event stream (TraceLogger)."""
        return self.path(self.TRACE_JSONL)

    # -- Phase 10 multi-stage artifacts --------------------------------

    @property
    def stages_root(self) -> Path:
        """``<run_dir>/stages/`` — parent dir for all per-stage subdirs."""
        return self.run_dir / self.STAGES_DIR

    def stage_dir(self, stage_id: str) -> Path:
        """Return (and create) ``<run_dir>/stages/<stage_id>/``.

        The directory is created on first access so the TaskGraphRunner
        doesn't need separate ``mkdir`` plumbing. The per-stage artifacts
        (plan.json / dry_run.md / actions.json) land here via
        :class:`app.harness.taskgraph_runner.StageRunStore`.
        """
        d = self.stages_root / stage_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def taskgraph_path(self) -> Path:
        """``<run_dir>/taskgraph.json`` — the persisted graph spec."""
        return self.path(self.TASKGRAPH_JSON)

    @property
    def taskgraph_result_path(self) -> Path:
        """``<run_dir>/taskgraph_result.json`` — aggregated stage results."""
        return self.path(self.TASKGRAPH_RESULT_JSON)

    # -- Phase 11 plan refinement artifacts ----------------------------

    @property
    def plans_dir(self) -> Path:
        """``<run_dir>/plans/`` — created lazily on first revise.

        Tasks with no revisions never trigger directory creation; the
        single canonical ``plan.json`` covers them. The first revise
        backfills ``plan_v1.json`` here so the audit trail is complete.
        """
        return self.run_dir / self.PLANS_DIR

    def plan_version_path(self, version: int) -> Path:
        """Path of ``plan_v<version>.json``. Does NOT create the dir —
        callers must invoke :meth:`save_plan_version` which mkdir-ps."""
        return self.plans_dir / f"plan_v{version}.json"

    @property
    def revisions_log_path(self) -> Path:
        """``<run_dir>/revisions.jsonl`` — one JSON line per revise."""
        return self.path(self.REVISIONS_LOG)

    def save_plan_version(self, plan: ActionPlan, version: int) -> None:
        """Persist ``plan_vN.json`` under plans/ AND mirror to plan.json.

        Existing code paths (executor / verifier / rollback) read
        plan.json — the mirror keeps them oblivious to versioning. The
        versioned file under plans/ is the audit trail.
        """
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self.write_model(self.plan_version_path(version), plan)
        self.save_plan(plan)

    def list_plan_versions(self) -> list[int]:
        """Return sorted list of version numbers under ``plans/``.

        Empty list for tasks that never invoked refinement (their
        plan.json is conceptually v1 but not materialized as
        plan_v1.json until the first revise backfills it)."""
        if not self.plans_dir.exists():
            return []
        versions: list[int] = []
        for p in self.plans_dir.glob("plan_v*.json"):
            try:
                versions.append(int(p.stem.split("_v")[1]))
            except (ValueError, IndexError):
                continue
        return sorted(versions)

    # -- Phase 13 semantic verification + repair artifacts -------------

    @property
    def semantic_verify_path(self) -> Path:
        """``<run_dir>/semantic_verify.json`` — the latest
        :class:`~app.schemas.SemanticVerificationResult`. Overwritten
        on each repair attempt so the file always reflects the final
        state the user sees."""
        return self.path(self.SEMANTIC_VERIFY_JSON)

    @property
    def repairs_log_path(self) -> Path:
        """``<run_dir>/repairs.jsonl`` — one JSON line per auto-repair
        attempt: {ts, attempt, grader, suggested_hint, plan_version,
        outcome}. Parallel to revisions.jsonl from Phase 11; revisions
        is user-driven, repairs is harness-driven."""
        return self.path(self.REPAIRS_LOG)

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
        path.write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )

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
