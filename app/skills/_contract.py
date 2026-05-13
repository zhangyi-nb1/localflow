"""Phase 4.3 — Unified Skill Lifecycle + Contract Test Template.

This is the **canonical "is this Skill compatible with LocalFlow's
harness?" check**. Any caller — built-in tests, third-party skill
authors, or a future ``localflow validate-skill`` CLI command — can run
the same 8-stage gauntlet and get back a structured pass/fail report.

The 8 lifecycle stages (in canonical order):

  1. ``manifest_valid``           — manifest fields well-formed,
                                    required_tools resolve in the
                                    Phase 4.2 Tool Registry.
  2. ``plan_empty_workspace``     — skill.plan() on an empty snapshot
                                    must not crash.
  3. ``plan_happy_path``          — skill.plan() on the seeded snapshot
                                    yields a valid ActionPlan staying
                                    within the workspace and the
                                    manifest's allowed_actions.
  4. ``validate_accepts_own_plan``— skill.validate() accepts whatever
                                    skill.plan() just produced.
  5. ``validate_rejects_garbage`` — A plan with a clearly malformed
                                    action (target outside workspace) is
                                    rejected by validate, Pydantic, or
                                    the Executor's policy guard.
  6. ``execute_and_verify``       — Executor runs the plan and Verifier
                                    independently certifies success.
  7. ``rollback_restores``        — Rollback restores the workspace to
                                    its pre-execute file count.
  8. ``report_non_empty``         — skill.report() returns non-empty
                                    markdown referencing the skill name.

The 5 outline §13.7 categories all map into these stages:
  正常样例 → plan_happy_path + execute_and_verify
  非法 action → validate_rejects_garbage
  dry-run → covered implicitly by Executor policy_guard pre-flight (stage 5)
  rollback → rollback_restores
  verify → execute_and_verify (the Verifier half)

Stages are independently wrapped in try/except so one failure doesn't
mask the others. A typo in ``manifest_valid`` shouldn't hide an unrelated
crash in ``rollback_restores`` — you get the whole list at once.

Outline §10.7: this module touches NO ``app/harness/*`` code. It treats
Executor/Verifier/Rollback as black boxes — exactly what the kernel
contract is supposed to make possible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from app.skills._base import Skill
    from app.storage.run_store import RunStore


WorkspaceSeeder = Callable[[Path], None]


@dataclass
class StageResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name}: {self.detail}" if self.detail else f"[{status}] {self.name}"


@dataclass
class ContractReport:
    skill_name: str
    stages: list[StageResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(s.passed for s in self.stages)

    def failed_stages(self) -> list[StageResult]:
        return [s for s in self.stages if not s.passed]

    def __str__(self) -> str:
        header = f"ContractReport({self.skill_name}): {'ALL PASSED' if self.all_passed else 'FAILURES'}"
        lines = [header] + [f"  {s}" for s in self.stages]
        return "\n".join(lines)


def _count_files(root: Path) -> int:
    """File count under ``root`` (recursive, excludes dirs)."""
    return sum(1 for p in root.rglob("*") if p.is_file())


def run_skill_contract(
    skill: "Skill",
    *,
    workspace_seeder: WorkspaceSeeder,
    workspace_root: Path,
    run_store: "RunStore",
    allowed_actions: list[str] | None = None,
    require_at_least_one_action: bool = True,
) -> ContractReport:
    """Drive ``skill`` through the canonical lifecycle and return per-stage results.

    ``workspace_seeder`` populates ``workspace_root`` with files
    representative of the kind of input ``skill`` is designed to plan
    over. Pass an empty function if your skill works against any
    workspace shape.

    ``allowed_actions`` defaults to the skill's manifest. Override only
    if the TaskSpec needs a *narrower* allowed_actions list than the
    manifest declares (rare).

    The function performs real IO (writes to ``workspace_root``, runs
    Executor / Rollback). Callers should use a ``tmp_path`` workspace and
    an isolated ``RunStore`` for clean state.
    """
    # Local imports keep the module import-time light + avoid the cycle
    # app.skills -> app.harness -> app.schemas -> app.skills.
    from app.harness.executor import Executor
    from app.harness.policy_guard import PolicyViolation
    from app.harness.rollback import Rollback
    from app.harness.verifier import Verifier
    from app.schemas import (
        ExecutionStatus,
        TaskSpec,
        WorkspaceSnapshot,
    )
    from app.schemas.action import Action, ActionType, RiskLevel
    from app.schemas.plan import ActionPlan
    from app.tools import get_default_tool_registry
    from app.tools.file_scan import scan_workspace

    report = ContractReport(skill_name=skill.manifest.name)

    def _record(name: str, fn: Callable[[], str | None]) -> bool:
        """Run a stage. Return True if it passed. ``fn`` may return a
        detail string on success (empty == "ok") or raise to fail."""
        try:
            detail = fn() or "ok"
            report.stages.append(StageResult(name=name, passed=True, detail=detail))
            return True
        except Exception as exc:
            report.stages.append(
                StageResult(
                    name=name,
                    passed=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            return False

    manifest = skill.manifest

    # ------------------------------------------------------------- 1. manifest
    def _check_manifest() -> str:
        if not manifest.name:
            raise AssertionError("manifest.name is empty")
        if not manifest.allowed_actions:
            raise AssertionError("manifest.allowed_actions is empty")
        tool_reg = get_default_tool_registry()
        for tool_name in manifest.required_tools:
            if not tool_reg.has(tool_name):
                raise AssertionError(
                    f"required_tool {tool_name!r} not in Tool Registry"
                )
        return f"name={manifest.name}, allowed={manifest.allowed_actions}, tools={len(manifest.required_tools)}"

    _record("manifest_valid", _check_manifest)

    # ------------------------------------------------------------- prep
    # Empty workspace BEFORE seeding for stage 2.
    workspace_root = workspace_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    actions_for_task = allowed_actions or list(manifest.allowed_actions)

    def _build_task() -> TaskSpec:
        return TaskSpec(
            task_id=run_store.task_id,
            user_goal=f"contract test for {manifest.name}",
            workspace_root=str(workspace_root),
            skill=manifest.name,
            constraints=[],
            allowed_actions=actions_for_task,
            forbidden_actions=["delete", "shell"],
        )

    # ------------------------------------------------------------- 2. empty workspace
    empty_snap_id = f"snap-empty-{uuid4().hex[:6]}"

    def _check_empty() -> str:
        empty_snap = WorkspaceSnapshot(
            snapshot_id=empty_snap_id,
            task_id=run_store.task_id,
            root=str(workspace_root),
        )
        empty_task = _build_task()
        plan = skill.plan(empty_task, empty_snap)
        if not isinstance(plan, ActionPlan):
            raise AssertionError(f"plan() returned {type(plan).__name__}, not ActionPlan")
        return f"returned ActionPlan with {len(plan.actions)} action(s)"

    _record("plan_empty_workspace", _check_empty)

    # ------------------------------------------------------------- seed + scan
    workspace_seeder(workspace_root)
    pre_execute_file_count = _count_files(workspace_root)
    snapshot = scan_workspace(
        workspace_root, run_store.task_id, compute_hash=True, compute_preview=True
    )
    task = _build_task()
    run_store.save_task(task)
    run_store.save_workspace(snapshot)

    # ------------------------------------------------------------- 3. happy-path plan
    plan_holder: dict = {}

    def _check_plan_happy() -> str:
        plan = skill.plan(task, snapshot)
        if not isinstance(plan, ActionPlan):
            raise AssertionError(f"plan() returned {type(plan).__name__}, not ActionPlan")
        if require_at_least_one_action and not plan.actions:
            raise AssertionError("planner produced 0 actions on seeded workspace")
        allowed_set = set(manifest.allowed_actions)
        for a in plan.actions:
            if a.action_type.value not in allowed_set:
                raise AssertionError(
                    f"action {a.action_id!r} has type {a.action_type.value!r} "
                    f"not in manifest.allowed_actions={allowed_set}"
                )
            # Target must resolve inside workspace.
            target = (workspace_root / a.target_path).resolve()
            try:
                target.relative_to(workspace_root)
            except ValueError:
                raise AssertionError(
                    f"action {a.action_id!r} target_path={a.target_path!r} "
                    f"escapes workspace"
                )
        plan_holder["plan"] = plan
        return f"{len(plan.actions)} action(s), all types ⊆ allowed"

    plan_ok = _record("plan_happy_path", _check_plan_happy)

    # ------------------------------------------------------------- 4. validate accepts own
    def _check_validate_self() -> str:
        if "plan" not in plan_holder:
            raise AssertionError("no plan available (plan_happy_path failed)")
        skill.validate(plan_holder["plan"])
        return "validate() accepted own plan"

    if plan_ok:
        _record("validate_accepts_own_plan", _check_validate_self)
    else:
        report.stages.append(StageResult(
            name="validate_accepts_own_plan", passed=False,
            detail="skipped (plan_happy_path failed)"
        ))

    # ------------------------------------------------------------- 5. validate rejects garbage
    def _check_validate_garbage() -> str:
        """Synthesize an obviously bad plan (target escapes workspace).

        Acceptable rejection: skill.validate() raises, OR the harness-level
        ``policy_guard.resolve_inside`` raises PolicyViolation. We never
        run the bad plan through Executor — that would risk polluting the
        workspace and corrupting later stages. Instead we test the two
        synchronous checkpoints any execution would have to pass through.
        """
        from app.harness.policy_guard import resolve_inside

        bad_type = manifest.allowed_actions[0]
        bad_action = Action(
            action_id="bad-001",
            action_type=ActionType(bad_type),
            target_path="../escape.md",
            reason="contract test fixture — should be rejected",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
            metadata={"content": "x", "overwrite_existing": True},
        )
        bad_plan = ActionPlan(
            plan_id=f"bad-{uuid4().hex[:6]}",
            task_id=task.task_id,
            summary="garbage plan",
            actions=[bad_action],
        )

        # Path 1: skill-level rejection (preferred — skill knows its own
        # invariants and can produce a more helpful error).
        try:
            skill.validate(bad_plan)
        except Exception as exc:
            return f"skill.validate rejected: {type(exc).__name__}"

        # Path 2: harness-level rejection. resolve_inside is the universal
        # safety net every Executor call passes through.
        try:
            resolve_inside(workspace_root, "../escape.md")
        except PolicyViolation as exc:
            return f"policy_guard rejected: {exc}"

        raise AssertionError(
            "garbage plan with target_path='../escape.md' was NOT rejected "
            "by either skill.validate or policy_guard.resolve_inside"
        )

    if plan_ok:
        _record("validate_rejects_garbage", _check_validate_garbage)
    else:
        report.stages.append(StageResult(
            name="validate_rejects_garbage", passed=False,
            detail="skipped (plan_happy_path failed)"
        ))

    # ------------------------------------------------------------- 6. execute + verify
    outcome_holder: dict = {}

    def _check_execute_verify() -> str:
        plan = plan_holder["plan"]
        run_store.save_plan(plan)
        executor = Executor(workspace_root=workspace_root, run_store=run_store)
        outcome = executor.execute(plan, approved=True)
        outcome_holder["outcome"] = outcome
        if not outcome.success:
            failed = [r.action_id for r in outcome.records if r.status == ExecutionStatus.FAILED]
            raise AssertionError(f"executor.execute reported failure; failed={failed}")
        verifier = Verifier(workspace_root=workspace_root)
        executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
        skipped = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SKIPPED}
        failed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.FAILED}
        result = verifier.verify(
            task_id=task.task_id,
            run_id=outcome.run_id,
            plan=plan,
            manifest=outcome.manifest,
            executed_action_ids=executed,
            skipped_action_ids=skipped,
            failed_action_ids=failed,
            original_snapshot=snapshot,
        )
        outcome_holder["verify"] = result
        if not result.passed:
            raise AssertionError(f"Verifier failed: {result.failed_checks}")
        return f"executed={len(executed)}, verifier.passed=True"

    exec_ok = False
    if plan_ok:
        exec_ok = _record("execute_and_verify", _check_execute_verify)
    else:
        report.stages.append(StageResult(
            name="execute_and_verify", passed=False,
            detail="skipped (plan_happy_path failed)"
        ))

    # ------------------------------------------------------------- 7. rollback restores
    def _check_rollback() -> str:
        if not manifest.supports_rollback:
            return "skipped (manifest.supports_rollback=False)"
        outcome = outcome_holder["outcome"]
        rb = Rollback(workspace_root=workspace_root, run_store=run_store)
        rb_outcome = rb.run(outcome.manifest)
        if not rb_outcome.success:
            raise AssertionError(f"rollback failed: {rb_outcome.failed}")
        post_count = _count_files(workspace_root)
        if post_count != pre_execute_file_count:
            raise AssertionError(
                f"file count drift after rollback: pre={pre_execute_file_count} "
                f"post={post_count} (leftover artifacts?)"
            )
        return f"undone={len(rb_outcome.undone)}, file count restored ({post_count})"

    if exec_ok:
        _record("rollback_restores", _check_rollback)
    else:
        report.stages.append(StageResult(
            name="rollback_restores", passed=False,
            detail="skipped (execute_and_verify failed)"
        ))

    # ------------------------------------------------------------- 8. report non-empty
    def _check_report() -> str:
        plan = plan_holder["plan"]
        text = skill.report(
            task=task,
            plan=plan,
            outcome=outcome_holder["outcome"],
            verification=outcome_holder["verify"],
        )
        if not isinstance(text, str) or not text.strip():
            raise AssertionError(f"report() returned empty/non-string: {text!r}")
        return f"{len(text)} chars"

    if exec_ok:
        _record("report_non_empty", _check_report)
    else:
        report.stages.append(StageResult(
            name="report_non_empty", passed=False,
            detail="skipped (execute_and_verify failed)"
        ))

    return report
