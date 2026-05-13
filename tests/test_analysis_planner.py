"""Phase 3.3b — LLM-driven AnalysisSpec planner tests.

All LLM I/O is faked via ``FakeLLMClient`` (zero real API calls). The
purpose is to exercise:

  * happy path: LLM emits a valid spec → engine runs → ActionPlan
  * repair: LLM emits invalid column → repair turn → second attempt OK
  * failure: LLM never converges → ``AnalysisPlannerFailure``
  * dispatch: ``DataAnalyzerSkill.plan_with_llm`` actually calls the
    LLM planner (CLI integration sanity check)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.agent.analysis_planner import (
    AnalysisPlannerFailure,
    plan_analysis_with_llm,
)
from app.agent.client import FakeLLMClient
from app.schemas import TaskSpec
from app.skills.data_analyzer import DataAnalyzerSkill
from app.tools.file_scan import scan_workspace


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def sales_workspace(tmp_path: Path) -> Path:
    """Workspace with one CSV ready to analyze."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sales.csv").write_text(
        "region,product,amount\n"
        "N,A,10\nN,B,20\nS,A,30\nS,B,40\nE,A,50\nW,B,60\n",
        encoding="utf-8",
    )
    return ws


@pytest.fixture
def sales_snapshot_and_task(sales_workspace: Path):
    snap = scan_workspace(sales_workspace, "tt", compute_preview=False)
    task = TaskSpec(
        task_id="tt",
        user_goal="Compare avg amount by region",
        workspace_root=str(sales_workspace),
        skill="data_analyzer",
    )
    return snap, task


def _valid_payload() -> dict[str, Any]:
    """The shape the LLM is supposed to emit, matching our hand-written
    tool schema (aggregations as a LIST of {column, op}, not a dict)."""
    return {
        "source_file": "sales.csv",
        "sheet": None,
        "filters": [],
        "groupby": {
            "by": ["region"],
            "aggregations": [{"column": "amount", "op": "mean"}],
        },
        "sort_by": ["amount"],
        "sort_descending": True,
        "limit": None,
        "chart": {
            "kind": "bar",
            "x": "region",
            "y": "amount",
            "title": "Mean amount by region",
        },
    }


# --------------------------------------------------------------------- happy path


def test_llm_happy_path_produces_plan(sales_snapshot_and_task) -> None:
    snap, task = sales_snapshot_and_task
    client = FakeLLMClient(payloads=[_valid_payload()])

    plan = plan_analysis_with_llm(task, snap, client=client, max_attempts=2)

    # 1 report action + 1 chart action (bar chart from groupby).
    assert len(plan.actions) >= 1
    report = next(a for a in plan.actions if a.target_path == "analysis_report.md")
    assert "content" in report.metadata
    assert "Mean amount by region" in report.metadata["content"] or "amount" in report.metadata["content"]


def test_llm_picks_correct_source_file(sales_snapshot_and_task) -> None:
    snap, task = sales_snapshot_and_task
    client = FakeLLMClient(payloads=[_valid_payload()])
    plan = plan_analysis_with_llm(task, snap, client=client)
    # Provenance records the spec the LLM chose.
    prov = plan.actions[0].metadata["provenance"]
    assert prov["analyses"][0]["source_file"] == "sales.csv"
    assert prov["analyses"][0]["outcome"] == "ok"


# --------------------------------------------------------------------- repair


