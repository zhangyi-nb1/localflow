"""Phase 10 — TaskGraph runner.

Walks a :class:`TaskGraph` sequentially, driving each
:class:`StageSpec` through the same ``control_loop.run_*`` pipeline
the single-skill CLI uses. The kernel itself is unchanged; this
file lives next to ``control_loop.py`` (not inside it) so the §10.7
"no kernel-behaviour changes" invariant holds.

Two design points worth calling out:

  1. **One global RollbackManifest** at ``<run_dir>/rollback_manifest.json``,
     aggregating entries from every stage. ``localflow rollback
     --run-id <id>`` undoes the whole graph — the existing CLI command
     doesn't need to know stages exist.
  2. **One global TraceLogger** writing to ``<run_dir>/trace.jsonl``.
     Each stage's events get tagged with ``stage_id`` via the
     :meth:`TraceLogger.stage` context manager — no plumbing changes
     to the kernel emission sites.

Failure-policy semantics:
  * ``ABORT``    — stop the graph; remaining stages get StageStatus.ABORTED.
  * ``CONTINUE`` — log the failure and run the next stage anyway.
  * ``SKIP``     — same execution semantics as CONTINUE but the
                   stage's recorded status is SKIPPED instead of FAILED.

Phase 12 will add a fourth policy (REPAIR) that triggers a repair
loop; Phase 10's ``max_retries`` field is reserved for that, always
1 in v0.11.0.
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from app.harness import control_loop
from app.harness.executor import Executor
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    ExecutionStatus,
    RollbackEntry,
    RollbackManifest,
    StageFailurePolicy,
    StageResult,
    StageSpec,
    StageStatus,
    TaskGraph,
    TaskGraphResult,
    TaskSpec,
)
from app.skills import SkillError, get_default_registry
from app.storage.run_store import RunStore


class StageRunStore(RunStore):
    """Per-stage view onto a parent RunStore.

    Reuses the parent's ``task_id`` + ``home``, but redirects
    ``run_dir`` to ``<parent.run_dir>/stages/<stage_id>/`` so each
    stage's plan.json / dry_run.md / actions.json land in its own
    subdir. The trace path stays at the parent level so we get ONE
    trace.jsonl for the whole graph (events tagged via
    :meth:`TraceLogger.stage`).
    """

    def __init__(self, parent: RunStore, stage_id: str) -> None:
        # Avoid super().__init__ — it would recreate dirs under
        # <home>/runs/<task_id>/ as a fresh run. We want to slot UNDER
        # an existing parent run.
        self.task_id = parent.task_id
        self.home = parent.home
        self.run_dir = parent.stage_dir(stage_id)
        self._parent = parent
        # backups/ stays at the parent level (binary backups are
        # shared across the graph's lifecycle).
        (self.run_dir / self.BACKUPS_DIR).mkdir(exist_ok=True)

    @property
    def trace_path(self) -> Path:
        return self._parent.trace_path

    @property
    def backups_dir(self) -> Path:
        # Use the parent's backups dir so overwrite-aware actions
        # across multiple stages share one backup root.
        return self._parent.backups_dir


def run_taskgraph(
    graph: TaskGraph,
    run_store: RunStore,
    *,
    trace: TraceLogger | None = None,
    approved: bool = False,
) -> TaskGraphResult:
    """Walk ``graph.stages`` sequentially. Returns a structured
    :class:`TaskGraphResult` regardless of success / failure.

    The runner NEVER raises through the call boundary — any stage
    crash is caught, recorded on its :class:`StageResult.error`, and
    the runner consults ``failure_policy`` to decide what to do.
    """
    started = time.perf_counter()
    if not approved:
        raise RuntimeError(
            "TaskGraphRunner refused: graph not approved. "
            "Approval ceremony happens at the CLI / eval-runner layer."
        )

    # Persist the graph spec for audit / replay.
    if graph.task_id is None:
        graph = graph.model_copy(update={"task_id": run_store.task_id})
    run_store.write_json(run_store.taskgraph_path, graph.model_dump(mode="json"))

    registry = get_default_registry()
    aggregated = RollbackManifest(
        run_id=run_store.task_id, task_id=graph.task_id or run_store.task_id
    )

    stage_results: list[StageResult] = []
    abort_remaining = False

    for stage in graph.stages:
        if abort_remaining:
            stage_results.append(StageResult(stage_id=stage.stage_id, status=StageStatus.ABORTED))
            continue

        stage_started = time.perf_counter()
        ctx = _wrap_stage_in_trace(trace, stage.stage_id)
        with ctx:
            result = _run_one_stage(
                graph=graph,
                stage=stage,
                run_store=run_store,
                registry=registry,
                trace=trace,
                aggregated=aggregated,
            )
        result.duration_ms = int((time.perf_counter() - stage_started) * 1000)
        stage_results.append(result)

        if result.status == StageStatus.FAILED:
            if stage.failure_policy == StageFailurePolicy.ABORT:
                abort_remaining = True
            elif stage.failure_policy == StageFailurePolicy.SKIP:
                # Downgrade to SKIPPED in the report — same execution
                # behaviour as CONTINUE.
                result.status = StageStatus.SKIPPED

    # Persist the aggregated manifest at the parent run_store level so
    # `localflow rollback --run-id` undoes the whole graph.
    run_store.save_rollback(aggregated)

    duration_ms = int((time.perf_counter() - started) * 1000)
    tg_result = TaskGraphResult.from_stages(
        task_id=run_store.task_id,
        stages=stage_results,
        aggregated_manifest_path=str(run_store.rollback_path),
        duration_ms=duration_ms,
    )
    run_store.write_json(run_store.taskgraph_result_path, tg_result.model_dump(mode="json"))
    return tg_result


# --------------------------------------------------------------------- internals


def _wrap_stage_in_trace(trace: TraceLogger | None, stage_id: str):
    """Return a context manager that tags emitted events with
    ``stage_id``. When trace is None, returns a null-context so the
    runner body stays identical in either case."""
    if trace is not None:
        return trace.stage(stage_id)
    from contextlib import nullcontext

    return nullcontext()


def _run_one_stage(
    *,
    graph: TaskGraph,
    stage: StageSpec,
    run_store: RunStore,
    registry,
    trace: TraceLogger | None,
    aggregated: RollbackManifest,
) -> StageResult:
    """Drive one stage through the standard control_loop pipeline.

    Returns a StageResult. The TraceLogger.stage context manager
    already wraps the call so any trace events emitted inside get
    tagged.
    """
    try:
        skill = registry.require(stage.skill)
    except SkillError as exc:
        return StageResult(stage_id=stage.stage_id, status=StageStatus.FAILED, error=str(exc))

    # Build a sub-TaskSpec — inherits workspace + forbidden_paths from
    # the graph; allowed_actions defaults to the skill manifest unless
    # the stage overrides; forbidden_actions are additive.
    allowed = list(stage.allowed_actions or skill.manifest.allowed_actions)
    forbidden_actions = list(graph.forbidden_actions) + list(stage.forbidden_actions)
    sub_task = TaskSpec(
        task_id=run_store.task_id,
        user_goal=f"[{stage.stage_id}] {stage.title}",
        workspace_root=graph.workspace_root,
        skill=stage.skill,
        allowed_actions=allowed,
        forbidden_actions=forbidden_actions,
        forbidden_paths=list(graph.forbidden_paths),
        preferences=dict(graph.preferences),
    )

    stage_store = StageRunStore(run_store, stage.stage_id)
    stage_store.save_task(sub_task)

    try:
        snapshot = control_loop.run_inspect(
            Path(graph.workspace_root),
            task_id=run_store.task_id,
            compute_hash=True,
            compute_preview=True,
        )
        stage_store.save_workspace(snapshot)

        if stage.planner == "llm":
            plan = skill.plan_with_llm(sub_task, snapshot, trace=trace)
        else:
            plan = skill.plan(sub_task, snapshot)

        # Tag every action_id with stage prefix so the aggregated
        # manifest's action_ids stay unique across stages.
        plan = _prefix_action_ids(plan, stage.stage_id)
        # Carry the stage's declared expected_outputs onto the plan so
        # graders / verifier see them.
        if stage.expected_outputs:
            plan = plan.model_copy(
                update={
                    "expected_outputs": list(
                        list(plan.expected_outputs) + list(stage.expected_outputs)
                    )
                }
            )
        skill.validate(plan)
        stage_store.save_plan(plan)

        assessment = control_loop.run_risk_check(sub_task, plan, trace=trace)
        control_loop.run_dry_run(sub_task, plan, assessment, stage_store, trace=trace)

        if not assessment.passed:
            return StageResult(
                stage_id=stage.stage_id,
                status=StageStatus.FAILED,
                plan_id=plan.plan_id,
                action_count=len(plan.actions),
                error=f"policy_guard blocked the plan: {'; '.join(assessment.warnings)}",
            )

        executor = Executor(
            workspace_root=Path(graph.workspace_root),
            run_store=stage_store,
            forbidden_actions=tuple(sub_task.forbidden_actions),
            forbidden_paths=tuple(sub_task.forbidden_paths),
            trace=trace,
        )
        outcome = executor.execute(plan, approved=True)
        # Merge the stage's manifest into the graph-level one.
        _merge_manifest(aggregated, outcome.manifest)

        verification = control_loop.run_verify(
            sub_task, plan, stage_store, outcome, snapshot, trace=trace
        )

        success_count = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
        failed_count = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
        skipped_count = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

        status = (
            StageStatus.PASSED if outcome.success and verification.passed else StageStatus.FAILED
        )
        return StageResult(
            stage_id=stage.stage_id,
            status=status,
            plan_id=plan.plan_id,
            action_count=len(plan.actions),
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            verifier_passed=verification.passed,
            failed_checks=[c.name for c in verification.failed_checks],
        )
    except Exception as exc:
        # Defensive — Phase 10 contract: NEVER raise out of the runner.
        # Crashing graphs poison batched eval reports; we'd rather
        # mark one stage failed than nuke the whole result.
        return StageResult(
            stage_id=stage.stage_id,
            status=StageStatus.FAILED,
            error=(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}"),
        )


def _prefix_action_ids(plan: ActionPlan, stage_id: str) -> ActionPlan:
    """Rewrite ``plan.actions[*].action_id`` to ``<stage_id>.<original>``.

    Keeps action_ids globally unique across stages so the aggregated
    rollback manifest's checks (and the verifier's
    ``all_actions_accounted`` check) keep working.
    """
    prefix = f"{stage_id}."
    new_actions = [a.model_copy(update={"action_id": prefix + a.action_id}) for a in plan.actions]
    return plan.model_copy(update={"actions": new_actions})


def _merge_manifest(into: RollbackManifest, src: RollbackManifest) -> None:
    """Aggregate ``src`` into ``into`` in place.

    Preserves the global ``run_id`` / ``task_id`` on ``into`` (set at
    runner init); ``src`` brought along its per-stage copies which we
    discard. Lists are extended; ``file_hashes_before`` merges with
    later-stage hashes overriding earlier ones (each stage sees the
    workspace state at the START of that stage).
    """
    # action_ids are already stage-prefixed by _prefix_action_ids, so
    # the entries' action_ids stay unique across the merge.
    for entry in src.entries:
        into.entries.append(_clone_entry(entry))
    into.created_dirs.extend(src.created_dirs)
    into.generated_files.extend(src.generated_files)
    into.file_hashes_before.update(src.file_hashes_before)


def _clone_entry(entry: RollbackEntry) -> RollbackEntry:
    """Make sure aggregation doesn't share mutable state with the
    per-stage manifest."""
    return RollbackEntry(**entry.model_dump())
