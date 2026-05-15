"""Phase 9 / v0.10.0 — eval harness.

The eval suite measures **task-level success**, complementing the unit
tests' code-level success. Each eval task is one YAML file describing:

  * a workspace seed (files to plant before the run)
  * a goal + skill + planner choice
  * expected outputs (paths that must exist after execute)
  * graders (named functions that decide pass/fail against the
    artifacts + trace)

An eval RUN walks the full harness lifecycle in an isolated workspace,
captures the trace stream, then dispatches each grader. The
:class:`EvalResult` aggregates verdicts + a failure-type histogram
from the trace so reports can answer "of the 20 tasks, 4 ended in
``rollback_drift`` and 1 in ``missing_output``."

This is the foundation Phases 10–12 will measure their work against.
The report's framing — "做完 trace + eval 才能量化后续 harness
改造" — is the whole point. v0.10.0 does NOT claim semantic
improvements; it claims a measurement substrate.
"""

from app.eval.report import render_eval_report
from app.eval.runner import discover_tasks, load_task, run_all, run_eval
from app.eval.schema import (
    EvalResult,
    EvalTask,
    GraderContext,
    GraderVerdict,
    WorkspaceFile,
)

__all__ = [
    "EvalResult",
    "EvalTask",
    "GraderContext",
    "GraderVerdict",
    "WorkspaceFile",
    "discover_tasks",
    "load_task",
    "render_eval_report",
    "run_all",
    "run_eval",
]
