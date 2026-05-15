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


def run_eval(task: EvalTask, eval_home: Path) -> EvalResult:
    """Run one eval task end-to-end. Returns the structured result.

    Never raises — failures (plan errors, executor crashes, grader
    explosions) get caught and surfaced in ``EvalResult.error`` or as a
    failed grader verdict. A future eval pipeline must be able to
    finish a batch even if individual tasks fail.
    """
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
