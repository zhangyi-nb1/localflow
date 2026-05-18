"""Phase 14 — every_input_accounted_for grader unit tests.

The grader complements the existing all_files_accounted_for (which
only checks that seed files didn't vanish without a move action) by
adding the "or cited in a generated *.md" coverage rule. These tests
pin the two coverage branches + the empty-seed edge case + the
genuine-missing case.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.eval.graders.structural import every_input_accounted_for
from app.eval.schema import EvalTask, GraderContext, WorkspaceFile
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    RiskLevel,
    RollbackManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)


def _ctx(
    workspace: Path,
    *,
    seed_paths: list[str],
    move_actions: list[tuple[str, str]] | None = None,
) -> GraderContext:
    actions: list[Action] = []
    for i, (src, tgt) in enumerate(move_actions or [], start=1):
        actions.append(
            Action(
                action_id=f"a-{i:03d}",
                action_type=ActionType.MOVE,
                source_path=src,
                target_path=tgt,
                reason="seed",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            )
        )
    seed = [WorkspaceFile(path=p, text="seed") for p in seed_paths]
    eval_task = EvalTask.model_construct(
        task_id="t-1",
        title="cov",
        goal="cov",
        skill="agent",
        planner="rule",
        expected_outputs=[],
        workspace_seed=seed,
        graders=[],
        must_pass=[],
        stages=None,
    )
    task_spec = TaskSpec(
        task_id="t-1",
        user_goal="cov",
        workspace_root=str(workspace),
        skill="agent",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )
    plan = ActionPlan(
        plan_id="plan-1",
        task_id="t-1",
        summary="seed",
        actions=actions,
        expected_outputs=[],
        risk_summary="low",
    )
    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root=str(workspace),
        files=[],
        total_files=0,
        total_size_bytes=0,
    )
    verification = VerificationResult(
        task_id="t-1",
        run_id="t-1",
        passed=True,
        checks=[VerificationCheck(name="x", passed=True)],
        failed_checks=[],
        summary="ok",
        created_at=datetime.now(timezone.utc),
    )
    return GraderContext(
        task=eval_task,
        task_spec=task_spec,
        plan=plan,
        snapshot_before=snap,
        snapshot_after=None,
        execution_records=[],
        manifest=RollbackManifest(task_id="t-1", run_id="t-1", entries=[], file_hashes_before={}),
        verification=verification,
        trace_events=[],
        workspace_path=workspace,
        seed_hashes={},
    )


def test_empty_seed_passes_trivially(tmp_path: Path) -> None:
    """No input files → trivially accounted-for."""
    ctx = _ctx(tmp_path, seed_paths=[])
    v = every_input_accounted_for(ctx)
    assert v.passed is True
    assert "trivially" in v.detail.lower()


def test_moved_to_existing_target_passes(tmp_path: Path) -> None:
    """Input moved to a target that exists on disk → branch (a) passes."""
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "attention.pdf").write_bytes(b"%PDF-1.4")
    ctx = _ctx(
        tmp_path,
        seed_paths=["attention.pdf"],
        move_actions=[("attention.pdf", "papers/attention.pdf")],
    )
    v = every_input_accounted_for(ctx)
    assert v.passed is True, v.detail


def test_cited_in_markdown_passes(tmp_path: Path) -> None:
    """Input not moved but mentioned by basename in a generated .md →
    branch (b) passes."""
    (tmp_path / "pdf_index.md").write_text(
        "# PDF Index\n\n- attention.pdf — paper\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path, seed_paths=["attention.pdf"])
    v = every_input_accounted_for(ctx)
    assert v.passed is True, v.detail


def test_unaccounted_when_missing_and_uncited(tmp_path: Path) -> None:
    """Input that's neither moved nor cited anywhere → fails with the
    missing basename listed in detail."""
    ctx = _ctx(tmp_path, seed_paths=["lost.pdf"])
    v = every_input_accounted_for(ctx)
    assert v.passed is False
    assert "lost.pdf" in v.detail
