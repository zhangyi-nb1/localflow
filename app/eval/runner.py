"""Phase 9 eval runner.

Drives one :class:`EvalTask` through the full harness lifecycle in an
isolated workspace, collects the trace + artifacts, and dispatches
every grader the task lists.

Design rules:

* **Isolated workspace** — never use a user-supplied path; the runner
  always creates a fresh tmpdir under ``eval_home`` and plants
  ``task.workspace_seed`` files into it. Eval tasks must be safe to
  run on any developer machine.
* **Isolated RunStore** — the runner uses its own ``localflow_home``
  (also under ``eval_home``) so eval runs never pollute the user's
  real ``~/.localflow/runs/`` history.
* **Trace mandatory** — the runner ALWAYS attaches a TraceLogger; the
  grader set needs the trace stream to function. Eval is the
  motivating use case for the trace layer.
* **Rule planner by default** — the deterministic path runs in CI
  with no API key. Tasks that need LLM set ``planner: llm`` and
  must accept the runner skipping them in offline mode (v0.10.1+).
"""

from __future__ import annotations

import base64
import time
import traceback
from pathlib import Path

import yaml

from app.eval.graders import get as get_grader
from app.eval.schema import (
    EvalResult,
    EvalTask,
    GraderContext,
    GraderVerdict,
    WorkspaceFile,
)
from app.harness import control_loop
from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.harness.trace import TraceLogger
from app.schemas import TaskSpec
from app.skills import get_default_registry
from app.storage.run_store import RunStore
from app.tools.hash_ops import sha256_file


