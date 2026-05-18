"""v0.14.1 — folder_organizer's review/ dir routing for low-confidence files."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import FileMeta, TaskSpec, WorkspaceSnapshot
from app.skills.folder_organizer.planner import (
    PREFERENCE_REVIEW_KEY,
    REVIEW_DIR,
    REVIEW_REPORT_NAME,
    plan_organization,
)


def _snap(*items: tuple[str, str]) -> WorkspaceSnapshot:
    files = [
        FileMeta(
            path=path,
            file_type=ftype,
            size_bytes=10,
            modified_at=datetime.now(timezone.utc),
        )
        for path, ftype in items
    ]
    return WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t",
        root="/tmp/ws",
        files=files,
        total_files=len(files),
        total_size_bytes=10 * len(files),
    )


def _task(prefs: dict | None = None) -> TaskSpec:
    return TaskSpec(
        task_id="t-1",
        user_goal="organize",
        workspace_root="/tmp/ws",
        skill="folder_organizer",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences=prefs or {},
    )


def test_other_files_default_to_misc_when_pref_off() -> None:
    """Back-compat: without route_low_confidence_to_review=True, an
    ``other``-classified file routes to misc/ (the v0.14.0 behaviour)."""
    snap = _snap(("weird.dat", "other"))
    plan = plan_organization(_task(), snap)
    moves = [a for a in plan.actions if a.action_type.value == "move"]
    assert len(moves) == 1
    assert moves[0].target_path == "misc/weird.dat"
    # No review report.
    assert not any(a.target_path == REVIEW_REPORT_NAME for a in plan.actions)


def test_pref_routes_other_files_to_review_dir() -> None:
    """When the preference is set, ``other``-classified files divert
    to review/ instead of misc/, and the planner emits an unresolved
    files report."""
    snap = _snap(("weird.dat", "other"), ("paper.pdf", "pdf"))
    plan = plan_organization(_task({PREFERENCE_REVIEW_KEY: True}), snap)
    moves = {a.source_path: a.target_path for a in plan.actions if a.action_type.value == "move"}
    assert moves["weird.dat"] == f"{REVIEW_DIR}/weird.dat"
    assert moves["paper.pdf"] == "papers/paper.pdf"  # pdf still goes to papers/
    report_actions = [a for a in plan.actions if a.target_path == REVIEW_REPORT_NAME]
    assert len(report_actions) == 1
    assert "weird.dat" in report_actions[0].metadata.get("content", "")


def test_pref_emits_no_review_report_when_no_low_confidence_files() -> None:
    """Edge case: pref ON but workspace has no low-confidence files
    (all extensions recognised) → no review report action emitted."""
    snap = _snap(("paper.pdf", "pdf"), ("notes.txt", "text"))
    plan = plan_organization(_task({PREFERENCE_REVIEW_KEY: True}), snap)
    assert not any(a.target_path == REVIEW_REPORT_NAME for a in plan.actions)
