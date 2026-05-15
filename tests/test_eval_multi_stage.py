"""Phase 10 — multi-stage EvalTask via EvalTask.stages."""

from __future__ import annotations

from pathlib import Path

from app.eval import discover_tasks, run_eval

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals" / "workspace_pack"


def test_task_007_multi_stage_runs_through_eval_runner(tmp_path: Path) -> None:
    """The v0.11.0 starter multi-stage task passes its 4 graders via
    the standard `run_eval()` entry point — proving the eval runner
    transparently dispatches to the TaskGraph path when ``stages``
    is set."""
    tasks = discover_tasks(EVALS_DIR / "task_007_organize_then_chart.yaml")
    assert len(tasks) == 1
    assert tasks[0].stages is not None
    assert len(tasks[0].stages) == 2

    result = run_eval(tasks[0], tmp_path)
    assert result.passed, [(v.name, v.passed, v.detail) for v in result.grader_verdicts]
    # All 4 graders ran.
    assert {v.name for v in result.grader_verdicts} == {
        "safety_no_forbidden_path",
        "expected_outputs_present",
        "all_files_accounted_for",
        "rollback_restores",
    }


def test_multi_stage_eval_produces_per_stage_artifacts(tmp_path: Path) -> None:
    """The runner must write per-stage plan.json under
    `<localflow>/runs/<task_id>/stages/<stage_id>/`."""
    tasks = discover_tasks(EVALS_DIR / "task_007_organize_then_chart.yaml")
    run_eval(tasks[0], tmp_path)

    runs_dir = tmp_path / "localflow" / "runs"
    run_dir = next(runs_dir.iterdir())
    stages_dir = run_dir / "stages"
    assert (stages_dir / "s1_organize").exists()
    assert (stages_dir / "s2_chart").exists()
    assert (stages_dir / "s1_organize" / "plan.json").exists()
    assert (stages_dir / "s2_chart" / "plan.json").exists()
    # Graph-level artifacts at the top of run_dir.
    assert (run_dir / "taskgraph.json").exists()
    assert (run_dir / "taskgraph_result.json").exists()
    # ONE trace.jsonl for the whole graph.
    assert (run_dir / "trace.jsonl").exists()


def test_existing_single_skill_tasks_still_route_to_single_path(tmp_path: Path) -> None:
    """v0.11.0 invariant: tasks WITHOUT a ``stages`` field continue
    to use the v0.10.x single-skill code path (no TaskGraph, no
    stages/ subdir)."""
    tasks = discover_tasks(EVALS_DIR / "task_001_basic_organize.yaml")
    assert tasks[0].stages is None
    result = run_eval(tasks[0], tmp_path)
    assert result.passed

    runs_dir = tmp_path / "localflow" / "runs"
    run_dir = next(runs_dir.iterdir())
    # Single-skill path doesn't create stages/.
    assert not (run_dir / "stages").exists()