def load_task(path: Path) -> EvalTask:
    """Parse a YAML file (or a directory entry) into an EvalTask."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: eval task YAML must be a mapping at the top level")
    return EvalTask.model_validate(raw)


def discover_tasks(target: Path) -> list[EvalTask]:
    """Accept a single .yaml file or a directory of them."""
    target = Path(target)
    if target.is_file():
        return [load_task(target)]
    if not target.is_dir():
        raise FileNotFoundError(f"eval target does not exist: {target}")
    yamls = sorted(p for p in target.iterdir() if p.suffix.lower() in (".yaml", ".yml"))
    return [load_task(p) for p in yamls]


def run_eval(
    task: EvalTask,
    eval_home: Path,
    *,
    enable_auto_repair: bool = False,
    max_auto_repairs: int = 2,
) -> EvalResult:
    """Run one eval task end-to-end. Returns the structured result.

    Never raises — failures (plan errors, executor crashes, grader
    explosions) get caught and surfaced in ``EvalResult.error`` or as a
    failed grader verdict. A future eval pipeline must be able to
    finish a batch even if individual tasks fail.

    v0.11.0: when ``task.stages`` is set, this dispatches to the
    multi-stage TaskGraph path. Otherwise, single-skill behaviour
    unchanged from v0.10.x.

    v0.13.0: when ``enable_auto_repair`` is True, the runner wires the
    semantic verifier + auto-repair loop in place of the plain execute
    path. The structural pipeline (plan → policy → dry-run) is
    unchanged. ``--compare-repair`` runs each task twice (once with
    enable_auto_repair=False, once True) to measure the loop's impact.
    """
    if task.stages is not None:
        return _run_multi_stage_eval(task, eval_home)
    started = time.perf_counter()
    eval_home = Path(eval_home).resolve()
    eval_home.mkdir(parents=True, exist_ok=True)

    workspace_path = eval_home / "workspaces" / task.task_id
    workspace_path.mkdir(parents=True, exist_ok=True)
    # Clean any leftover state from a previous run of the same task.
    _wipe_dir_contents(workspace_path)

    localflow_home = eval_home / "localflow"
    run_store = RunStore.create(home=localflow_home)
    trace = TraceLogger(run_store.trace_path)

    try:
        _plant_seed(workspace_path, task.workspace_seed)
        seed_hashes = _hash_seed(workspace_path, task.workspace_seed)
        registry = get_default_registry()
        skill = registry.require(task.skill)

        task_spec = TaskSpec(
            task_id=run_store.task_id,
            user_goal=task.goal,
            workspace_root=str(workspace_path),
            skill=task.skill,
            allowed_actions=list(skill.manifest.allowed_actions),
            forbidden_actions=list(task.forbidden_actions),
            forbidden_paths=list(task.forbidden_paths),
        )
        run_store.save_task(task_spec)

        snapshot_before = control_loop.run_inspect(
            workspace_path,
            task_id=run_store.task_id,
            compute_hash=True,
            compute_preview=False,
        )
        run_store.save_workspace(snapshot_before)

        if task.planner == "llm":
            plan = skill.plan_with_llm(task_spec, snapshot_before, trace=trace)
        else:
            plan = skill.plan(task_spec, snapshot_before)
        skill.validate(plan)
        run_store.save_plan(plan)

        assessment = control_loop.run_risk_check(task_spec, plan, trace=trace)
        control_loop.run_dry_run(task_spec, plan, assessment, run_store, trace=trace)

        if enable_auto_repair:
            # Phase 13 — go through the auto-repair-capable orchestrator
            # so a semantic-grader rejection triggers rollback + revise
            # + re-execute up to ``max_auto_repairs`` times.
            (
                plan,
                outcome,
                verification,
                _semantic,
                _repair_outcome,
            ) = control_loop.run_with_auto_repair(
                task_spec,
                plan,
                snapshot_before,
                skill=skill,
                run_store=run_store,
                approved=True,
                enable_semantic=True,
                max_auto_repairs=max_auto_repairs,
                trace=trace,
            )
        else:
            executor = Executor(
                workspace_root=workspace_path,
                run_store=run_store,
                forbidden_actions=tuple(task_spec.forbidden_actions),
                forbidden_paths=tuple(task_spec.forbidden_paths),
                trace=trace,
            )
            outcome = executor.execute(plan, approved=True)
            verification = control_loop.run_verify(
                task_spec, plan, run_store, outcome, snapshot_before, trace=trace
            )

        # Run pre-rollback graders first.
        ctx_pre = GraderContext(
            task=task,
            task_spec=task_spec,
            plan=plan,
            snapshot_before=snapshot_before,
            snapshot_after=None,
            execution_records=outcome.records,
            manifest=outcome.manifest,
            verification=verification,
            trace_events=trace.read_all(),
            workspace_path=workspace_path,
            seed_hashes=seed_hashes,
        )

        verdicts: list[GraderVerdict] = []
        # Run all non-rollback graders first so they see post-execute state.
        rollback_graders: list[str] = []
        for name in task.graders:
            if name == "rollback_restores":
                rollback_graders.append(name)
                continue
            verdicts.append(_run_grader(name, ctx_pre))

        # Now rollback (if requested) and run the rollback-dependent graders.
        if rollback_graders:
            rb = Rollback(workspace_root=workspace_path, run_store=run_store, trace=trace)
            rb.run(outcome.manifest, force=False)
            ctx_post = GraderContext(
                task=task,
                task_spec=task_spec,
                plan=plan,
                snapshot_before=snapshot_before,
                snapshot_after=None,
                execution_records=outcome.records,
                manifest=outcome.manifest,
                verification=verification,
                trace_events=trace.read_all(),
                workspace_path=workspace_path,
                seed_hashes=seed_hashes,
            )
            for name in rollback_graders:
                verdicts.append(_run_grader(name, ctx_post))

        # Aggregate the failure-type histogram from the trace.
        failure_summary: dict[str, int] = {}
        for evt in trace.read_all():
            if evt.failure_type is None:
                continue
            key = evt.failure_type.value
            failure_summary[key] = failure_summary.get(key, 0) + 1

        passed = _aggregate_passed(task, verdicts)
        return EvalResult(
            task_id=task.task_id,
            title=task.title,
            passed=passed,
            grader_verdicts=verdicts,
            run_id=run_store.task_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            failure_summary=failure_summary,
        )
    except Exception as exc:  # pragma: no cover — top-level safety net
        return EvalResult(
            task_id=task.task_id,
            title=task.title,
            passed=False,
            grader_verdicts=[],
            run_id=run_store.task_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            failure_summary={},
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}",
        )


# ───────────────────────────────────── internals


def _wipe_dir_contents(path: Path) -> None:
    """Remove files in ``path`` but keep the directory itself."""
    import shutil

    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _plant_seed(workspace_path: Path, seed: list[WorkspaceFile]) -> None:
    for wf in seed:
        abs_path = workspace_path / wf.path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if wf.text is not None:
            abs_path.write_text(wf.text, encoding="utf-8")
        else:
            assert wf.bytes_b64 is not None
            abs_path.write_bytes(base64.b64decode(wf.bytes_b64))


def _hash_seed(workspace_path: Path, seed: list[WorkspaceFile]) -> dict[str, str]:
    out: dict[str, str] = {}
    for wf in seed:
        abs_path = workspace_path / wf.path
        if abs_path.exists():
            out[wf.path] = sha256_file(abs_path)
    return out


def _run_grader(name: str, ctx: GraderContext) -> GraderVerdict:
    """Run one grader; trap exceptions so a buggy grader doesn't sink
    the whole eval result."""
    try:
        fn = get_grader(name)
    except KeyError as exc:
        return GraderVerdict(name=name, passed=False, detail=str(exc))
    try:
        return fn(ctx)
    except Exception as exc:  # pragma: no cover
        return GraderVerdict(
            name=name,
            passed=False,
            detail=f"grader crashed: {type(exc).__name__}: {exc}",
        )


def _aggregate_passed(task: EvalTask, verdicts: list[GraderVerdict]) -> bool:
    """A task passes iff every grader in ``task.must_pass`` is OK, OR
    if ``must_pass`` is empty, every grader passed."""
    must_pass = set(task.must_pass) if task.must_pass else {v.name for v in verdicts}
    by_name = {v.name: v for v in verdicts}
    for name in must_pass:
        v = by_name.get(name)
        if v is None or not v.passed:
            return False
    return True


# Convenience for the CLI: scan a tasks-dir and run them all.


def run_all(tasks_target: Path, eval_home: Path) -> list[EvalResult]:
    """Helper used by the CLI command. Iterates :func:`discover_tasks`
    and runs each through :func:`run_eval`."""
    return [run_eval(t, eval_home) for t in discover_tasks(tasks_target)]


# ───────────────────────────────────── v0.11.0 multi-stage path


def _run_multi_stage_eval(task: EvalTask, eval_home: Path) -> EvalResult:
    """Drive a multi-stage EvalTask through the TaskGraphRunner.

    Mirrors :func:`run_eval`'s contract: never raises; populates the
    same EvalResult shape. The difference is that the underlying
    pipeline is a TaskGraph (each stage = one skill invocation) rather
    than a single skill.plan() call.
    """
    from app.harness.taskgraph_runner import run_taskgraph
    from app.schemas import TaskGraph

    started = time.perf_counter()
    eval_home = Path(eval_home).resolve()
    eval_home.mkdir(parents=True, exist_ok=True)

    workspace_path = eval_home / "workspaces" / task.task_id
    workspace_path.mkdir(parents=True, exist_ok=True)
    _wipe_dir_contents(workspace_path)

    localflow_home = eval_home / "localflow"
    run_store = RunStore.create(home=localflow_home)
    trace = TraceLogger(run_store.trace_path)

    try:
        _plant_seed(workspace_path, task.workspace_seed)
        seed_hashes = _hash_seed(workspace_path, task.workspace_seed)

        graph = TaskGraph(
            task_id=run_store.task_id,
            user_goal=task.goal,
            workspace_root=str(workspace_path),
            stages=task.stages or [],
            forbidden_actions=list(task.forbidden_actions),
            forbidden_paths=list(task.forbidden_paths),
        )

        tg_result = run_taskgraph(graph, run_store, trace=trace, approved=True)

        # Build a GraderContext from the graph-level artifacts. We
        # reconstruct execution_records by reading each stage's
        # actions.json — this is the same data the single-skill path
        # produces in outcome.records, just aggregated across stages.
        execution_records = _read_aggregated_execution_records(run_store, graph)
        manifest = run_store.load_rollback()

        # Synthesise an aggregated plan whose actions = union of every
        # stage's actions. Graders like `all_files_accounted_for` read
        # plan.actions looking for MOVE entries; in multi-stage mode,
        # the moves live in stage 1's plan while the final filesystem
        # state reflects stage N's outputs. The aggregated plan
        # bridges both views.
        plan = _aggregate_stage_plans(run_store, graph)
        # Use the FIRST stage's snapshot (= pre-execute workspace
        # state, what the seed_hashes match).
        first_stage = graph.stages[0]
        first_stage_store = run_store.stage_dir(first_stage.stage_id)
        snapshot = _load_stage_snapshot(first_stage_store)

        # Synthesise expected_outputs by unioning task-level + per-stage
        # so graders see everything any stage promised.
        per_task_outputs = list(task.expected_outputs)
        for stage in graph.stages:
            per_task_outputs.extend(stage.expected_outputs)
        task_for_grader = task.model_copy(update={"expected_outputs": per_task_outputs})

        ctx_pre = GraderContext(
            task=task_for_grader,
            task_spec=_synthesize_task_spec_for_grader(run_store, graph),
            plan=plan,
            snapshot_before=snapshot,
            snapshot_after=None,
            execution_records=execution_records,
            manifest=manifest,
            verification=None,
            trace_events=trace.read_all(),
            workspace_path=workspace_path,
            seed_hashes=seed_hashes,
        )

        verdicts: list[GraderVerdict] = []
        rollback_graders: list[str] = []
        for name in task.graders:
            if name == "rollback_restores":
                rollback_graders.append(name)
                continue
            verdicts.append(_run_grader(name, ctx_pre))

        if rollback_graders:
            from app.harness.rollback import Rollback

            rb = Rollback(workspace_root=workspace_path, run_store=run_store, trace=trace)
            rb.run(manifest, force=False)
            ctx_post = GraderContext(
                task=task_for_grader,
                task_spec=ctx_pre.task_spec,
                plan=plan,
                snapshot_before=snapshot,
                snapshot_after=None,
                execution_records=execution_records,
                manifest=manifest,
                verification=None,
                trace_events=trace.read_all(),
                workspace_path=workspace_path,
                seed_hashes=seed_hashes,
            )
            for name in rollback_graders:
                verdicts.append(_run_grader(name, ctx_post))

        failure_summary: dict[str, int] = {}
        for evt in trace.read_all():
            if evt.failure_type is None:
                continue
            key = evt.failure_type.value
            failure_summary[key] = failure_summary.get(key, 0) + 1

        passed = _aggregate_passed(task, verdicts) and tg_result.passed
        return EvalResult(
            task_id=task.task_id,
            title=task.title,
            passed=passed,
            grader_verdicts=verdicts,
            run_id=run_store.task_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            failure_summary=failure_summary,
        )
    except Exception as exc:  # pragma: no cover — top-level safety net
        return EvalResult(
            task_id=task.task_id,
            title=task.title,
            passed=False,
            grader_verdicts=[],
            run_id=run_store.task_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            failure_summary={},
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}",
        )


def _read_aggregated_execution_records(run_store, graph) -> list:
    """Read each stage's actions.json into one combined list of
    ExecutionRecord. Stages that didn't run leave nothing."""
    from app.schemas import ExecutionRecord

    out: list = []
    for stage in graph.stages:
        actions_json = run_store.stage_dir(stage.stage_id) / "actions.json"
        if not actions_json.exists():
            continue
        import json

        raw = json.loads(actions_json.read_text(encoding="utf-8"))
        for rec in raw:
            try:
                out.append(ExecutionRecord.model_validate(rec))
            except Exception:
                continue
    return out


