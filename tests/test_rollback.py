from __future__ import annotations

from pathlib import Path

from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.skills.folder_organizer.planner import plan_organization
from app.tools.hash_ops import sha256_file


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in root.rglob("*") if p.is_file()}


def test_rollback_restores_hashes(workspace: Path, task, snapshot, run_store) -> None:
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    before = _hashes(workspace)

    plan = plan_organization(task, snapshot)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success

    after_exec = _hashes(workspace)
    assert after_exec != before  # something moved

    rollback = Rollback(workspace_root=workspace, run_store=run_store)
    result = rollback.run(outcome.manifest)
    assert result.success, result.failed

    after_rb = _hashes(workspace)
    # Generated files (index.md, duplicates_report.md) should be gone.
    generated = set(outcome.manifest.generated_files)
    for g in generated:
        assert g not in after_rb
    # Original content hashes must all be present after rollback.
    original_hashes = set(before.values())
    restored_hashes = set(after_rb.values())
    assert original_hashes <= restored_hashes


def test_overwrite_existing_backs_up_then_rollback_restores(
    workspace: Path, task, snapshot, run_store
) -> None:
    """Phase 3.1b: when an index action sets metadata.overwrite_existing=True
    and the target already exists, executor backs up the original into
    the run's backups/ dir and writes new content at the original path.
    Rollback then restores the backup byte-for-byte."""
    from app.schemas import ActionPlan
    from app.schemas.action import Action, ActionType, RiskLevel

    # Plant an existing report on disk (e.g. left over from a previous run).
    original_content = "# Original report\n\nThis was here before.\n"
    target = workspace / "report.md"
    target.write_text(original_content, encoding="utf-8")
    # On Windows write_text adds \r\n; re-read to capture canonical bytes
    # rather than comparing across the newline-translation boundary.
    original_bytes = target.read_bytes()

    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = ActionPlan(
        plan_id="p-overwrite",
        task_id=task.task_id,
        summary="Overwrite the existing report.",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="regenerate",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
                metadata={
                    "content": "# Updated report\n\nNew content here.\n",
                    "overwrite_existing": True,
                },
            ),
        ],
    )
    run_store.save_plan(plan)

    # Execute — should overwrite the original AND back it up.
    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    assert target.read_text(encoding="utf-8") == "# Updated report\n\nNew content here.\n"
    # The backup file should exist in the run's backups/ dir.
    backups_dir = run_store.backups_dir
    assert backups_dir.exists()
    backups = list(backups_dir.iterdir())
    assert len(backups) == 1, f"expected exactly 1 backup, got {backups}"
    # Byte-identical: the backup is the original file moved as-is, no
    # text translation. This is the safety contract.
    assert backups[0].read_bytes() == original_bytes

    # Rollback — must restore the ORIGINAL content, not just delete.
    rollback = Rollback(workspace_root=workspace, run_store=run_store)
    result = rollback.run(outcome.manifest)
    assert result.success, result.failed
    assert target.read_bytes() == original_bytes


def test_no_backup_when_target_does_not_exist(workspace: Path, task, snapshot, run_store) -> None:
    """If the target file is brand new (no prior version), overwrite_existing
    must NOT create an empty backup — it's a no-op compared to the default
    write path. Rollback then deletes the new file rather than restoring."""
    from app.schemas import ActionPlan
    from app.schemas.action import Action, ActionType, RiskLevel

    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = ActionPlan(
        plan_id="p-fresh",
        task_id=task.task_id,
        summary="First write of a report.",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="fresh_report.md",
                reason="first write",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
                metadata={"content": "fresh content", "overwrite_existing": True},
            ),
        ],
    )
    run_store.save_plan(plan)
    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    assert (workspace / "fresh_report.md").exists()
    # No backup should have been created since there was nothing to back up.
    assert list(run_store.backups_dir.iterdir()) == []
    # Rollback simply deletes the new file.
    rollback = Rollback(workspace_root=workspace, run_store=run_store)
    result = rollback.run(outcome.manifest)
    assert result.success
    assert not (workspace / "fresh_report.md").exists()


