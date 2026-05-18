"""v0.14.1 — source ledger construction.

Builds a typed :class:`~app.schemas.SourceLedger` from a workspace.
Two construction paths:

  * :func:`build_from_workspace` — pure filesystem scan; no run context.
    Used by ``localflow ledger build <path>`` for ad-hoc inventories.
  * :func:`build_from_run` — walks a finished run's plan + manifest +
    workspace; classifies each entry by ``role`` (seed / moved /
    generated) based on the plan actions. Used by the agent's
    synthesis stage in Phase 14 + by ``localflow ledger build
    --task-id <id>``.

Both paths produce the same :class:`SourceLedger` shape so consumers
don't need to branch on construction source.
"""

from __future__ import annotations

from pathlib import Path

from app.schemas import (
    ActionPlan,
    RollbackManifest,
    SourceEntry,
    SourceLedger,
    WorkspaceSnapshot,
)
from app.schemas.action import ActionType


def build_from_workspace(
    workspace_root: Path,
    *,
    task_id: str | None = None,
    compute_hash: bool = True,
) -> SourceLedger:
    """Walk the workspace top-down + produce a flat ledger.

    The category for each entry is derived from the file's top-level
    directory (so ``papers/index.md`` lands in ``papers``; a file at
    the root has ``category=None``). Every entry is tagged ``role="moved"``
    by default — without a plan context the ledger can't distinguish
    seed/moved/generated. Use :func:`build_from_run` when you have one.
    """
    from app.tools.file_scan import scan_workspace

    snap = scan_workspace(
        workspace_root,
        task_id=task_id or "ledger",
        compute_hash=compute_hash,
        compute_preview=False,
    )
    return _ledger_from_snapshot(snap, workspace_root, task_id=task_id)


def build_from_run(
    workspace_root: Path,
    *,
    seed_snapshot: WorkspaceSnapshot,
    plan: ActionPlan,
    manifest: RollbackManifest,
    task_id: str,
) -> SourceLedger:
    """Walk the current workspace + classify each entry by role using
    the run's plan + manifest.

    ``seed_snapshot`` is the pre-execute snapshot — its file set
    defines what "seed" means. The plan's MOVE/RENAME actions
    determine which files were moved; everything else that ended up
    on disk and wasn't in the seed is treated as ``generated``.
    """
    from app.tools.file_scan import scan_workspace

    post = scan_workspace(
        workspace_root,
        task_id=task_id,
        compute_hash=True,
        compute_preview=False,
    )

    seed_paths = {f.path for f in seed_snapshot.files}
    move_targets: dict[str, str] = {}
    for action in plan.actions:
        if action.action_type in (ActionType.MOVE, ActionType.RENAME):
            if action.source_path and action.target_path:
                move_targets[action.target_path] = action.source_path

    entries: list[SourceEntry] = []
    for f in sorted(post.files, key=lambda x: x.path):
        category = f.path.split("/", 1)[0] if "/" in f.path else None
        if f.path in seed_paths:
            role = "seed"
        elif f.path in move_targets:
            role = "moved"
        else:
            role = "generated"
        entries.append(
            SourceEntry(
                path=f.path,
                file_type=f.file_type,
                size_bytes=f.size_bytes,
                sha256=f.sha256,
                category=category,
                role=role,
            )
        )

    return SourceLedger(
        ledger_schema_version=1,
        task_id=task_id,
        workspace_root=str(workspace_root),
        entries=entries,
    )


def _ledger_from_snapshot(
    snap: WorkspaceSnapshot,
    workspace_root: Path,
    *,
    task_id: str | None,
) -> SourceLedger:
    entries: list[SourceEntry] = []
    for f in sorted(snap.files, key=lambda x: x.path):
        category = f.path.split("/", 1)[0] if "/" in f.path else None
        entries.append(
            SourceEntry(
                path=f.path,
                file_type=f.file_type,
                size_bytes=f.size_bytes,
                sha256=f.sha256,
                category=category,
                role="moved",  # unknown without a plan; conservative default
            )
        )
    return SourceLedger(
        task_id=task_id,
        workspace_root=str(workspace_root),
        entries=entries,
    )
