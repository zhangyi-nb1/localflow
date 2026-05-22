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
    VerificationResult,
    WorkspaceSnapshot,
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
    persist_graph: bool = True,
) -> TaskGraphResult:
    """Walk ``graph.stages`` sequentially. Returns a structured
    :class:`TaskGraphResult` regardless of success / failure.

    The runner NEVER raises through the call boundary — any stage
    crash is caught, recorded on its :class:`StageResult.error`, and
    the runner consults ``failure_policy`` to decide what to do.

    ``persist_graph`` (Phase 21.1): when False, skip writing
    ``taskgraph.json``. Used by :func:`replay_from_stage` so the
    truncated sub-graph it constructs doesn't overwrite the original
    (full) graph spec already on disk — preserves audit trail.
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
    if persist_graph:
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
        # Phase 20: surface the stage's declared deliverables to the
        # plan() / plan_with_llm() call so the LLM (or rule planner)
        # sees the contract. The agent meta-skill uses this to ensure
        # both README.md AND SOURCES.md get generated, fixing a Phase
        # 19 regression where the LLM only produced README.
        expected_outputs=list(stage.expected_outputs),
        # v0.22 — propagate graph-level locale so every stage's LLM
        # planner produces user-facing prose in the user's language.
        locale=graph.locale,
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
            # Phase 21 — recipe auto-repair injects a user_hint here
            # when re-planning a stage to fix a failed deliverable
            # verifier. Skills that don't accept user_hint can ignore
            # it (the meta-skill agent does accept it).
            llm_kwargs: dict = {"trace": trace}
            hint = graph.stage_hints.get(stage.stage_id)
            if hint:
                llm_kwargs["user_hint"] = hint
            plan = skill.plan_with_llm(sub_task, snapshot, **llm_kwargs)
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

        # Phase 13 — when the stage opts into REPAIR policy, run the
        # semantic verifier in-stage; on rejection, kick the auto-repair
        # loop (bounded by stage.max_retries). The repair loop may swap
        # plan / outcome / verification for the post-repair state; we
        # mirror those back so the StageResult and the aggregated
        # manifest reflect the FINAL state, not the pre-repair one.
        if stage.failure_policy == StageFailurePolicy.REPAIR and verification.passed:
            plan, outcome, verification = _maybe_run_stage_repair(
                sub_task=sub_task,
                plan=plan,
                outcome=outcome,
                verification=verification,
                snapshot=snapshot,
                skill=skill,
                stage_store=stage_store,
                max_attempts=max(stage.max_retries, 1),
                trace=trace,
                aggregated=aggregated,
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


def replay_from_stage(
    *,
    graph: TaskGraph,
    run_store: RunStore,
    from_stage: str,
    trace: TraceLogger | None = None,
) -> TaskGraphResult:
    """Phase 15 — cross-stage repair: roll back every stage from
    ``from_stage`` onwards (inclusive) and replay them.

    Use case: stage 3's failure traces back to stage 1's wrong output.
    The user (or a future auto-loop) decides "we need to redo from
    stage 1 with a hint". This function:

      1. Loads the existing aggregated manifest.
      2. Filters entries to ones produced by ``from_stage`` and every
         stage downstream of it. Rollbacks them in reverse order.
      3. Re-runs ``run_taskgraph`` with the same graph but only the
         affected stages — UPSTREAM stages are left alone.

    Returns a fresh :class:`TaskGraphResult` reflecting just the
    replayed stage range. The aggregated rollback_manifest.json on
    disk is rewritten to combine the surviving upstream entries +
    the replayed entries (so a subsequent full `localflow rollback`
    still undoes everything from a clean state).
    """
    from app.harness.rollback import Rollback, filter_manifest_to_stage

    stage_ids = [s.stage_id for s in graph.stages]
    if from_stage not in stage_ids:
        raise ValueError(f"replay_from_stage: {from_stage!r} not in graph stages: {stage_ids}")
    pivot = stage_ids.index(from_stage)
    affected = stage_ids[pivot:]

    # Existing manifest. Filter into upstream (kept) + affected (rolled back).
    existing = run_store.load_rollback() if run_store.exists(run_store.ROLLBACK_JSON) else None
    if existing is None:
        raise RuntimeError("replay_from_stage: no existing rollback manifest to filter")

    affected_entries = []
    for sid in affected:
        affected_entries.extend(filter_manifest_to_stage(existing, sid).entries)
    upstream_entries = [
        e
        for e in existing.entries
        if not any(e.action_id.startswith(f"{sid}.") for sid in affected)
    ]

    # Roll back affected entries.
    affected_manifest = existing.model_copy(update={"entries": affected_entries})
    rollback = Rollback(
        workspace_root=Path(graph.workspace_root),
        run_store=run_store,
        trace=trace,
    )
    rb_outcome = rollback.run(affected_manifest, force=False)
    if rb_outcome.conflicts:
        raise RuntimeError(
            f"replay_from_stage: rollback halted on drift in affected stages: "
            f"{[c['action_id'] for c in rb_outcome.conflicts]}"
        )

    # Replay just the affected slice as a fresh sub-graph. Each replayed
    # stage's action_ids get re-prefixed as usual, so the new manifest's
    # entries don't collide with kept upstream entries (different stage
    # prefixes already disambiguate).
    sub_graph = graph.model_copy(update={"stages": graph.stages[pivot:]})
    # Phase 21.1: persist_graph=False — the truncated sub_graph must not
    # overwrite the original taskgraph.json on disk (audit trail).
    sub_result = run_taskgraph(
        sub_graph,
        run_store=run_store,
        trace=trace,
        approved=True,
        persist_graph=False,
    )

    # Re-stitch the manifest: upstream entries + the new affected entries.
    new_aggregated = run_store.load_rollback()
    merged_entries = upstream_entries + list(new_aggregated.entries)
    merged = new_aggregated.model_copy(update={"entries": merged_entries})
    run_store.save_rollback(merged)
    return sub_result


def _maybe_run_stage_repair(
    *,
    sub_task: TaskSpec,
    plan: ActionPlan,
    outcome,
    verification: VerificationResult,
    snapshot: WorkspaceSnapshot,
    skill,
    stage_store: RunStore,
    max_attempts: int,
    trace: TraceLogger | None,
    aggregated: RollbackManifest,
):
    """Phase 13 — invoke the semantic verifier + auto-repair loop for
    one stage. Returns the (possibly repaired) ``(plan, outcome, verification)``.

    Imported lazily so installs without the [data] / openai extras can
    still load taskgraph_runner — semantic verifier triggers an LLM
    client probe that only happens when REPAIR policy is set.
    """
    from app.harness.repair_loop import run_repair_loop
    from app.harness.semantic_verifier import SemanticVerifier

    workspace_root = Path(sub_task.workspace_root)
    semantic_verifier = SemanticVerifier(workspace_root, trace=trace)
    semantic = semantic_verifier.verify(
        task=sub_task,
        plan=plan,
        execution_records=outcome.records,
        manifest=outcome.manifest,
        snapshot_before=snapshot,
        snapshot_after=None,
        structural=verification,
        run_id=outcome.run_id,
    )
    stage_store.write_model(stage_store.semantic_verify_path, semantic)
    if semantic.passed or not semantic.auto_repair_eligible:
        return plan, outcome, verification

    final_plan, state, _repair_outcome = run_repair_loop(
        sub_task,
        snapshot=snapshot,
        current_plan=plan,
        current_outcome=outcome,
        current_structural=verification,
        current_semantic=semantic,
        skill=skill,
        run_store=stage_store,
        max_attempts=max_attempts,
        trace=trace,
    )
    # Merge any rollback-then-re-execute manifest entries into the
    # aggregated graph-level manifest — the original execution's entries
    # were already merged, but repair adds new ones.
    if state.outcome is not outcome:
        _merge_manifest(aggregated, state.outcome.manifest)
    return final_plan, state.outcome, state.structural


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