def _load_stage_plan(stage_dir: Path, stage_id: str):
    """Load the stage's plan.json for the grader context."""
    from app.schemas import ActionPlan

    plan_path = stage_dir / "plan.json"
    if not plan_path.exists():
        # Synthesize an empty plan as a fallback — the multi-stage
        # path occasionally needs a placeholder when the last stage
        # was ABORTED.
        return ActionPlan(plan_id=f"plan-empty-{stage_id}", task_id="t", summary="empty")
    import json

    return ActionPlan.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))


def _aggregate_stage_plans(run_store, graph):
    """Concatenate every stage's plan.json actions into one synthetic
    ActionPlan. Graders that read ``plan.actions`` (e.g.
    ``all_files_accounted_for``) need a unified view across stages —
    in v0.11.0 multi-stage mode each stage's moves live in its own
    plan.json, but the final workspace state is the result of all of
    them combined.

    Source paths get rewritten from the post-stage-1 names back into
    the original seed paths where possible: stage 1's actions use the
    seed-relative paths (since they ran first), so we just concatenate
    in order and the seed path → final path mapping falls out
    naturally.
    """
    from app.schemas import ActionPlan

    actions: list = []
    expected_outputs: list[str] = []
    for stage in graph.stages:
        stage_plan = _load_stage_plan(run_store.stage_dir(stage.stage_id), stage.stage_id)
        actions.extend(stage_plan.actions)
        expected_outputs.extend(stage_plan.expected_outputs)
    return ActionPlan(
        plan_id=f"plan-aggregated-{run_store.task_id}",
        task_id=run_store.task_id,
        summary=f"Aggregated across {len(graph.stages)} stages",
        actions=actions,
        expected_outputs=expected_outputs,
    )


def _load_stage_snapshot(stage_dir: Path):
    """Load the stage's workspace_snapshot.json."""
    from app.schemas import WorkspaceSnapshot

    snap_path = stage_dir / "workspace_snapshot.json"
    if not snap_path.exists():
        return WorkspaceSnapshot(snapshot_id="s", task_id="t", root="/fake", files=[])
    import json

    return WorkspaceSnapshot.model_validate(json.loads(snap_path.read_text(encoding="utf-8")))


def _synthesize_task_spec_for_grader(run_store, graph):
    """Build a TaskSpec for the grader context — graders mostly read
    task_spec.workspace_root / forbidden_paths, so a flat one suffices."""
    from app.schemas import TaskSpec

    return TaskSpec(
        task_id=run_store.task_id,
        user_goal=graph.user_goal,
        workspace_root=graph.workspace_root,
        skill="taskgraph",  # synthetic; graders don't dispatch on it
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
        forbidden_actions=list(graph.forbidden_actions),
        forbidden_paths=list(graph.forbidden_paths),
    )