def test_overwrite_existing_false_uses_safe_target(
    workspace: Path, task, snapshot, run_store
) -> None:
    """Backwards compatibility: without the overwrite_existing flag,
    safe_target adds (1) suffix to avoid clobbering — outline §7.6 default."""
    from app.schemas import ActionPlan
    from app.schemas.action import Action, ActionType, RiskLevel

    (workspace / "report.md").write_text("ORIG", encoding="utf-8")
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = ActionPlan(
        plan_id="p-safe",
        task_id=task.task_id,
        summary="Write without overwrite flag.",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="no overwrite",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
                metadata={"content": "NEW"},  # NO overwrite_existing flag
            ),
        ],
    )
    run_store.save_plan(plan)
    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    # Original untouched, new file got the (1) suffix.
    assert (workspace / "report.md").read_text(encoding="utf-8") == "ORIG"
    assert (workspace / "report (1).md").read_text(encoding="utf-8") == "NEW"


def test_rollback_sweeps_empty_subdirs_from_nested_targets(
    workspace: Path, task, snapshot, run_store
) -> None:
    """Regression: when a move's target is a NESTED path inside a newly
    created dir (e.g. ``subdir/foo → newdir/subdir/foo``), the executor's
    parent.mkdir(parents=True) implicitly creates ``newdir/subdir/``.
    That intermediate dir wasn't recorded in the manifest as its own
    action, so a naive rollback left an empty-but-non-empty parent that
    blocked DELETE_CREATED_DIR. The fix: rollback sweeps empty subdirs
    before refusing to delete a created_dir."""
    from app.schemas import ActionPlan
    from app.schemas.action import Action, ActionType, RiskLevel

    run_store.save_task(task)
    run_store.save_workspace(snapshot)

    # Craft a small plan with a NESTED move target — exactly the LLM
    # pattern that produced the bug (subdir/helper.py -> codebase/subdir/helper.py).
    plan = ActionPlan(
        plan_id="p-nested",
        task_id=task.task_id,
        summary="Move a file into a nested location under a new dir.",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MKDIR,
                target_path="newdir",
                reason="create parent.",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            ),
            Action(
                action_id="a-002",
                action_type=ActionType.MOVE,
                source_path="subdir/a_copy.pdf",
                # Nested target — newdir/inner/ does NOT exist yet, the
                # executor will create it implicitly.
                target_path="newdir/inner/a_copy.pdf",
                reason="nested move.",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            ),
        ],
    )
    run_store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success

    # Sanity: nested target created, file moved in, intermediate dir exists.
    assert (workspace / "newdir" / "inner" / "a_copy.pdf").exists()
    assert (workspace / "newdir" / "inner").is_dir()

    rollback = Rollback(workspace_root=workspace, run_store=run_store)
    result = rollback.run(outcome.manifest)
    assert result.success, (
        f"rollback should succeed even with implicit intermediate dirs, "
        f"got failures: {result.failed}"
    )
    # Everything created by the run must be gone.
    assert not (workspace / "newdir").exists()
    # Original file is back at its original location.
    assert (workspace / "subdir" / "a_copy.pdf").exists()


def test_rollback_refuses_to_remove_nonempty_dir(
    workspace: Path, task, snapshot, run_store
) -> None:
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = plan_organization(task, snapshot)
    run_store.save_plan(plan)
    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)

    # Plant an extra file inside one of the created dirs so the rollback's
    # remove_empty_dir step refuses.
    created = outcome.manifest.created_dirs
    if not created:
        return  # nothing to test on
    (workspace / created[0] / "extra.txt").write_text("user-data", encoding="utf-8")

    rollback = Rollback(workspace_root=workspace, run_store=run_store)
    result = rollback.run(outcome.manifest)
    # Should be partial: extra file's presence blocks dir removal.
    assert not result.success
    assert (workspace / created[0] / "extra.txt").exists()  # user data preserved


# --------------------------------------------------------------- v0.7.3 regression


def test_rollback_preview_has_entry_count_property() -> None:
    """v0.7.3 regression: the UI Rollback page reads
    ``preview.entry_count``; an earlier release exposed only
    ``preview.entries`` so the page crashed with AttributeError. Pin the
    property here so it never regresses."""
    from app.harness.rollback import RollbackPreview

    pv = RollbackPreview(run_id="x")
    assert pv.entry_count == 0
    pv.entries.append({"action_id": "a1"})
    pv.entries.append({"action_id": "a2"})
    assert pv.entry_count == 2
