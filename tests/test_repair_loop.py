"""Phase 13 — run_repair_loop unit tests.

These tests drive the orchestrator with carefully-staged inputs:
- A successful initial state → returns immediately, no attempts.
- An ineligible failure (no hint) → returns without attempting.
- An eligible failure → drives one repair cycle.

The actual LLM-driven revise call is patched (no API traffic). The
filesystem is real (we need real rollback semantics), but the
workspaces are tmp_path-scoped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.harness.executor import ExecutionOutcome
from app.harness.repair_loop import run_repair_loop
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    RiskLevel,
    RollbackManifest,
    SemanticVerdict,
    SemanticVerificationResult,
    SkillManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill
from app.storage.run_store import RunStore


def _task(workspace: Path) -> TaskSpec:
    return TaskSpec(
        task_id="t-1",
        user_goal="seed",
        workspace_root=str(workspace),
        skill="stub",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )


def _plan(task_id: str, plan_id: str = "plan-1") -> ActionPlan:
    return ActionPlan(
        plan_id=plan_id,
        task_id=task_id,
        summary="seed",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="out.md",
                reason="seed",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "x"},
            )
        ],
        expected_outputs=["out.md"],
        risk_summary="low",
    )


def _empty_outcome(task_id: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        run_id=task_id,
        records=[],
        manifest=RollbackManifest(
            task_id=task_id, run_id=task_id, entries=[], file_hashes_before={}
        ),
        success=True,
    )


def _verify(passed: bool = True) -> VerificationResult:
    return VerificationResult(
        task_id="t-1",
        run_id="t-1",
        passed=passed,
        checks=[VerificationCheck(name="x", passed=passed)],
        failed_checks=[] if passed else [VerificationCheck(name="x", passed=False)],
        summary="ok" if passed else "failed",
        created_at=datetime.now(timezone.utc),
    )


def _semantic(passed: bool, *, with_hint: bool = True) -> SemanticVerificationResult:
    if passed:
        verdicts = [SemanticVerdict(grader="x", passed=True, reason="all good")]
    else:
        hint = "re-plan with different aggregation columns" if with_hint else None
        verdicts = [
            SemanticVerdict(
                grader="analysis_result_nonempty",
                passed=False,
                reason="every analysis produced empty results",
                suggested_hint=hint,
            )
        ]
    failed = [v for v in verdicts if not v.passed]
    return SemanticVerificationResult(
        task_id="t-1",
        run_id="t-1",
        passed=passed,
        verdicts=verdicts,
        failed_verdicts=failed,
        summary="ok" if passed else "fail",
        created_at=datetime.now(timezone.utc),
        auto_repair_eligible=(not passed) and any(v.suggested_hint for v in failed),
    )


def _snapshot(workspace: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root=str(workspace),
        files=[],
        total_files=0,
        total_size_bytes=0,
    )


class _NoLLMStub(Skill):
    """A skill that signals supports_llm=False so Skill.revise raises."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="stub",
            description="rule-only stand-in",
            version="0.0.1",
            capabilities=[],
            required_tools=[],
            allowed_actions=["mkdir", "move", "index"],
            requires_approval=[],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task, snapshot):
        return _plan(task.task_id)

    def validate(self, plan):
        return None

    def report(self, **kwargs):
        return ""

    def supports_llm(self) -> bool:
        return False


def test_skip_when_initial_state_already_passed(tmp_path: Path) -> None:
    """No attempts when the initial semantic verdict already passed."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore(task_id="t-1", home=tmp_path)
    task = _task(workspace)
    initial_sem = _semantic(passed=True)
    final_plan, _state, outcome = run_repair_loop(
        task,
        snapshot=_snapshot(workspace),
        current_plan=_plan("t-1"),
        current_outcome=_empty_outcome("t-1"),
        current_structural=_verify(True),
        current_semantic=initial_sem,
        skill=_NoLLMStub(),
        run_store=store,
        max_attempts=3,
    )
    assert outcome.repaired is True
    assert outcome.attempts == 0
    assert outcome.halt_reason == "passed"


def test_skip_when_max_attempts_is_zero(tmp_path: Path) -> None:
    """max_attempts=0 = 'report-only mode' — surface verdicts but don't repair."""
    store = RunStore(task_id="t-1", home=tmp_path)
    task = _task(tmp_path)
    _, _, outcome = run_repair_loop(
        task,
        snapshot=_snapshot(tmp_path),
        current_plan=_plan("t-1"),
        current_outcome=_empty_outcome("t-1"),
        current_structural=_verify(True),
        current_semantic=_semantic(False),
        skill=_NoLLMStub(),
        run_store=store,
        max_attempts=0,
    )
    assert outcome.repaired is False
    assert outcome.attempts == 0
    assert outcome.halt_reason == "report_only"


def test_skip_when_no_eligible_hint(tmp_path: Path) -> None:
    """A failed verdict without a suggested_hint can't drive auto-repair —
    surface to the user instead of looping uselessly."""
    store = RunStore(task_id="t-1", home=tmp_path)
    task = _task(tmp_path)
    sem = _semantic(False, with_hint=False)
    _, _, outcome = run_repair_loop(
        task,
        snapshot=_snapshot(tmp_path),
        current_plan=_plan("t-1"),
        current_outcome=_empty_outcome("t-1"),
        current_structural=_verify(True),
        current_semantic=sem,
        skill=_NoLLMStub(),
        run_store=store,
        max_attempts=3,
    )
    assert outcome.repaired is False
    assert outcome.attempts == 0
    assert outcome.halt_reason == "no_hint"


def test_rule_only_skill_halts_with_clean_reason(tmp_path: Path) -> None:
    """A skill whose ``supports_llm()`` returns False raises SkillError
    inside revise(); the loop catches it and halts with
    halt_reason='not_revisable'."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore(task_id="t-1", home=tmp_path)
    task = _task(workspace)
    sem = _semantic(False)
    # Use a manifest with no entries → rollback is a no-op.
    initial = _empty_outcome("t-1")
    _, _, outcome = run_repair_loop(
        task,
        snapshot=_snapshot(workspace),
        current_plan=_plan("t-1"),
        current_outcome=initial,
        current_structural=_verify(True),
        current_semantic=sem,
        skill=_NoLLMStub(),
        run_store=store,
        max_attempts=2,
    )
    assert outcome.repaired is False
    assert outcome.halt_reason == "not_revisable"


def test_journal_row_appended_on_attempt(tmp_path: Path) -> None:
    """Every repair attempt writes a JSON line to repairs.jsonl —
    even when the attempt itself fails, so the user can audit what
    happened."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore(task_id="t-1", home=tmp_path)
    task = _task(workspace)
    sem = _semantic(False)
    run_repair_loop(
        task,
        snapshot=_snapshot(workspace),
        current_plan=_plan("t-1"),
        current_outcome=_empty_outcome("t-1"),
        current_structural=_verify(True),
        current_semantic=sem,
        skill=_NoLLMStub(),
        run_store=store,
        max_attempts=1,
    )
    # The repair_loop attempted once before SkillError halted; the
    # journal should have at least one row.
    assert store.repairs_log_path.exists()
    lines = store.repairs_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
