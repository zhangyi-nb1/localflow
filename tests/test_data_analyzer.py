"""Phase 3.3a — typed AnalysisSpec engine + data_analyzer skill tests.

All tests are LLM-free: we construct AnalysisSpec instances directly
and exercise the engine. Phase 3.3b will add LLM-planner tests using
FakeLLMClient.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import pytest

from app.harness.executor import Executor
from app.harness.verifier import Verifier
from app.schemas import ExecutionStatus, TaskSpec
from app.schemas.analysis import (
    AggregationOp,
    AnalysisOutcome,
    AnalysisSpec,
    ChartRequest,
    Filter,
    FilterOp,
    GroupBy,
)
from app.skills.data_analyzer import (
    DataAnalyzerSkill,
    DataAnalyzerValidationError,
    plan_data_analysis,
    validate_data_analyzer_plan,
)
from app.tools.data_analysis import execute_analysis
from app.tools.file_scan import scan_workspace

# --------------------------------------------------------------------- fixtures


@pytest.fixture
def sales_df() -> pd.DataFrame:
    """A small but realistic transaction-shaped DataFrame."""
    return pd.DataFrame(
        {
            "region": ["N", "S", "N", "S", "N", "W", "W", "S", "E", "E"],
            "product": ["A", "A", "B", "B", "B", "A", "B", "A", "B", "B"],
            "qty": [1, 5, 2, 3, 4, 1, 6, 2, 1, 7],
            "amount": [10.0, 50.5, 22.0, 30.0, 40.0, 11.0, 66.0, 21.0, 12.0, 70.0],
            "broken": [None, None, None, None, None, None, None, None, None, None],
        }
    )


# --------------------------------------------------------------------- engine: filter


def test_filter_eq_keeps_matching_rows(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="region", op=FilterOp.EQ, value="N")],
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert result.row_count == 3
    assert {row["region"] for row in result.rows} == {"N"}


def test_filter_gt_numeric(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="amount", op=FilterOp.GT, value=30.0)],
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert result.row_count == 4  # 50.5, 40.0, 66.0, 70.0
    assert all(float(row["amount"]) > 30.0 for row in result.rows)


def test_filter_in_list(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="region", op=FilterOp.IN, value=["N", "W"])],
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert {row["region"] for row in result.rows} == {"N", "W"}


def test_filter_is_null_finds_nulls(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="broken", op=FilterOp.IS_NULL)],
    )
    result = execute_analysis(sales_df, spec)
    assert result.row_count == 10  # every row is null in broken


def test_filter_unknown_column_returns_invalid_spec(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="nonexistent", op=FilterOp.EQ, value="x")],
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.INVALID_SPEC
    assert "nonexistent" in (result.error or "")


# --------------------------------------------------------------------- engine: groupby


def test_groupby_mean_aggregates(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        groupby=GroupBy(by=["region"], aggregations={"amount": AggregationOp.MEAN}),
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    by_region = {row["region"]: float(row["amount"]) for row in result.rows}
    # N: (10+22+40)/3 = 24
    # S: (50.5+30+21)/3 = ~33.83
    # W: (11+66)/2 = 38.5
    # E: (12+70)/2 = 41
    assert pytest.approx(by_region["N"], rel=0.01) == 24.0
    assert pytest.approx(by_region["W"], rel=0.01) == 38.5


def test_groupby_count_and_sort(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        groupby=GroupBy(by=["region"], aggregations={"amount": AggregationOp.COUNT}),
        sort_by=["amount"],
        sort_descending=True,
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    counts = [int(row["amount"]) for row in result.rows]
    assert counts == sorted(counts, reverse=True)


def test_groupby_with_filter_and_limit(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        filters=[Filter(column="amount", op=FilterOp.GT, value=10.0)],
        groupby=GroupBy(by=["product"], aggregations={"qty": AggregationOp.SUM}),
        sort_by=["qty"],
        sort_descending=True,
        limit=1,
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert result.row_count == 2  # 2 products before limit
    assert len(result.rows) == 1  # limit=1 caps display
    assert result.rows_truncated is True


# --------------------------------------------------------------------- engine: chart


def test_chart_histogram_produces_png(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        chart=ChartRequest(kind="histogram", x="amount"),
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert result.chart_png_b64 is not None
    raw = base64.b64decode(result.chart_png_b64)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_chart_bar_from_groupby(sales_df: pd.DataFrame) -> None:
    spec = AnalysisSpec(
        source_file="sales.csv",
        groupby=GroupBy(by=["region"], aggregations={"amount": AggregationOp.SUM}),
        chart=ChartRequest(kind="bar", x="region", y="amount"),
    )
    result = execute_analysis(sales_df, spec)
    assert result.outcome == AnalysisOutcome.OK
    assert result.chart_png_b64 is not None


# --------------------------------------------------------------------- planner


def test_planner_produces_plan_for_csv_workspace(tmp_path: Path) -> None:
    """Rule planner: pick a default analysis per file → one report
    action + one chart per analyzable file."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "events.csv").write_text(
        "type,value\nclick,1\npurchase,5\nclick,2\nview,1\nclick,3\n",
        encoding="utf-8",
    )
    snap = scan_workspace(ws, "t1", compute_preview=False)
    task = TaskSpec(
        task_id="t1", user_goal="analyze", workspace_root=str(ws), skill="data_analyzer"
    )

    plan = plan_data_analysis(task, snap)
    assert len(plan.actions) >= 1
    report = next(a for a in plan.actions if a.target_path == "analysis_report.md")
    assert "content" in report.metadata
    assert "data_analysis" in report.metadata["provenance"]["synthesis_kind"]
    # Charts (if any) live under analysis_charts/
    for a in plan.actions:
        if a.target_path != "analysis_report.md":
            assert a.target_path.startswith("analysis_charts/")
            assert "binary_content_b64" in a.metadata


