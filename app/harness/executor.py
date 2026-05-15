from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.harness.audit import AuditLogger
from app.harness.checkpoint import completed_action_ids
from app.harness.policy_guard import PolicyViolation, evaluate_action, resolve_inside
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    ExecutionRecord,
    ExecutionStatus,
    FailureType,
    RollbackEntry,
    RollbackManifest,
    TraceEvent,
    TraceEventType,
)
from app.schemas.action import Action, ActionType
from app.schemas.rollback import RollbackOpType
from app.storage.jsonl_logger import JsonlLogger
from app.storage.run_store import RunStore
from app.tools import file_ops
from app.tools.hash_ops import sha256_file


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ExecutionOutcome:
    run_id: str
    records: list[ExecutionRecord]
    manifest: RollbackManifest
    success: bool


class Executor:
    """Runs an ActionPlan against the real filesystem under harness controls.

    Guarantees:
      * Every action is policy-checked at execution time (defense in depth
        even after the plan-level RiskAssessment).
      * Every successful write produces a rollback entry.
      * The execution log is appended *before and after* each action so a
        crash leaves enough trail for ``completed_action_ids`` to resume.
    """

    def __init__(
        self,
        workspace_root: Path,
        run_store: RunStore,
        forbidden_actions: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        *,
        trace: TraceLogger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.run_store = run_store
        self.forbidden_actions = forbidden_actions
        self.forbidden_paths = forbidden_paths
        self.exec_log = JsonlLogger(run_store.execution_log_path)
        self.audit = AuditLogger(run_store.audit_log_path)
        # Phase 9 — optional trace stream. None = no-op (back-compat
        # with v0.9.1 callers; library tests that don't care about
        # trace see identical behaviour).
        self.trace = trace

    def execute(
        self,
        plan: ActionPlan,
        *,
        approved: bool,
        resume: bool = False,
    ) -> ExecutionOutcome:
        if not approved:
            raise RuntimeError("Executor refused: plan not approved")

        already_done = completed_action_ids(self.exec_log) if resume else set()
        run_id = self.run_store.task_id

        # Load any prior manifest so we keep the rollback entries from
        # earlier (partial) executions when resuming.
        if resume and self.run_store.rollback_path.exists():
            manifest = self.run_store.load_rollback()
        else:
            manifest = RollbackManifest(run_id=run_id, task_id=plan.task_id)

        records: list[ExecutionRecord] = []
        self.audit.log("execute.start", run_id=run_id, plan_id=plan.plan_id, resume=resume)

        all_ok = True
        for action in plan.actions:
            if action.action_id in already_done:
                self.exec_log.write(
                    "action.skip",
                    {"action_id": action.action_id, "reason": "checkpoint"},
                )
                records.append(
                    ExecutionRecord(
                        run_id=run_id,
                        action_id=action.action_id,
                        status=ExecutionStatus.SKIPPED,
                    )
                )
                continue

            # Defense in depth: re-check policy at execute time.
            decision = evaluate_action(
                self.workspace_root,
                action,
                forbidden_actions=self.forbidden_actions,
                forbidden_paths=self.forbidden_paths,
            )
            if not decision.allowed:
                err = "; ".join(decision.reasons)
                self.exec_log.write(
                    "action.end",
                    {
                        "action_id": action.action_id,
                        "status": ExecutionStatus.FAILED.value,
                        "error": f"policy_violation: {err}",
                    },
                )
                self._emit_trace(
                    TraceEventType.POLICY_CHECK,
                    status="blocked",
                    failure_type=_classify_policy_reason(decision.reasons),
                    action_id=action.action_id,
                    detail=err,
                    payload={"task_id": plan.task_id, "reasons": list(decision.reasons)},
                )
                records.append(
                    ExecutionRecord(
                        run_id=run_id,
                        action_id=action.action_id,
                        status=ExecutionStatus.FAILED,
                        ended_at=_utcnow(),
                        error=f"policy_violation: {err}",
                    )
                )
                all_ok = False
                continue

            record = self._run_one(action, run_id, manifest)
            records.append(record)
            if record.status == ExecutionStatus.FAILED:
                all_ok = False

        self.run_store.save_rollback(manifest)
        self.run_store.write_json(
            self.run_store.actions_path,
            [r.model_dump(mode="json") for r in records],
        )
        self.audit.log("execute.end", run_id=run_id, success=all_ok, total=len(records))
        return ExecutionOutcome(run_id=run_id, records=records, manifest=manifest, success=all_ok)

    # -- per-action dispatch ------------------------------------------

    def _run_one(
        self,
        action: Action,
        run_id: str,
        manifest: RollbackManifest,
    ) -> ExecutionRecord:
        started = _utcnow()
        self.exec_log.write(
            "action.start",
            {
                "action_id": action.action_id,
                "action_type": action.action_type.value,
                "source": action.source_path,
                "target": action.target_path,
                "started_at": started.isoformat(),
            },
        )
        self._emit_trace(
            TraceEventType.ACTION_START,
            action_id=action.action_id,
            detail=f"{action.action_type.value} {action.target_path or ''}",
            payload={
                "action_type": action.action_type.value,
                "source": action.source_path,
                "target": action.target_path,
            },
        )
        try:
            hash_before, hash_after, rb = self._dispatch(action, manifest)
        except Exception as exc:
            ended = _utcnow()
            self.exec_log.write(
                "action.end",
                {
                    "action_id": action.action_id,
                    "status": ExecutionStatus.FAILED.value,
                    "ended_at": ended.isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self._emit_trace(
                TraceEventType.ACTION_END,
                status="fail",
                action_id=action.action_id,
                duration_ms=_duration_ms(started, ended),
                failure_type=FailureType.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return ExecutionRecord(
                run_id=run_id,
                action_id=action.action_id,
                status=ExecutionStatus.FAILED,
                started_at=started,
                ended_at=ended,
                error=f"{type(exc).__name__}: {exc}",
            )

        if rb is not None:
            manifest.entries.append(rb)
        ended = _utcnow()
        self.exec_log.write(
            "action.end",
            {
                "action_id": action.action_id,
                "status": ExecutionStatus.SUCCESS.value,
                "ended_at": ended.isoformat(),
                "hash_before": hash_before,
                "hash_after": hash_after,
            },
        )
        self._emit_trace(
            TraceEventType.ACTION_END,
            status="ok",
            action_id=action.action_id,
            duration_ms=_duration_ms(started, ended),
            detail=f"{action.action_type.value} ok",
            payload={
                "hash_before": hash_before,
                "hash_after": hash_after,
            },
        )
        return ExecutionRecord(
            run_id=run_id,
            action_id=action.action_id,
            status=ExecutionStatus.SUCCESS,
            started_at=started,
            ended_at=ended,
            file_hash_before=hash_before,
            file_hash_after=hash_after,
            rollback_action=rb.model_dump(mode="json") if rb else None,
        )

    def _dispatch(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry | None]:
        atype = action.action_type
        if atype == ActionType.MKDIR:
            return self._do_mkdir(action, manifest)
        if atype == ActionType.MOVE or atype == ActionType.RENAME:
            return self._do_move(action, manifest)
        if atype == ActionType.COPY:
            return self._do_copy(action, manifest)
        if atype == ActionType.INDEX:
            return self._do_index(action, manifest)
        if atype == ActionType.SUMMARIZE:
            return self._do_index(action, manifest)
        # CONVERT / ANALYZE not supported in Phase 0.
        raise NotImplementedError(f"action_type {atype.value} not implemented in Phase 0")

    def _do_mkdir(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, None, RollbackEntry | None]:
        target_abs = resolve_inside(self.workspace_root, action.target_path or "")
        created = file_ops.mkdir(target_abs)
        if not created:
            return None, None, None
        rel = self._rel(target_abs)
        manifest.created_dirs.append(rel)
        return (
            None,
            None,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_DIR,
                target_path=rel,
            ),
        )

    def _do_move(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry]:
        source_abs = resolve_inside(self.workspace_root, action.source_path or "")
        if not source_abs.exists():
            raise FileNotFoundError(f"source missing: {action.source_path}")
        target_abs = resolve_inside(self.workspace_root, action.target_path or "")
        chosen = file_ops.safe_target(target_abs)
        hash_before = sha256_file(source_abs) if source_abs.is_file() else None
        manifest.file_hashes_before[self._rel(source_abs)] = hash_before or ""
        final = file_ops.move(source_abs, chosen)
        hash_after = sha256_file(final) if final.is_file() else None
        return (
            hash_before,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.MOVE_BACK,
                source_path=self._rel(final),
                target_path=self._rel(source_abs),
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _do_copy(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry]:
        source_abs = resolve_inside(self.workspace_root, action.source_path or "")
        if not source_abs.exists():
            raise FileNotFoundError(f"source missing: {action.source_path}")
        target_abs = resolve_inside(self.workspace_root, action.target_path or "")
        chosen = file_ops.safe_target(target_abs)
        hash_before = sha256_file(source_abs) if source_abs.is_file() else None
        final = file_ops.copy(source_abs, chosen)
        hash_after = sha256_file(final) if final.is_file() else None
        rel = self._rel(final)
        manifest.generated_files.append(rel)
        return (
            hash_before,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_FILE,
                target_path=rel,
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _do_index(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, str | None, RollbackEntry]:
        target_abs = resolve_inside(self.workspace_root, action.target_path or "")
        overwrite = bool(action.metadata.get("overwrite_existing", False))

        # Phase 3.2: ``index`` actions can carry binary payloads (e.g.
        # PNG charts from chart_ops). The base64 encoding keeps plan.json
        # JSON-safe; we decode here and write via write_bytes. The
        # rollback semantics are identical to text writes — same backup
        # / restore / delete logic, just bytes instead of text.
        binary_b64 = action.metadata.get("binary_content_b64")
        if binary_b64 is not None:
            import base64

            try:
                payload_bytes: bytes = base64.b64decode(binary_b64)
            except Exception as exc:
                raise ValueError(
                    f"action {action.action_id}: binary_content_b64 is not valid base64: {exc}"
                ) from exc
            writer = lambda p: file_ops.write_bytes(p, payload_bytes)  # noqa: E731
        else:
            content_text: str = action.metadata.get("content", "")
            writer = lambda p: file_ops.write_text(p, content_text)  # noqa: E731

        # Phase 3.2: track parent dirs that *this* action will implicitly
        # create via write_text/write_bytes's `parents=True` mkdir.
        # Without this, rollback deletes the file but leaves an empty
        # parent dir (e.g. ``charts/`` from chart actions) hanging around.
        # We record entries for each implicitly-created level BEFORE the
        # file entry so reverse-iteration removes the file first, then
        # the dir(s) inner-to-outer.
        self._record_implicit_parents(target_abs, action.action_id, manifest)

        if overwrite and target_abs.is_file():
            # Outline §13.3 "compensation strategy" for writes that aren't
            # purely additive: move the existing file into the run's
            # backups/ directory, then write the new content at the
            # original path. Rollback's RESTORE_FROM_BACKUP undoes both
            # steps atomically — even bytewise-identical restoration.
            backup_filename = f"{action.action_id}__{target_abs.name}"
            backup_abs = self.run_store.backups_dir / backup_filename
            backup_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target_abs), str(backup_abs))
            writer(target_abs)
            hash_after = sha256_file(target_abs) if target_abs.is_file() else None
            rel = self._rel(target_abs)
            manifest.generated_files.append(rel)
            return (
                None,
                hash_after,
                RollbackEntry(
                    action_id=action.action_id,
                    op=RollbackOpType.RESTORE_FROM_BACKUP,
                    target_path=rel,
                    backup_path=str(backup_abs.relative_to(self.run_store.run_dir).as_posix()),
                    metadata={"after_hash": hash_after} if hash_after else {},
                ),
            )

        # Default (and the path for first-time writes): refuse to clobber.
        # ``safe_target`` auto-suffixes so we never silently overwrite.
        chosen = file_ops.safe_target(target_abs)
        writer(chosen)
        hash_after = sha256_file(chosen) if chosen.is_file() else None
        rel = self._rel(chosen)
        manifest.generated_files.append(rel)
        return (
            None,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_FILE,
                target_path=rel,
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _record_implicit_parents(
        self, target_abs: Path, action_id: str, manifest: RollbackManifest
    ) -> None:
        """Walk from target's parent upward until we hit an existing dir
        (or workspace_root). Each non-existent level gets a
        DELETE_CREATED_DIR rollback entry. Outer-most first in execution
        order, so reverse-rollback removes inner before outer.

        Skips silently if ``target_abs.parent`` already exists or if the
        target is at the workspace root itself.
        """
        try:
            workspace_root = self.workspace_root.resolve()
        except OSError:
            return
        new_dirs: list[Path] = []
        cursor = target_abs.parent
        while True:
            try:
                resolved = cursor.resolve()
            except OSError:
                break
            if resolved == workspace_root:
                break
            if cursor.exists():
                break
            new_dirs.append(cursor)
            cursor = cursor.parent
        new_dirs.reverse()  # outermost first → execution order
        for d in new_dirs:
            rel_d = self._rel(d)
            manifest.created_dirs.append(rel_d)
            manifest.entries.append(
                RollbackEntry(
                    action_id=action_id,
                    op=RollbackOpType.DELETE_CREATED_DIR,
                    target_path=rel_d,
                )
            )

    def _rel(self, abs_path: Path) -> str:
        try:
            return abs_path.resolve().relative_to(self.workspace_root).as_posix()
        except ValueError as exc:
            raise PolicyViolation(f"path outside workspace: {abs_path}") from exc

    # -- Phase 9 trace emission helper --------------------------------

    def _emit_trace(
        self,
        event_type: TraceEventType,
        *,
        status: str = "ok",
        failure_type: FailureType | None = None,
        action_id: str | None = None,
        duration_ms: int | None = None,
        detail: str = "",
        payload: dict | None = None,
    ) -> None:
        """No-op when self.trace is None (Phase 9 additive-only rule).

        The trace stream must never raise into the executor's hot path —
        a malformed event should drop on the floor rather than fail an
        action. ``run_id`` and ``task_id`` come from run_store.
        """
        if self.trace is None:
            return
        try:
            self.trace.emit(
                TraceEvent(
                    task_id=self.run_store.task_id,
                    run_id=self.run_store.task_id,
                    event_type=event_type,
                    status=status,  # type: ignore[arg-type]
                    failure_type=failure_type,
                    action_id=action_id,
                    duration_ms=duration_ms,
                    detail=detail[:500],  # cap; eval reports don't need full traces
                    payload=payload or {},
                )
            )
        except Exception:
            # Defensive — trace emission must never break execution.
            pass


def _duration_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


def _classify_policy_reason(reasons: list[str]) -> FailureType:
    """Map policy_guard reason strings onto FailureType buckets so eval
    histograms can separate `path_forbidden` (user-set forbidden_paths
    hit) from generic `policy_blocked` (forbidden action type, etc.)."""
    joined = " ".join(reasons).lower()
    if "forbidden_path" in joined or "forbidden path" in joined:
        return FailureType.PATH_FORBIDDEN
    return FailureType.POLICY_BLOCKED
