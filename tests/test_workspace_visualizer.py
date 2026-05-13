"""Phase 8.2 / v0.8.2 — workspace_visualizer skill tests.

The skill counts files by either parent directory (when the workspace
is already organized into subfolders) or by file_type (when files are
flat), renders a real PNG via ``chart_ops.bar_png``, and writes a
markdown summary that references the chart.

These tests pin:

  * Skill registration via the standard contract test.
  * Plan structure: chart action carries valid base64-encoded PNG
    bytes; summary action carries markdown content; mkdir is emitted
    only when `images/` doesn't already exist.
  * Grouping mode picks 'folder' when ≥60% of files are in subdirs;
    'file_type' otherwise.
  * Empty workspace doesn't crash (the chart_ops library handles
    'no data' by drawing a placeholder).
  * End-to-end execute+verify+rollback restores the workspace.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path

from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.schemas import FileMeta, TaskSpec, WorkspaceSnapshot
from app.skills import get_default_registry, run_skill_contract
from app.skills.workspace_visualizer.planner import plan_workspace_visualization
from app.skills.workspace_visualizer.validator import (
    WorkspaceVisualizerValidationError,
    validate_workspace_visualizer_plan,
)
from app.storage.run_store import RunStore

# ---------------------------------------------------- helpers


def _snapshot(files: list[tuple[str, str]]) -> WorkspaceSnapshot:
    metas = [
        FileMeta(
            path=path,
            file_type=ftype,
            size_bytes=1,
            modified_at=datetime.now(timezone.utc),
        )
        for path, ftype in files
    ]
    return WorkspaceSnapshot(
        snapshot_id="snap-test",
        task_id="t-test",
        root="/fake",
        files=metas,
        total_files=len(metas),
        total_size_bytes=len(metas),
    )


def _task(workspace_root: str = "/fake") -> TaskSpec:
    return TaskSpec(
        task_id="t-test",
        user_goal="chart file counts",
        workspace_root=workspace_root,
        skill="workspace_visualizer",
        allowed_actions=["mkdir", "index"],
    )


# ---------------------------------------------------- registry


def test_skill_registered() -> None:
    reg = get_default_registry()
    assert "workspace_visualizer" in reg.list_names()
    sk = reg.require("workspace_visualizer")
    assert sk.manifest.name == "workspace_visualizer"
    assert "index" in sk.manifest.allowed_actions
    assert "mkdir" in sk.manifest.allowed_actions


def test_skill_does_not_support_llm() -> None:
    """The skill is intentionally rule-only — counts + matplotlib add no
    signal from an LLM. Pin this so it doesn't sneak in by accident."""
    sk = get_default_registry().require("workspace_visualizer")
    assert sk.supports_llm() is False


# ---------------------------------------------------- planner: grouping


def test_grouping_by_folder_when_files_are_organized() -> None:
    """If 60%+ of files live in subdirs, group by folder name."""
    snap = _snapshot(
        [
            ("papers/a.pdf", "pdf"),
            ("papers/b.pdf", "pdf"),
            ("images/c.png", "image"),
            ("images/d.jpg", "image"),
            ("notes/e.txt", "text"),
        ]
    )
    plan = plan_workspace_visualization(_task(), snap)
    chart_action = next(a for a in plan.actions if a.target_path and a.target_path.endswith(".png"))
    spec = chart_action.metadata["chart_spec"]
    assert spec["grouping"] == "folder"
    assert spec["groups"] == {"papers": 2, "images": 2, "notes": 1}


def test_grouping_by_file_type_when_flat() -> None:
    """Files at the root → group by file_type."""
    snap = _snapshot(
        [
            ("a.pdf", "pdf"),
            ("b.pdf", "pdf"),
            ("c.png", "image"),
            ("d.csv", "tabular"),
        ]
    )
    plan = plan_workspace_visualization(_task(), snap)
    chart_action = next(a for a in plan.actions if a.target_path and a.target_path.endswith(".png"))
    spec = chart_action.metadata["chart_spec"]
    assert spec["grouping"] == "file_type"
    assert spec["groups"] == {"pdf": 2, "image": 1, "tabular": 1}


def test_grouping_with_single_folder_falls_back_to_file_type() -> None:
    """If everything sits in a single subdir, grouping by 'folder' yields
    one bar — useless. Fall back to file_type so the chart is informative."""
    snap = _snapshot(
        [
            ("docs/a.pdf", "pdf"),
            ("docs/b.pdf", "pdf"),
            ("docs/c.png", "image"),
        ]
    )
    plan = plan_workspace_visualization(_task(), snap)
    chart_action = next(a for a in plan.actions if a.target_path and a.target_path.endswith(".png"))
    assert chart_action.metadata["chart_spec"]["grouping"] == "file_type"


# ---------------------------------------------------- planner: action shape


