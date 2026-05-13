from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.harness.audit import AuditLogger
from app.harness.policy_guard import resolve_inside
from app.schemas import RollbackManifest
from app.schemas.rollback import RollbackEntry, RollbackOpType
from app.storage.jsonl_logger import JsonlLogger
from app.storage.run_store import RunStore
from app.tools import file_ops


def _sweep_empty_subdirs(root: Path) -> None:
    """Recursively remove EMPTY subdirectories under ``root``.

    Walks children depth-first so deeper dirs are removed before their
    parents. Non-empty dirs are left intact — this is the safety property
    that lets rollback still refuse to clobber real user data.
    """
    candidates = [p for p in root.rglob("*") if p.is_dir()]
    candidates.sort(key=lambda p: len(p.parts), reverse=True)
    for path in candidates:
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            # Race / permission issue — leave it for the caller's check.
            pass


@dataclass
class RollbackOutcome:
    success: bool
    undone: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


class Rollback:
    """Replays a RollbackManifest in reverse to restore the workspace.

    The rollback log itself is appended to the run's execution_log.jsonl so
    that a future verify pass can see what happened.
    """

    def __init__(self, workspace_root: Path, run_store: RunStore) -> None:
        self.workspace_root = workspace_root.resolve()
        self.run_store = run_store
        self.exec_log = JsonlLogger(run_store.execution_log_path)
        self.audit = AuditLogger(run_store.audit_log_path)

    def run(self, manifest: RollbackManifest) -> RollbackOutcome:
        self.audit.log("rollback.start", run_id=manifest.run_id, count=len(manifest.entries))
        undone: list[str] = []
        failed: list[dict] = []

        for entry in reversed(manifest.entries):
            try:
                self._apply(entry)
                undone.append(entry.action_id)
                self.exec_log.write(
                    "rollback.apply",
                    {"action_id": entry.action_id, "op": entry.op.value, "status": "success"},
                )
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                failed.append({"action_id": entry.action_id, "op": entry.op.value, "error": err})
                self.exec_log.write(
                    "rollback.apply",
                    {"action_id": entry.action_id, "op": entry.op.value, "status": "failed", "error": err},
                )

        self.audit.log(
            "rollback.end",
            run_id=manifest.run_id,
            success=not failed,
            undone=len(undone),
            failed=len(failed),
        )
        return RollbackOutcome(success=not failed, undone=undone, failed=failed)

    def _apply(self, entry: RollbackEntry) -> None:
        if entry.op == RollbackOpType.MOVE_BACK:
            src = resolve_inside(self.workspace_root, entry.source_path or "")
            dst = resolve_inside(self.workspace_root, entry.target_path or "")
            if not src.exists():
                raise FileNotFoundError(f"rollback source missing: {entry.source_path}")
            if dst.exists():
                raise FileExistsError(f"rollback target already exists: {entry.target_path}")
            file_ops.move(src, dst)
        elif entry.op == RollbackOpType.DELETE_CREATED_FILE:
            tgt = resolve_inside(self.workspace_root, entry.target_path or "")
            file_ops.remove_file(tgt)
        elif entry.op == RollbackOpType.DELETE_CREATED_DIR:
            tgt = resolve_inside(self.workspace_root, entry.target_path or "")
            if not tgt.exists():
                return
            # A directory created by mkdir can accumulate *empty* leftover
            # subdirectories if any move/copy/index target inside it used
            # a nested path (e.g. move source -> codebase/subdir/foo). The
            # executor's `target.parent.mkdir(parents=True)` creates those
            # intermediate dirs implicitly and doesn't record them as
            # their own rollback entries. After all child moves are
            # reversed, the empty shells stay behind and block the parent
            # removal. Sweep them here — but only EMPTY ones, so genuine
            # user data inside still triggers the refusal below.
            _sweep_empty_subdirs(tgt)
            if not file_ops.remove_empty_dir(tgt):
                raise OSError(f"created dir is not empty, refusing to remove: {entry.target_path}")
        elif entry.op == RollbackOpType.RESTORE_FROM_BACKUP:
            # Restore a file that was overwritten during this run. The
            # backup lives under the run dir (NOT inside workspace_root)
            # so we resolve it against run_store.run_dir directly.
            if not entry.backup_path or not entry.target_path:
                raise ValueError(
                    f"RESTORE_FROM_BACKUP requires backup_path and target_path; got {entry}"
                )
            tgt = resolve_inside(self.workspace_root, entry.target_path)
            backup_abs = (self.run_store.run_dir / entry.backup_path).resolve()
            # Defense: backup file must actually live under the run dir.
            try:
                backup_abs.relative_to(self.run_store.run_dir.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"backup_path escapes run dir: {entry.backup_path}"
                ) from exc
            if not backup_abs.exists():
                raise FileNotFoundError(f"backup not found: {entry.backup_path}")
            # Remove the new content, then move the backup back.
            if tgt.exists():
                tgt.unlink()
            tgt.parent.mkdir(parents=True, exist_ok=True)
            file_ops.move(backup_abs, tgt)
        else:
            raise ValueError(f"unknown rollback op: {entry.op}")
