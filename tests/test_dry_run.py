from __future__ import annotations

from pathlib import Path

from app.harness import control_loop
from app.harness.dry_run import render_dry_run_markdown, simulate_action
from app.harness.policy_guard import assess_plan
from app.schemas import ActionPlan, RiskAssessment
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    SandboxPolicy,
)
from app.schemas.risk import RiskVerdict
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


def _make_compute_plan(*, with_script: str = "print('hi')") -> ActionPlan:
    compute = ComputeAction(
        script=with_script,
        script_summary="normalise sales_dirty.csv and emit cleaned.csv + report.json",
        inputs=[ComputeInputRef(rel_path="sales_dirty.csv", size_bytes=1024)],
        expected_outputs=[
            ArtifactSpec(relative_path="outputs/cleaned.csv", description="cleaned CSV"),
            ArtifactSpec(relative_path="outputs/report.json", description="summary"),
        ],
        sandbox_policy=SandboxPolicy(timeout_sec=30),
    )
    action = Action(
        action_id="a-compute-1",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="clean dirty CSV",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )
    return ActionPlan(
        plan_id="plan-compute",
        task_id="t-compute",
        summary="Phase 23 dry-run: render ComputeAction script",
        actions=[action],
    )


def test_simulate_action_extracts_compute_fields(workspace: Path) -> None:
    plan = _make_compute_plan()
    info = simulate_action(workspace, plan.actions[0])
    assert info["action_type"] == "python_compute"
    assert info["compute_summary"].startswith("normalise sales_dirty.csv")
    assert info["compute_inputs"] == ["sales_dirty.csv"]
    assert info["compute_outputs"] == ["outputs/cleaned.csv", "outputs/report.json"]
    assert info["compute_timeout_sec"] == 30
    assert "print('hi')" in info["compute_script"]


def test_dry_run_renders_compute_summary_and_script(workspace: Path) -> None:
    plan = _make_compute_plan(
        with_script="import csv\nprint('demo')\n",
    )
    assessment = RiskAssessment(
        plan_id=plan.plan_id,
        passed=True,
        risk_level=RiskVerdict.MEDIUM,
        reason="compute action requires approval",
        warnings=[],
    )
    md = render_dry_run_markdown(plan, workspace, assessment)
    # Table row shows summary as reason and scratch/outputs/ as target.
    assert "python_compute" in md
    assert "normalise sales_dirty.csv" in md
    assert "scratch/outputs/" in md
    # Dedicated compute scripts section with full source.
    assert "## Compute scripts" in md
    assert "### Action #1 — `a-compute-1`" in md
    assert "**Summary:**" in md
    assert "**Inputs (1):**" in md
    assert "`sales_dirty.csv`" in md
    assert "**Declared outputs (2):**" in md
    assert "`outputs/cleaned.csv`" in md
    assert "**Timeout:** 30s" in md
    # Script body fenced as python.
    assert "```python" in md
    assert "import csv" in md
    assert "print('demo')" in md


def test_dry_run_compute_script_is_truncated_when_huge(workspace: Path) -> None:
    huge = "x = 0\n" * 1000  # ~6 KiB
    plan = _make_compute_plan(with_script=huge)
    assessment = RiskAssessment(
        plan_id=plan.plan_id,
        passed=True,
        risk_level=RiskVerdict.MEDIUM,
        reason="compute large script",
        warnings=[],
    )
    md = render_dry_run_markdown(plan, workspace, assessment)
    assert "truncated; full source in scratch script.py" in md


def test_dry_run_compute_with_invalid_metadata_surfaces_error(workspace: Path) -> None:
    bad = Action(
        action_id="a-bad",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="bad metadata",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"this_is_not": "a_compute_action"},
    )
    plan = ActionPlan(
        plan_id="plan-bad",
        task_id="t-bad",
        summary="bad compute",
        actions=[bad],
    )
    assessment = RiskAssessment(
        plan_id=plan.plan_id,
        passed=False,
        risk_level=RiskVerdict.MEDIUM,
        reason="bad metadata",
        warnings=[],
    )
    md = render_dry_run_markdown(plan, workspace, assessment)
    # The error surfaces in the compute-script section.
    assert "## Compute scripts" in md
    assert "**Error:**" in md
    assert "invalid ComputeAction metadata" in md