def test_repair_loop_handles_unknown_column(sales_snapshot_and_task) -> None:
    """First payload references a column that doesn't exist; semantic
    validation catches it and we send a repair turn. Second payload is
    valid."""
    snap, task = sales_snapshot_and_task
    bad = _valid_payload()
    bad["groupby"]["by"] = ["nonexistent_column"]
    good = _valid_payload()

    client = FakeLLMClient(payloads=[bad, good])
    plan = plan_analysis_with_llm(task, snap, client=client, max_attempts=3)

    # Should have made 2 calls (bad → repair turn → good).
    assert len(client.calls) == 2
    # Second call must include the repair feedback as a tool_result.
    second_msgs = client.calls[1]["messages"]
    last = second_msgs[-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["is_error"] is True
    # And the plan came out valid.
    assert plan.actions[0].target_path == "analysis_report.md"


def test_repair_loop_handles_missing_source_file(sales_snapshot_and_task) -> None:
    snap, task = sales_snapshot_and_task
    bad = _valid_payload()
    bad["source_file"] = "does-not-exist.csv"
    good = _valid_payload()
    client = FakeLLMClient(payloads=[bad, good])
    plan = plan_analysis_with_llm(task, snap, client=client, max_attempts=3)
    assert len(client.calls) == 2
    assert plan.actions[0].metadata["provenance"]["analyses"][0]["source_file"] == "sales.csv"


# --------------------------------------------------------------------- failure


def test_llm_gives_up_after_max_attempts(sales_snapshot_and_task) -> None:
    snap, task = sales_snapshot_and_task
    bad = _valid_payload()
    bad["source_file"] = "does-not-exist.csv"
    client = FakeLLMClient(payloads=[bad, bad, bad])

    with pytest.raises(AnalysisPlannerFailure) as excinfo:
        plan_analysis_with_llm(task, snap, client=client, max_attempts=3)
    assert "does-not-exist.csv" in str(excinfo.value)
    assert len(client.calls) == 3


# --------------------------------------------------------------------- skill dispatch


def test_skill_plan_with_llm_uses_analysis_planner(sales_snapshot_and_task) -> None:
    """Phase 2.3 contract: ``skill.plan_with_llm`` is the entry point the
    CLI calls. Confirm DataAnalyzerSkill routes to plan_analysis_with_llm."""
    snap, task = sales_snapshot_and_task
    client = FakeLLMClient(payloads=[_valid_payload()])
    skill = DataAnalyzerSkill()
    plan = skill.plan_with_llm(task, snap, client=client, max_attempts=2)
    # Same return contract as the rule path: an ActionPlan with
    # exactly one analysis_report.md action plus optional charts.
    assert plan.actions[0].target_path == "analysis_report.md"
    assert plan.actions[0].metadata["provenance"]["synthesis_kind"] == "data_analysis"


def test_skill_supports_llm_reports_true() -> None:
    """Phase 2.3's supports_llm() reflection: it must return True now
    that we've overridden plan_with_llm."""
    skill = DataAnalyzerSkill()
    assert skill.supports_llm() is True


# --------------------------------------------------------------------- aggregation translation


def test_aggregations_list_unpacked_to_dict(sales_snapshot_and_task) -> None:
    """The LLM emits aggregations as ``[{column, op}, ...]`` per our
    strict-mode schema. The coercion step must turn that into the
    ``dict[str, AggregationOp]`` AnalysisSpec expects."""
    snap, task = sales_snapshot_and_task
    payload = _valid_payload()
    payload["groupby"]["aggregations"] = [
        {"column": "amount", "op": "sum"},
        {"column": "amount", "op": "max"},  # multi-agg same column not supported, but coercion still runs
    ]
    client = FakeLLMClient(payloads=[payload])
    plan = plan_analysis_with_llm(task, snap, client=client)
    # Plan succeeded → coercion worked.
    assert plan.actions[0].target_path == "analysis_report.md"


# --------------------------------------------------------------------- end-to-end via executor


def test_llm_plan_runs_through_executor(sales_workspace, sales_snapshot_and_task, tmp_path) -> None:
    """Phase 3.3b / outline §10.7: confirm the LLM path still produces
    a plan the harness can execute end-to-end. 7th implementation of
    the 'new skill doesn't touch Harness Kernel' rule."""
    from app.harness.executor import Executor
    from app.harness.verifier import Verifier
    from app.schemas import ExecutionStatus
    from app.storage.run_store import RunStore
    import os

    snap, task = sales_snapshot_and_task
    os.environ["LOCALFLOW_HOME"] = str(tmp_path / ".lf")
    store = RunStore.create()
    task = TaskSpec(
        task_id=store.task_id, user_goal=task.user_goal,
        workspace_root=task.workspace_root, skill="data_analyzer",
    )
    store.save_task(task)
    store.save_workspace(snap)

    client = FakeLLMClient(payloads=[_valid_payload()])
    skill = DataAnalyzerSkill()
    plan = skill.plan_with_llm(task, snap, client=client, max_attempts=2)
    skill.validate(plan)
    store.save_plan(plan)

    executor = Executor(workspace_root=sales_workspace, run_store=store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    succ = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    assert succ == len(plan.actions)

    # Workspace artifact exists and mentions the source.
    report_text = (sales_workspace / "analysis_report.md").read_text(encoding="utf-8")
    assert "sales.csv" in report_text

    # Verifier passes.
    verifier = Verifier(workspace_root=sales_workspace)
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    vresult = verifier.verify(
        task_id=task.task_id, run_id=outcome.run_id, plan=plan,
        manifest=outcome.manifest, executed_action_ids=executed,
        skipped_action_ids=set(), failed_action_ids=set(),
        original_snapshot=snap,
    )
    assert vresult.passed, vresult.failed_checks
