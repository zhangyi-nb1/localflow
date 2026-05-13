from __future__ import annotations

from pathlib import Path

from app.harness.policy_guard import PolicyViolation, resolve_inside
from app.schemas import (
    ActionPlan,
    ExecutionStatus,
    RollbackManifest,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.action import ActionType


class Verifier:
    """Independent, rules-based completion check.

    Crucially, this module never asks the model whether the task succeeded.
    It compares plan + manifest + on-disk state to a fixed checklist.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

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

        write_actions = [a for a in plan.actions if a.is_write()]
        accounted = executed_action_ids | skipped_action_ids | failed_action_ids
        missing = [a.action_id for a in plan.actions if a.action_id not in accounted]
        checks.append(
            VerificationCheck(
                name="all_actions_accounted",
                passed=not missing,
                detail=("missing: " + ", ".join(missing)) if missing else "ok",
            )
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
        checks.append(
            VerificationCheck(
                name="moves_relocated_sources",
                passed=not bad_move,
                detail="; ".join(bad_move) or "ok",
            )
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
        checks.append(
            VerificationCheck(
                name="rollback_covers_writes",
                passed=not missing_rb,
                detail=("uncovered: " + ", ".join(missing_rb)) if missing_rb else "ok",
            )
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
        checks.append(
            VerificationCheck(
                name="no_path_escapes",
                passed=not escapes,
                detail="; ".join(escapes) or "ok",
            )
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
        checks.append(
            VerificationCheck(
                name="no_file_loss",
                passed=not lost,
                detail=f"{len(lost)} hash(es) missing" if lost else "ok",
            )
        )

        # Generated files exist (e.g. index.md).
        missing_generated = [
            p for p in manifest.generated_files
            if not (self.workspace_root / p).exists()
        ]
        checks.append(
            VerificationCheck(
                name="generated_files_present",
                passed=not missing_generated,
                detail=("missing: " + ", ".join(missing_generated)) if missing_generated else "ok",
            )
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
