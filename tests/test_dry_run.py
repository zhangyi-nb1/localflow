from __future__ import annotations

from pathlib import Path

from app.harness import control_loop
from app.harness.dry_run import render_dry_run_markdown
from app.harness.policy_guard import assess_plan
from app.skills.folder_organizer.planner import plan_organization


def _snapshot_file_set(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_dry_run_is_pure(workspace: Path, task, snapshot) -> None:
    before = _snapshot_file_set(workspace)
    plan = plan_organization(task, snapshot)
    assessment = assess_plan(workspace, plan)
    md = render_dry_run_markdown(plan, workspace, assessment)
    assert "Dry-run preview" in md
    assert "Actions" in md
    # Crucial: dry-run must not touch the filesystem.
    after = _snapshot_file_set(workspace)
    assert before == after


def test_dry_run_lists_each_action(workspace: Path, task, snapshot) -> None:
    plan = plan_organization(task, snapshot)
    assessment = assess_plan(workspace, plan)
    md = render_dry_run_markdown(plan, workspace, assessment)
    # Each action_id should appear in the markdown table or be reflected
    # by its source/target paths.
    for action in plan.actions:
        marker = action.target_path or action.source_path or action.action_id
        assert marker in md, f"action {action.action_id} ({marker}) missing from dry-run"


def test_dry_run_run_persists_markdown(workspace: Path, task, snapshot, run_store) -> None:
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = plan_organization(task, snapshot)
    run_store.save_plan(plan)
    assessment = control_loop.run_risk_check(task, plan)
    md = control_loop.run_dry_run(task, plan, assessment, run_store)
    assert run_store.dry_run_path.exists()
    assert run_store.dry_run_path.read_text(encoding="utf-8") == md
