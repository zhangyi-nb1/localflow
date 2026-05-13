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
from app.tools.hash_ops import sha256_file


class RollbackConflict(Exception):
    """Raised when the file targeted by a rollback entry has been
    modified by the user since execute.

    The Executor records ``after_hash`` for every file-write op in
    ``RollbackEntry.metadata``. ``_check_drift`` compares the current
    on-disk hash; a mismatch indicates the user edited the file after
    execute and proceeding with rollback would clobber those edits.

    Callers can pass ``force=True`` to ``Rollback.run`` to bypass.
    """


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
    # Phase 7.1: entries skipped because the target file's current hash
    # doesn't match the executor-recorded post-execute hash (i.e., the
    # user manually edited the file after execute). Distinct from
    # ``failed`` so the caller can tell "rollback hit a conflict" from
    # "rollback hit a real error".
    conflicts: list[dict] = field(default_factory=list)


@dataclass
class RollbackPreview:
    """Read-only preview of what ``Rollback.run`` would do.

    For each entry: the inverse op, the target path it would touch, and
    whether the target's current state matches what the executor
    recorded after the write (``after_hash``). Useful for MCP clients
    to show users a confirmation page before triggering the actual
    rollback, mirroring the dry_run → execute flow for forward writes.
    """

    run_id: str
    entries: list[dict] = field(default_factory=list)
    has_conflicts: bool = False


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

    def run(self, manifest: RollbackManifest, *, force: bool = False) -> RollbackOutcome:
        """Replay ``manifest`` in reverse to restore the workspace.

        Phase 7.1: hash-drift detection. Before applying any file-write
        rollback (MOVE_BACK / DELETE_CREATED_FILE / RESTORE_FROM_BACKUP),
        we compare the file's current sha256 against the
        ``after_hash`` the Executor recorded post-execute. If they
        differ, the user has manually modified the file after execute;
        proceeding would clobber those changes.

        With ``force=False`` (the safe default) such entries are
        recorded as **conflicts** and skipped — the rollback proceeds
        for non-conflicted entries. With ``force=True`` the drift is
        recorded in the audit log but the op proceeds anyway, matching
        the CLI ``--force`` opt-in.
        """
        self.audit.log(
            "rollback.start",
            run_id=manifest.run_id,
            count=len(manifest.entries),
            force=force,
        )
        undone: list[str] = []
        failed: list[dict] = []
        conflicts: list[dict] = []

        for entry in reversed(manifest.entries):
            drift = self._check_drift(entry)
            if drift is not None and not force:
                conflicts.append(
                    {
                        "action_id": entry.action_id,
                        "op": entry.op.value,
                        "target_path": entry.target_path,
                        "reason": drift,
                    }
                )
                self.exec_log.write(
                    "rollback.apply",
                    {
                        "action_id": entry.action_id,
                        "op": entry.op.value,
                        "status": "conflict",
                        "reason": drift,
                    },
                )
                continue
            if drift is not None and force:
                # Force-mode: record the drift but proceed.
                self.exec_log.write(
                    "rollback.apply",
                    {
                        "action_id": entry.action_id,
                        "op": entry.op.value,
                        "status": "force_override",
                        "reason": drift,
                    },
                )
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
                    {
                        "action_id": entry.action_id,
                        "op": entry.op.value,
                        "status": "failed",
                        "error": err,
                    },
                )

        self.audit.log(
            "rollback.end",
            run_id=manifest.run_id,
            success=not failed and not conflicts,
            undone=len(undone),
            failed=len(failed),
            conflicts=len(conflicts),
        )
        return RollbackOutcome(
            success=not failed and not conflicts,
            undone=undone,
            failed=failed,
            conflicts=conflicts,
        )

    def preview(self, manifest: RollbackManifest) -> RollbackPreview:
        """Read-only preview — returns what ``run`` would do without
        touching the filesystem. Used by the MCP ``rollback_preview``
        tool so a client can show a user the planned reverse ops + any
        drift conflicts before requesting the destructive op."""
        preview = RollbackPreview(run_id=manifest.run_id)
        for entry in reversed(manifest.entries):
            drift = self._check_drift(entry)
            preview.entries.append(
                {
                    "action_id": entry.action_id,
                    "op": entry.op.value,
                    "target_path": entry.target_path,
                    "source_path": entry.source_path,
                    "backup_path": entry.backup_path,
                    "drift": drift,  # None = clean, str = mismatch reason
                }
            )
            if drift is not None:
                preview.has_conflicts = True
        return preview

    def _check_drift(self, entry: RollbackEntry) -> str | None:
        """Return None if the entry's target matches the executor-
        recorded after_hash, else a human-readable mismatch reason.

        Returns None (no drift) when:
          * the entry has no ``after_hash`` recorded (dir ops, or
            entries written before Phase 7.1)
          * the target file is missing (it was already moved/deleted —
            inverse op will handle it normally)
          * the current hash matches the recorded after_hash
        """
        after_hash = entry.metadata.get("after_hash") if entry.metadata else None
        if not after_hash:
            return None  # legacy entry or non-file op
        # The "file to hash" depends on the op direction.
        if entry.op == RollbackOpType.MOVE_BACK:
            check_path = entry.source_path  # file's CURRENT location
        elif entry.op in (RollbackOpType.DELETE_CREATED_FILE, RollbackOpType.RESTORE_FROM_BACKUP):
            check_path = entry.target_path
        else:
            return None  # DELETE_CREATED_DIR — no hash applicable
        if not check_path:
            return None
        try:
            abs_path = resolve_inside(self.workspace_root, check_path)
        except Exception:
            return None
        if not abs_path.is_file():
            return None  # file already gone — inverse op handles it
        current = sha256_file(abs_path)
        if current == after_hash:
            return None
        return (
            f"hash drift on {check_path!r}: file was modified after execute "
            f"(current sha256 differs from recorded after_hash). "
            f"Rolling back would lose those changes."
        )

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
                raise ValueError(f"backup_path escapes run dir: {entry.backup_path}") from exc
            if not backup_abs.exists():
                raise FileNotFoundError(f"backup not found: {entry.backup_path}")
            # Remove the new content, then move the backup back.
            if tgt.exists():
                tgt.unlink()
            tgt.parent.mkdir(parents=True, exist_ok=True)
            file_ops.move(backup_abs, tgt)
        else:
            raise ValueError(f"unknown rollback op: {entry.op}")
