"""Phase 13 — runtime SemanticVerifier tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.eval.graders import register
from app.eval.schema import GraderVerdict
from app.harness.semantic_verifier import SEMANTIC_GRADER_NAMES, SemanticVerifier
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    RollbackManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)


def _task(tmp_path: Path) -> TaskSpec:
    return TaskSpec(
        task_id="t-1",
        user_goal="seed",
        workspace_root=str(tmp_path),
        skill="agent",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )


def _plan() -> ActionPlan:
    return ActionPlan(
        plan_id="plan-1",
        task_id="t-1",
        summary="seed",
        actions=[],
        expected_outputs=[],
        risk_summary="low",
    )


def _snapshot(tmp_path: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root=str(tmp_path),
        files=[],
        total_files=0,
        total_size_bytes=0,
    )


def _verify_ok() -> VerificationResult:
    return VerificationResult(
        task_id="t-1",
        run_id="t-1",
        passed=True,
        checks=[VerificationCheck(name="x", passed=True)],
        failed_checks=[],
        summary="ok",
        created_at=datetime.now(timezone.utc),
    )


def _empty_manifest() -> RollbackManifest:
    return RollbackManifest(task_id="t-1", run_id="t-1", entries=[], file_hashes_before={})


# ─────────────────────────── happy path: all-pass


def test_all_pass_with_skip_graders(tmp_path: Path) -> None:
    """When every starter grader returns passed (e.g., because it
    skipped due to inapplicability), the aggregate is passed=True."""
    sv = SemanticVerifier(tmp_path)
    result = sv.verify(
        task=_task(tmp_path),
        plan=_plan(),
        execution_records=[],
        manifest=_empty_manifest(),
        snapshot_before=_snapshot(tmp_path),
        snapshot_after=None,
        structural=_verify_ok(),
    )
    assert result.passed is True
    assert result.auto_repair_eligible is False
    assert len(result.verdicts) == len(SEMANTIC_GRADER_NAMES)


def test_one_failure_makes_passed_false(tmp_path: Path) -> None:
    """A single grader rejection flips the aggregate."""
    register_name = "fake_failing"
    if register_name not in {n for n in SEMANTIC_GRADER_NAMES}:

        @register(register_name)
        def _fake(ctx):
            return GraderVerdict(name=register_name, passed=False, detail="manufactured failure")

    sv = SemanticVerifier(tmp_path, graders=[register_name])
    result = sv.verify(
        task=_task(tmp_path),
        plan=_plan(),
        execution_records=[],
        manifest=_empty_manifest(),
        snapshot_before=_snapshot(tmp_path),
        snapshot_after=None,
        structural=_verify_ok(),
    )
    assert result.passed is False
    assert result.auto_repair_eligible is True
    assert result.failed_verdicts and "manufactured failure" in result.failed_verdicts[0].reason


def test_unregistered_grader_treated_as_skipped(tmp_path: Path) -> None:
    """Asking for a grader that isn't in the registry doesn't crash —
    it surfaces as 'not registered; skipped' (passed=True)."""
    sv = SemanticVerifier(tmp_path, graders=["does_not_exist_anywhere"])
    result = sv.verify(
        task=_task(tmp_path),
        plan=_plan(),
        execution_records=[],
        manifest=_empty_manifest(),
        snapshot_before=_snapshot(tmp_path),
        snapshot_after=None,
        structural=_verify_ok(),
    )
    assert result.passed is True
    assert "not registered" in result.verdicts[0].reason


def test_auto_repair_eligible_requires_hint(tmp_path: Path) -> None:
    """A rejected verdict without a suggested_hint must NOT mark the
    result auto_repair_eligible — the loop has nothing concrete to do."""
    register_name = "fake_no_hint"
    if register_name not in {n for n in SEMANTIC_GRADER_NAMES}:

        @register(register_name)
        def _fake(ctx):
            # Passing detail="" means semantic_verifier won't synthesize
            # a hint and we want to test the no-hint branch — overriding
            # _generic_hint via patch.
            return GraderVerdict(name=register_name, passed=False, detail="")

    sv = SemanticVerifier(tmp_path, graders=[register_name])
    # Patch the generic-hint fallback to return empty so the verdict
    # genuinely has no hint.
    with patch("app.harness.semantic_verifier._generic_hint", return_value=""):
        result = sv.verify(
            task=_task(tmp_path),
            plan=_plan(),
            execution_records=[],
            manifest=_empty_manifest(),
            snapshot_before=_snapshot(tmp_path),
            snapshot_after=None,
            structural=_verify_ok(),
        )
    assert result.passed is False
    # The failing verdict has suggested_hint=None → not auto-repair-eligible.
    assert any(v.suggested_hint is None for v in result.failed_verdicts)


def test_grader_crash_is_treated_as_skipped(tmp_path: Path) -> None:
    """A crashing grader produces a passed=True 'skipped' verdict
    rather than poisoning the whole pass."""
    register_name = "fake_crasher"
    if register_name not in {n for n in SEMANTIC_GRADER_NAMES}:

        @register(register_name)
        def _crash(ctx):
            raise RuntimeError("boom")

    sv = SemanticVerifier(tmp_path, graders=[register_name])
    result = sv.verify(
        task=_task(tmp_path),
        plan=_plan(),
        execution_records=[],
        manifest=_empty_manifest(),
        snapshot_before=_snapshot(tmp_path),
        snapshot_after=None,
        structural=_verify_ok(),
    )
    assert result.passed is True
    assert "crashed" in result.verdicts[0].reason.lower()


def test_runs_only_explicit_grader_list(tmp_path: Path) -> None:
    """Constructing with an explicit graders=[…] list overrides the
    default SEMANTIC_GRADER_NAMES set — useful for tests that only
    care about one grader's behaviour."""
    sv = SemanticVerifier(tmp_path, graders=["analysis_result_nonempty"])
    result = sv.verify(
        task=_task(tmp_path),
        plan=_plan(),
        execution_records=[],
        manifest=_empty_manifest(),
        snapshot_before=_snapshot(tmp_path),
        snapshot_after=None,
        structural=_verify_ok(),
    )
    assert len(result.verdicts) == 1
    assert result.verdicts[0].grader == "analysis_result_nonempty"


def test_trace_logger_constructor_smoke(tmp_path: Path) -> None:
    """SemanticVerifier accepts an optional TraceLogger; constructing
    one shouldn't raise."""
    trace = TraceLogger(tmp_path / "trace.jsonl")
    sv = SemanticVerifier(tmp_path, trace=trace)
    assert sv.workspace_root == tmp_path.resolve()
    assert sv.trace is trace
