"""Phase 5 — folder_organizer.plan() applies naming_style preference."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.memory import NamingStyle
from app.schemas import TaskSpec
from app.skills.folder_organizer import FolderOrganizerSkill
from app.tools.file_scan import scan_workspace


@pytest.fixture()
def styled_workspace(tmp_path: Path) -> Path:
    """A workspace with intentionally messy filenames so naming-style
    transforms have something visible to do."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "Report (Final).pdf").write_text("a", encoding="utf-8")
    (root / "MY NOTES.txt").write_text("b", encoding="utf-8")
    (root / "data set v2.csv").write_text("c,d\n1,2\n", encoding="utf-8")
    return root


def _task(workspace: Path, *, naming_style: str) -> TaskSpec:
    return TaskSpec(
        task_id="t-naming",
        user_goal="organize",
        workspace_root=str(workspace),
        skill="folder_organizer",
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        preferences={"naming_style": naming_style} if naming_style != "original" else {},
    )


def _move_actions(plan):
    return [a for a in plan.actions if a.action_type.value == "move"]


def test_default_naming_style_preserves_filenames(styled_workspace: Path) -> None:
    """No preference set → planner emits move targets with original names."""
    task = _task(styled_workspace, naming_style="original")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    plan = FolderOrganizerSkill().plan(task, snap)
    targets = {a.target_path for a in _move_actions(plan)}
    assert "papers/Report (Final).pdf" in targets
    assert "notes/MY NOTES.txt" in targets
    assert "data/data set v2.csv" in targets


def test_snake_case_renames_during_categorize(styled_workspace: Path) -> None:
    task = _task(styled_workspace, naming_style="snake_case")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    plan = FolderOrganizerSkill().plan(task, snap)
    targets = {a.target_path for a in _move_actions(plan)}
    assert "papers/report_final.pdf" in targets
    assert "notes/my_notes.txt" in targets
    assert "data/data_set_v2.csv" in targets


def test_kebab_case_renames_during_categorize(styled_workspace: Path) -> None:
    task = _task(styled_workspace, naming_style="kebab-case")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    plan = FolderOrganizerSkill().plan(task, snap)
    targets = {a.target_path for a in _move_actions(plan)}
    assert "papers/report-final.pdf" in targets
    assert "notes/my-notes.txt" in targets


def test_renamed_action_reason_mentions_style(styled_workspace: Path) -> None:
    """When the preference actually changes the filename, the user
    should see WHY in the planned action's reason."""
    task = _task(styled_workspace, naming_style="snake_case")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    plan = FolderOrganizerSkill().plan(task, snap)
    renamed = [a for a in _move_actions(plan) if a.target_path != f"papers/{Path(a.source_path).name}"]
    # at least one of the moves actually got renamed
    assert any("naming style: snake_case" in a.reason for a in renamed)


def test_validator_accepts_renamed_plan(styled_workspace: Path) -> None:
    """folder_organizer.validate must still accept the plan when names
    have been transformed — naming style is allowed to change targets."""
    task = _task(styled_workspace, naming_style="snake_case")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    skill = FolderOrganizerSkill()
    plan = skill.plan(task, snap)
    skill.validate(plan)  # should not raise


def test_unknown_naming_style_falls_back_to_original(styled_workspace: Path) -> None:
    """A typo'd naming_style in task.preferences must not crash the
    planner — apply_naming_style returns original on unknown styles."""
    task = _task(styled_workspace, naming_style="kamelKase")
    snap = scan_workspace(styled_workspace, task.task_id, compute_hash=False)
    plan = FolderOrganizerSkill().plan(task, snap)
    targets = {a.target_path for a in _move_actions(plan)}
    assert "papers/Report (Final).pdf" in targets