def test_planner_empty_workspace_is_noop(tmp_path: Path) -> None:
    ws = tmp_path / "empty"
    ws.mkdir()
    snap = scan_workspace(ws, "t-empty", compute_preview=False)
    task = TaskSpec(task_id="t-empty", user_goal="x", workspace_root=str(ws), skill="data_analyzer")
    plan = plan_data_analysis(task, snap)
    assert plan.actions == []
    assert "No CSV/TSV/Excel" in plan.summary


# --------------------------------------------------------------------- validator


def test_validator_rejects_non_index_action(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "x.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    snap = scan_workspace(ws, "t", compute_preview=False)
    task = TaskSpec(task_id="t", user_goal="x", workspace_root=str(ws), skill="data_analyzer")
    plan = plan_data_analysis(task, snap)

    # Tamper: change the report action_type to MOVE — should be rejected.
    from app.schemas.action import ActionType

    plan.actions[0].action_type = ActionType.MOVE
    with pytest.raises(DataAnalyzerValidationError):
        validate_data_analyzer_plan(plan)


# --------------------------------------------------------------------- end-to-end


def test_skill_runs_through_executor(tmp_path: Path) -> None:
    """Phase 3.3 / outline §10.7 — 6th implementation of the
    'new skill doesn't touch Harness Kernel' rule."""
    from app.storage.run_store import RunStore

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sales.csv").write_text(
        "region,amount\nN,10\nS,20\nN,30\nE,40\nS,15\nW,25\n",
        encoding="utf-8",
    )

    import os

    os.environ["LOCALFLOW_HOME"] = str(tmp_path / ".lf")
    store = RunStore.create()
    snap = scan_workspace(ws, store.task_id, compute_preview=False)
    task = TaskSpec(
        task_id=store.task_id, user_goal="analyze", workspace_root=str(ws), skill="data_analyzer"
    )
    store.save_task(task)
    store.save_workspace(snap)

    skill = DataAnalyzerSkill()
    plan = skill.plan(task, snap)
    skill.validate(plan)
    store.save_plan(plan)

    executor = Executor(workspace_root=ws, run_store=store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    succ = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    assert succ == len(plan.actions)

    # analysis_report.md exists and mentions the source file.
    report_path = ws / "analysis_report.md"
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "sales.csv" in report_text

    # Verifier passes.
    verifier = Verifier(workspace_root=ws)
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    vresult = verifier.verify(
        task_id=task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids=executed,
        skipped_action_ids=set(),
        failed_action_ids=set(),
        original_snapshot=snap,
    )
    assert vresult.passed, vresult.failed_checks
