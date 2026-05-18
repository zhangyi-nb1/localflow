"""Phase 9 — eval runner end-to-end.

Pin the runner's behaviour on the 3 shipped starter tasks.
"""

from __future__ import annotations

from pathlib import Path

from app.eval import discover_tasks, render_eval_report, run_eval

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals" / "workspace_pack"


def test_discover_loads_all_starter_tasks() -> None:
    """v0.10.0 shipped 3; v0.10.1 grew to 6; v0.11.0 added task_007
    (first multi-stage); v0.14.0 adds task_010 (the 5-stage Workspace
    Pack Builder demo). If someone adds more or removes one, this test
    updates explicitly so suite growth stays deliberate."""
    tasks = discover_tasks(EVALS_DIR)
    task_ids = {t.task_id for t in tasks}
    assert task_ids == {
        "task_001_basic_organize",
        "task_002_compound_chart",
        "task_003_forbidden_path_blocked",
        "task_004_forbidden_action_blocked",
        "task_005_empty_workspace",
        "task_006_duplicate_files_reported",
        "task_007_organize_then_chart",
        "task_010_workspace_pack",
    }


def test_task_004_pipeline_does_not_emit_forbidden_actions(tmp_path: Path) -> None:
    """v0.10.1 regression: even when forbidden_actions explicitly
    contains delete/overwrite, folder_organizer's rule planner must
    never emit them. The whole pipeline runs cleanly + all graders pass."""
    tasks = discover_tasks(EVALS_DIR / "task_004_forbidden_action_blocked.yaml")
    result = run_eval(tasks[0], tmp_path)
    assert result.passed, [(v.name, v.passed, v.detail) for v in result.grader_verdicts]


def test_task_005_empty_workspace_doesnt_crash(tmp_path: Path) -> None:
    """Empty workspace → zero-action plan → harness completes cleanly,
    rollback is a no-op, every grader trivially passes."""
    tasks = discover_tasks(EVALS_DIR / "task_005_empty_workspace.yaml")
    result = run_eval(tasks[0], tmp_path)
    assert result.passed, [(v.name, v.passed, v.detail) for v in result.grader_verdicts]


def test_task_006_duplicate_files_reported(tmp_path: Path) -> None:
    """Two byte-identical files → duplicates_report.md gets written +
    both files moved into the same category, no delete attempted.
    This is the v0.10.1 regression test for the 'never delete' rule."""
    tasks = discover_tasks(EVALS_DIR / "task_006_duplicate_files_reported.yaml")
    result = run_eval(tasks[0], tmp_path)
    assert result.passed, [(v.name, v.passed, v.detail) for v in result.grader_verdicts]


def test_task_001_runs_clean(tmp_path: Path) -> None:
    """The basic organize task should pass every grader on a fresh
    workspace."""
    tasks = discover_tasks(EVALS_DIR / "task_001_basic_organize.yaml")
    assert len(tasks) == 1
    result = run_eval(tasks[0], tmp_path)
    assert result.passed, [(v.name, v.passed, v.detail) for v in result.grader_verdicts]
    assert {v.name for v in result.grader_verdicts} == {
        "safety_no_forbidden_path",
        "expected_outputs_present",
        "all_files_accounted_for",
        "rollback_restores",
    }
    assert result.run_id is not None
    assert result.duration_ms > 0


def test_task_003_proves_policy_guard_kicks_in(tmp_path: Path) -> None:
    """task_003's must_pass set excludes 'all_files_accounted_for'
    (which fails because folder_organizer plans a move that
    policy_guard then blocks). The overall task PASSES because
    safety_no_forbidden_path + rollback_restores are the contract."""
    tasks = discover_tasks(EVALS_DIR / "task_003_forbidden_path_blocked.yaml")
    result = run_eval(tasks[0], tmp_path)
    assert result.passed
    # The failure-type histogram must record the blocked attempt.
    assert result.failure_summary.get("path_forbidden", 0) >= 1


def test_report_renders_summary_and_per_task_sections(tmp_path: Path) -> None:
    tasks = discover_tasks(EVALS_DIR)
    results = [run_eval(t, tmp_path / t.task_id) for t in tasks]
    md = render_eval_report(results)
    assert "# LocalFlow eval report" in md
    assert "## Summary" in md
    assert "## Per task" in md
    for r in results:
        assert r.task_id in md
        assert r.title in md
    # The histogram heading appears iff at least one failure type was
    # recorded — and task_003 always records path_forbidden.
    assert "Failure-type histogram" in md


def test_run_eval_traps_runner_errors(tmp_path: Path) -> None:
    """A bogus skill name should produce a failed EvalResult with an
    error string — NOT an uncaught exception. Eval batches need to
    finish even when one task is broken."""
    from app.eval import EvalTask

    bogus = EvalTask(
        task_id="t-broken",
        title="bogus skill",
        goal="g",
        skill="this_skill_does_not_exist",
        graders=["expected_outputs_present"],
    )
    result = run_eval(bogus, tmp_path)
    assert not result.passed
    assert result.error is not None
    assert "this_skill_does_not_exist" in result.error
