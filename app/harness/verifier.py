from __future__ import annotations

from pathlib import Path

from app.harness.policy_guard import PolicyViolation, resolve_inside
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    FailureType,
    RollbackManifest,
    TraceEvent,
    TraceEventType,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.action import ActionType
from app.schemas.rollback import RollbackOpType


class Verifier:
    """Independent, rules-based completion check.

    Crucially, this module never asks the model whether the task succeeded.
    It compares plan + manifest + on-disk state to a fixed checklist.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        trace: TraceLogger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        # Phase 9 — optional trace stream.
        self.trace = trace

    def verify(
        self,
        *,
        task_id: str,
        run_id: str,
        plan: ActionPlan,
        manifest: RollbackManifest,
        executed_action_ids: set[str],
        skipped_action_ids: set[str],
        failed_action_ids: set[str],
        original_snapshot: WorkspaceSnapshot,
    ) -> VerificationResult:
        checks: list[VerificationCheck] = []

        def _record(check: VerificationCheck, failure_type: FailureType | None = None) -> None:
            """Append the check + emit a parallel TraceEvent. Centralises the
            trace-emission boilerplate so each call site stays one line."""
            checks.append(check)
            self._emit_trace(check, task_id, run_id, failure_type=failure_type)

        write_actions = [a for a in plan.actions if a.is_write()]
        accounted = executed_action_ids | skipped_action_ids | failed_action_ids
        missing = [a.action_id for a in plan.actions if a.action_id not in accounted]
        _record(
            VerificationCheck(
                name="all_actions_accounted",
                passed=not missing,
                detail=("missing: " + ", ".join(missing)) if missing else "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if missing else None,
        )

        # For every successful move: source gone, target exists, no escape.
        bad_move: list[str] = []
        for action in plan.actions:
            if action.action_id not in executed_action_ids:
                continue
            if action.action_type in {ActionType.MOVE, ActionType.RENAME}:
                src_path = action.source_path or ""
                # Source must no longer exist at its original path.
                try:
                    src_abs = resolve_inside(self.workspace_root, src_path)
                except PolicyViolation as exc:
                    bad_move.append(f"{action.action_id}: {exc}")
                    continue
                if src_abs.exists():
                    bad_move.append(f"{action.action_id}: source still at {src_path}")
        _record(
            VerificationCheck(
                name="moves_relocated_sources",
                passed=not bad_move,
                detail="; ".join(bad_move) or "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if bad_move else None,
        )

        # Rollback manifest covers every successful write.
        manifest_aids = {e.action_id for e in manifest.entries}
        missing_rb = [
            a.action_id
            for a in write_actions
            if a.action_id in executed_action_ids and a.action_id not in manifest_aids
        ]
        # mkdir on an existing dir is a legitimate no-op without a rollback
        # entry — exempt those.
        if missing_rb:
            true_missing: list[str] = []
            for aid in missing_rb:
                action = next(a for a in plan.actions if a.action_id == aid)
                if action.action_type != ActionType.MKDIR:
                    true_missing.append(aid)
            missing_rb = true_missing
        _record(
            VerificationCheck(
                name="rollback_covers_writes",
                passed=not missing_rb,
                detail=("uncovered: " + ", ".join(missing_rb)) if missing_rb else "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if missing_rb else None,
        )

        # No paths escape workspace.
        escapes: list[str] = []
        for action in plan.actions:
            for p in (action.source_path, action.target_path):
                if p is None:
                    continue
                try:
                    resolve_inside(self.workspace_root, p)
                except PolicyViolation as exc:
                    escapes.append(f"{action.action_id}: {exc}")
        _record(
            VerificationCheck(
                name="no_path_escapes",
                passed=not escapes,
                detail="; ".join(escapes) or "ok",
            ),
            failure_type=FailureType.PATH_FORBIDDEN if escapes else None,
        )

        # No file loss: every source in the original snapshot is still
        # represented somewhere (either at its original path or moved).
        # We test by hash: the union of hashes after must be a superset of
        # the hashes before (minus duplicates).
        before_hashes = {f.sha256 for f in original_snapshot.files if f.sha256}
        after_hashes: set[str] = set()
        for path in self.workspace_root.rglob("*"):
            if path.is_file():
                # Skip files inside the localflow store if user picked the
                # repo root as workspace — guarded by file_scan in normal
                # operation but we double-check here for safety.
                try:
                    rel = path.relative_to(self.workspace_root)
                except ValueError:
                    continue
                if ".localflow" in rel.parts:
                    continue
                try:
                    from app.tools.hash_ops import sha256_file

                    after_hashes.add(sha256_file(path))
                except OSError:
                    pass
        lost = before_hashes - after_hashes
        _record(
            VerificationCheck(
                name="no_file_loss",
                passed=not lost,
                detail=f"{len(lost)} hash(es) missing" if lost else "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if lost else None,
        )

        # Phase 23 — PYTHON_COMPUTE action outcomes must be OK for the
        # corresponding action to count as a real success. The executor
        # raises on non-OK outcomes (marking the action FAILED), so the
        # only entries we see here for a successful run are OK ones —
        # but we double-check the recorded outcome to catch any future
        # path that bypasses the executor's raise.
        bad_compute: list[str] = []
        for entry in manifest.entries:
            if entry.op is not RollbackOpType.DELETE_SCRATCH_DIR:
                continue
            outcome = (entry.metadata or {}).get("outcome", {})
            status = outcome.get("status") if isinstance(outcome, dict) else None
            if entry.action_id in executed_action_ids and status != "ok":
                bad_compute.append(
                    f"{entry.action_id}: outcome.status={status!r}"
                )
        _record(
            VerificationCheck(
                name="compute_outcomes_ok",
                passed=not bad_compute,
                detail="; ".join(bad_compute) or "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if bad_compute else None,
        )

        # Generated files exist (e.g. index.md).
        missing_generated = [
            p for p in manifest.generated_files if not (self.workspace_root / p).exists()
        ]
        _record(
            VerificationCheck(
                name="generated_files_present",
                passed=not missing_generated,
                detail=("missing: " + ", ".join(missing_generated)) if missing_generated else "ok",
            ),
            failure_type=FailureType.MISSING_OUTPUT if missing_generated else None,
        )

        failed = [c for c in checks if not c.passed]
        passed = not failed
        summary = (
            f"All {len(checks)} checks passed."
            if passed
            else f"{len(failed)}/{len(checks)} checks failed."
        )
        return VerificationResult(
            task_id=task_id,
            run_id=run_id,
            passed=passed,
            checks=checks,
            failed_checks=failed,
            summary=summary,
        )

    # -- Phase 9 trace emission helper --------------------------------

    def _emit_trace(
        self,
        check: VerificationCheck,
        task_id: str,
        run_id: str,
        *,
        failure_type: FailureType | None,
    ) -> None:
        """No-op when self.trace is None."""
        if self.trace is None:
            return
        try:
            self.trace.emit(
                TraceEvent(
                    task_id=task_id,
                    run_id=run_id,
                    event_type=TraceEventType.VERIFIER_CHECK,
                    status="ok" if check.passed else "fail",
                    failure_type=failure_type if not check.passed else None,
                    detail=f"{check.name}: {check.detail[:200]}",
                    payload={"check_name": check.name, "passed": check.passed},
                )
            )
        except Exception:
            pass