def test_chart_action_has_real_png_bytes() -> None:
    """The chart action's binary_content_b64 must base64-decode into a
    PNG (8-byte header check). This is the only test that proves the
    skill emits a *real* image, not a markdown fake."""
    snap = _snapshot([("a.pdf", "pdf"), ("b.pdf", "pdf")])
    plan = plan_workspace_visualization(_task(), snap)
    chart_action = next(a for a in plan.actions if a.target_path and a.target_path.endswith(".png"))
    encoded = chart_action.metadata["binary_content_b64"]
    raw = base64.b64decode(encoded)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "binary_content_b64 is not a PNG"
    assert len(raw) > 100, "PNG suspiciously small"


def test_summary_action_references_chart_path() -> None:
    snap = _snapshot([("a.pdf", "pdf")])
    plan = plan_workspace_visualization(_task(), snap)
    summary = next(a for a in plan.actions if a.target_path and a.target_path.endswith(".md"))
    assert "images/file_counts.png" in summary.metadata["content"]


def test_mkdir_emitted_when_images_dir_missing() -> None:
    """No existing `images/` in snapshot → planner emits an mkdir first."""
    snap = _snapshot([("a.pdf", "pdf")])
    plan = plan_workspace_visualization(_task(), snap)
    actions = list(plan.actions)
    assert actions[0].action_type.value == "mkdir"
    assert actions[0].target_path == "images"


def test_no_mkdir_when_images_dir_exists() -> None:
    """`images/` already in the snapshot → planner skips the mkdir."""
    snap = _snapshot([("images/old.png", "image"), ("a.pdf", "pdf")])
    plan = plan_workspace_visualization(_task(), snap)
    types = [a.action_type.value for a in plan.actions]
    assert "mkdir" not in types


def test_empty_workspace_still_produces_a_chart() -> None:
    """No files → planner still emits a placeholder chart, not zero actions.
    The chart_ops layer draws a 'no data' label so users see what
    happened instead of silently nothing."""
    snap = _snapshot([])
    plan = plan_workspace_visualization(_task(), snap)
    assert any(a.target_path and a.target_path.endswith(".png") for a in plan.actions)


# ---------------------------------------------------- validator


def test_validator_accepts_well_formed_plan() -> None:
    snap = _snapshot([("a.pdf", "pdf"), ("b.png", "image")])
    plan = plan_workspace_visualization(_task(), snap)
    validate_workspace_visualizer_plan(plan)  # should not raise


def test_validator_rejects_missing_png_action() -> None:
    """Sanity: if someone fork-forks the planner and accidentally drops
    the PNG action, validator catches it."""
    from app.schemas import ActionPlan
    from app.schemas.action import Action, ActionType, RiskLevel

    plan = ActionPlan(
        plan_id="p-bad",
        task_id="t",
        summary="missing png",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="summary.md",
                reason="markdown only",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "hi"},
            ),
        ],
    )
    try:
        validate_workspace_visualizer_plan(plan)
    except WorkspaceVisualizerValidationError as exc:
        assert "1 PNG chart action" in str(exc)
    else:
        raise AssertionError("validator should have rejected png-less plan")


# ---------------------------------------------------- end-to-end


def test_end_to_end_execute_writes_real_png(tmp_path: Path) -> None:
    """Full lifecycle on a real on-disk workspace — execute writes a
    PNG to images/file_counts.png and rollback removes it cleanly."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Plant a few files of different types.
    (workspace / "a.pdf").write_bytes(b"x")
    (workspace / "b.png").write_bytes(b"x")
    (workspace / "c.txt").write_text("x", encoding="utf-8")

    from app.tools.file_scan import scan_workspace

    snap = scan_workspace(workspace, task_id="t-test", compute_hash=False, compute_preview=False)

    run_store = RunStore.create()
    task = _task(workspace_root=str(workspace))
    run_store.save_task(task)
    run_store.save_workspace(snap)

    plan = plan_workspace_visualization(task, snap)
    validate_workspace_visualizer_plan(plan)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success, outcome.failed

    png_path = workspace / "images" / "file_counts.png"
    assert png_path.exists()
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    rb = Rollback(workspace_root=workspace, run_store=run_store)
    result = rb.run(outcome.manifest)
    assert result.success, result.failed
    assert not png_path.exists()
    assert not (workspace / "file_counts_summary.md").exists()


# ---------------------------------------------------- contract


def _seed_visualizer_workspace(root: Path) -> None:
    """WorkspaceSeeder for the standard skill contract test."""
    (root / "a.pdf").write_bytes(b"x")
    (root / "b.png").write_bytes(b"x")
    (root / "c.txt").write_text("x", encoding="utf-8")


def test_skill_contract(tmp_path: Path) -> None:
    """The 8-stage contract from `_contract.py` — every skill is held
    to this standard. Ensures lifecycle compatibility with the harness."""
    sk = get_default_registry().require("workspace_visualizer")
    workspace = tmp_path / "contract_ws"
    workspace.mkdir()
    run_store = RunStore.create()
    report = run_skill_contract(
        sk,
        workspace_seeder=_seed_visualizer_workspace,
        workspace_root=workspace,
        run_store=run_store,
    )
    assert all(s.passed for s in report.stages), report
