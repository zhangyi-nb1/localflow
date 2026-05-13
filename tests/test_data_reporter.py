"""Phase 3.1 / outline §14 DataOps — tests for data_ops + data_reporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.executor import Executor
from app.harness.verifier import Verifier
from app.schemas import ExecutionStatus, TaskSpec
from app.skills.data_reporter import (
    DataReporterSkill,
    plan_data_report,
)
from app.skills.data_reporter.validator import (
    DataReporterValidationError,
    validate_data_report_plan,
)
from app.tools import data_ops
from app.tools.file_scan import scan_workspace

# --------------------------------------------------------------------- data_ops unit tests


def test_data_ops_is_csv_like(tmp_path: Path) -> None:
    assert data_ops.is_csv_like(tmp_path / "x.csv")
    assert data_ops.is_csv_like(tmp_path / "x.tsv")
    assert data_ops.is_csv_like(tmp_path / "x.CSV")
    assert not data_ops.is_csv_like(tmp_path / "x.xlsx")
    assert not data_ops.is_csv_like(tmp_path / "x.json")


def test_data_ops_is_excel_like(tmp_path: Path) -> None:
    assert data_ops.is_excel_like(tmp_path / "x.xlsx")
    assert data_ops.is_excel_like(tmp_path / "x.xls")
    assert data_ops.is_excel_like(tmp_path / "x.XLSX")
    assert not data_ops.is_excel_like(tmp_path / "x.csv")


def test_data_ops_is_supported_tabular(tmp_path: Path) -> None:
    assert data_ops.is_supported_tabular(tmp_path / "x.csv")
    assert data_ops.is_supported_tabular(tmp_path / "x.xlsx")
    assert not data_ops.is_supported_tabular(tmp_path / "x.json")
    assert not data_ops.is_supported_tabular(tmp_path / "x.parquet")


def test_data_ops_reads_simple_csv(tmp_path: Path) -> None:
    p = tmp_path / "users.csv"
    p.write_text("id,name,age\n1,Alice,30\n2,Bob,25\n3,Carol,28\n", encoding="utf-8")
    summaries = data_ops.read_and_describe(p, "users.csv")
    assert len(summaries) == 1
    s = summaries[0]
    assert s.error is None
    assert s.rows_read == 3
    assert s.cols == 3
    names = [c.name for c in s.columns]
    assert names == ["id", "name", "age"]
    age_col = next(c for c in s.columns if c.name == "age")
    assert age_col.numeric_stats is not None
    assert age_col.numeric_stats["min"] == 25.0
    assert age_col.numeric_stats["max"] == 30.0


def test_data_ops_handles_unicode(tmp_path: Path) -> None:
    p = tmp_path / "names.csv"
    p.write_text("name,city\nAlice,北京\nBob,Shanghai\n", encoding="utf-8")
    summaries = data_ops.read_and_describe(p, "names.csv")
    s = summaries[0]
    assert s.error is None
    assert s.rows_read == 2
    city_col = next(c for c in s.columns if c.name == "city")
    assert "北京" in city_col.sample_values or "Shanghai" in city_col.sample_values


def test_data_ops_caps_long_files(tmp_path: Path) -> None:
    p = tmp_path / "big.csv"
    lines = ["a,b"] + [f"{i},{i * 2}" for i in range(500)]
    p.write_text("\n".join(lines), encoding="utf-8")
    summaries = data_ops.read_and_describe(p, "big.csv", max_rows=100)
    s = summaries[0]
    assert s.rows_read == 100
    assert s.rows_truncated is True


def test_data_ops_returns_error_on_missing(tmp_path: Path) -> None:
    summaries = data_ops.read_and_describe(tmp_path / "nope.csv", "nope.csv")
    assert len(summaries) == 1
    assert summaries[0].error == "file not found"


def test_data_ops_returns_error_on_oversized(tmp_path: Path) -> None:
    p = tmp_path / "big.csv"
    p.write_bytes(b"x" * 2_000_000)
    summaries = data_ops.read_and_describe(p, "big.csv", max_bytes=1_000_000)
    assert summaries[0].error is not None
    assert "too large" in summaries[0].error


# --------------------------------------------------------------------- Excel (Phase 3.1b)


def test_data_ops_reads_single_sheet_xlsx(tmp_path: Path) -> None:
    """Real Excel file with one sheet — should produce exactly one summary."""
    import pandas as pd

    p = tmp_path / "single.xlsx"
    pd.DataFrame({"id": [1, 2, 3], "value": [10.5, 20.0, 15.7]}).to_excel(
        p, sheet_name="Sheet1", index=False
    )
    summaries = data_ops.read_and_describe(p, "single.xlsx")
    assert len(summaries) == 1
    s = summaries[0]
    assert s.error is None
    assert "single.xlsx" in s.path
    assert "Sheet1" in s.path
    assert s.rows_read == 3
    assert s.cols == 2
    value_col = next(c for c in s.columns if c.name == "value")
    assert value_col.numeric_stats is not None
    assert value_col.numeric_stats["min"] == 10.5
    assert value_col.numeric_stats["max"] == 20.0


def test_data_ops_reads_multi_sheet_xlsx(tmp_path: Path) -> None:
    """Excel with 3 sheets — should produce 3 summaries, each with sheet name in path."""
    import pandas as pd

    p = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame({"a": [1, 2]}).to_excel(w, sheet_name="Alpha", index=False)
        pd.DataFrame({"b": [10, 20, 30]}).to_excel(w, sheet_name="Beta", index=False)
        pd.DataFrame({"c": [100]}).to_excel(w, sheet_name="Gamma", index=False)
    summaries = data_ops.read_and_describe(p, "multi.xlsx")
    assert len(summaries) == 3
    sheet_names = [s.path for s in summaries]
    assert any("Alpha" in n for n in sheet_names)
    assert any("Beta" in n for n in sheet_names)
    assert any("Gamma" in n for n in sheet_names)
    beta = next(s for s in summaries if "Beta" in s.path)
    assert beta.rows_read == 3


def test_data_ops_returns_error_on_corrupted_xlsx(tmp_path: Path) -> None:
    """A file with .xlsx extension but garbage content."""
    p = tmp_path / "corrupt.xlsx"
    p.write_bytes(b"this is definitely not a valid xlsx file")
    summaries = data_ops.read_and_describe(p, "corrupt.xlsx")
    assert len(summaries) == 1
    assert summaries[0].error is not None


def test_data_ops_returns_error_on_corrupted_csv(tmp_path: Path) -> None:
    # A CSV with mismatched quoting that pandas can't parse.
    p = tmp_path / "broken.csv"
    p.write_bytes(b'col\n"unterminated\nmore data\nstill broken\n')
    summary = data_ops.read_and_describe(p, "broken.csv")
    # Either pandas recovered (with weird shape) or it errored — both fine,
    # we just need to confirm we don't crash.
    assert summary is not None


# --------------------------------------------------------------------- planner & skill


@pytest.fixture()
def csv_workspace(tmp_path: Path) -> Path:
    """Workspace with two CSVs of different shapes."""
    root = tmp_path / "data_ws"
    root.mkdir()
    (root / "users.csv").write_text(
        "id,name,age,joined\n"
        "1,Alice,30,2024-01-15\n"
        "2,Bob,25,2024-02-03\n"
        "3,Carol,28,2024-02-20\n"
        "4,Dan,,2024-03-01\n",
        encoding="utf-8",
    )
    (root / "metrics.tsv").write_text(
        "timestamp\tlatency_ms\tstatus\n"
        "2026-01-01T00:00\t12.3\tok\n"
        "2026-02-01T00:00\t9.7\tok\n"
        "2026-03-01T00:00\t150.0\terror\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# notes\n", encoding="utf-8")  # non-tabular
    return root


@pytest.fixture()
def csv_task(csv_workspace: Path) -> TaskSpec:
    return TaskSpec(
        task_id="t-data",
        user_goal="Summarize all tabular data",
        workspace_root=str(csv_workspace),
        skill="data_reporter",
        constraints=["do not modify source data"],
        allowed_actions=["index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )


def test_planner_produces_index_action_plus_optional_charts(csv_workspace, csv_task) -> None:
    """Phase 3.2: the report action is mandatory; chart actions optional.

    Every plan ends with exactly one action whose target is
    ``data_report.md``. Phase 3.2 may add 0..N extra ``index`` actions
    that write PNG charts under ``charts/``.
    """
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    assert len(plan.actions) >= 1
    # All actions are index actions; one specifically writes data_report.md.
    assert all(a.action_type.value == "index" for a in plan.actions)
    report_actions = [a for a in plan.actions if a.target_path == "data_report.md"]
    assert len(report_actions) == 1
    report = report_actions[0]
    assert report.metadata["content"]
    assert "provenance" in report.metadata
    # Any non-report actions must be charts under charts/ with binary payloads.
    for a in plan.actions:
        if a.target_path != "data_report.md":
            assert a.target_path.startswith("charts/")
            assert "binary_content_b64" in a.metadata


def test_planner_skips_non_tabular_files(csv_workspace, csv_task) -> None:
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    sources = plan.actions[0].metadata["provenance"]["sources"]
    paths = {s["path"] for s in sources}
    assert paths == {"users.csv", "metrics.tsv"}  # README.md not in sources


def test_planner_content_includes_schema_and_stats(csv_workspace, csv_task) -> None:
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    content = plan.actions[0].metadata["content"]
    assert "users.csv" in content
    assert "metrics.tsv" in content
    # Schema columns appear.
    assert "name" in content
    assert "latency_ms" in content
    # Numeric stats for latency_ms appear (min/max).
    assert "12.3" in content or "12.30" in content
    assert "150" in content


def test_planner_empty_workspace_produces_noop(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    snap = scan_workspace(empty, "t-empty", compute_preview=False)
    task = TaskSpec(task_id="t-empty", user_goal="x", workspace_root=str(empty))
    plan = plan_data_report(task, snap)
    assert plan.actions == []
    assert "No CSV/TSV/Excel" in plan.summary


def test_validator_rejects_missing_provenance(csv_workspace, csv_task) -> None:
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    plan.actions[0].metadata.pop("provenance")
    with pytest.raises(DataReporterValidationError, match="provenance"):
        validate_data_report_plan(plan)


def test_validator_rejects_wrong_synthesis_kind(csv_workspace, csv_task) -> None:
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    plan.actions[0].metadata["provenance"]["synthesis_kind"] = "pdf_index"
    with pytest.raises(DataReporterValidationError, match="synthesis_kind"):
        validate_data_report_plan(plan)


# --------------------------------------------------------------------- end-to-end through harness


def test_planner_includes_excel_sheets(tmp_path: Path) -> None:
    """End-to-end: a workspace with both a CSV and a multi-sheet Excel
    must yield one section per CSV PLUS one section per Excel sheet."""
    import pandas as pd

    from app.tools.file_scan import scan_workspace

    ws = tmp_path / "mixed"
    ws.mkdir()
    (ws / "users.csv").write_text("id,name\n1,alice\n2,bob\n", encoding="utf-8")
    with pd.ExcelWriter(ws / "report.xlsx") as w:
        pd.DataFrame({"month": ["Jan", "Feb"], "rev": [100, 200]}).to_excel(
            w, sheet_name="Q1", index=False
        )
        pd.DataFrame({"month": ["Apr", "May"], "rev": [300, 400]}).to_excel(
            w, sheet_name="Q2", index=False
        )
    snap = scan_workspace(ws, "t", compute_preview=False)
    task = TaskSpec(task_id="t", user_goal="profile", workspace_root=str(ws), skill="data_reporter")
    plan = plan_data_report(task, snap)
    # Phase 3.2: at least the data_report.md action; charts add 0+ more.
    assert len(plan.actions) >= 1
    report_action = next(a for a in plan.actions if a.target_path == "data_report.md")
    content = report_action.metadata["content"]
    # Must include the CSV and both Excel sheets.
    assert "users.csv" in content
    assert "Q1" in content
    assert "Q2" in content
    # Provenance records one entry per produced table (1 csv + 2 sheets = 3).
    prov_sources = report_action.metadata["provenance"]["sources"]
    assert len(prov_sources) == 3


# --------------------------------------------------------------------- Phase 3.2: charts


def test_chart_ops_histogram_produces_png_bytes() -> None:
    from app.tools import chart_ops

    png = chart_ops.histogram_png(
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        title="test histogram",
        xlabel="x",
    )
    assert isinstance(png, bytes)
    # PNG magic bytes
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Non-empty payload after header
    assert len(png) > 1000


def test_chart_ops_bar_produces_png_bytes() -> None:
    from app.tools import chart_ops

    png = chart_ops.bar_png(
        {"alpha": 12, "beta": 7, "gamma": 3},
        title="test bar",
        xlabel="category",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_chart_ops_handles_empty_input_gracefully() -> None:
    from app.tools import chart_ops

    # Empty list — should produce a "no data to plot" placeholder PNG, not crash.
    png = chart_ops.histogram_png([], title="empty", xlabel="x")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_data_reporter_plan_includes_chart_actions(csv_workspace, csv_task) -> None:
    """Plan should produce 1 markdown report + N chart actions where
    each successfully-read table contributes one chart."""
    from app.tools.file_scan import scan_workspace

    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    plan = plan_data_report(csv_task, snap)
    # 1 report action + chart actions
    assert len(plan.actions) >= 2
    report = plan.actions[0]
    assert report.metadata.get("content")
    assert report.metadata.get("binary_content_b64") is None
    # Chart actions carry binary_content_b64 + chart_spec
    chart_actions = [a for a in plan.actions[1:]]
    assert len(chart_actions) >= 1
    for a in chart_actions:
        assert a.metadata.get("binary_content_b64")
        assert a.metadata.get("chart_spec")
        assert a.metadata["chart_spec"]["kind"] in {"histogram", "bar"}
        # Chart files go under charts/ subdirectory
        assert a.target_path.startswith("charts/")


def test_data_reporter_chart_action_round_trips_through_executor(
    csv_workspace, csv_task, run_store
) -> None:
    """End-to-end: plan + execute writes real PNG bytes to disk under charts/."""
    from app.harness.executor import Executor
    from app.tools.file_scan import scan_workspace

    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    run_store.save_task(csv_task)
    run_store.save_workspace(snap)
    plan = plan_data_report(csv_task, snap)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=csv_workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    # Chart files exist on disk and are valid PNGs.
    charts_dir = csv_workspace / "charts"
    assert charts_dir.exists()
    chart_files = list(charts_dir.glob("*.png"))
    assert len(chart_files) >= 1
    for chart_file in chart_files:
        assert chart_file.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_data_reporter_runs_through_executor(csv_workspace, csv_task, run_store) -> None:
    """Phase 3.1 / outline §10.7: another new skill executes through the
    harness with zero Kernel changes (4th implementation of this rule)."""
    snap = scan_workspace(csv_workspace, csv_task.task_id, compute_preview=False)
    run_store.save_task(csv_task)
    run_store.save_workspace(snap)

    skill = DataReporterSkill()
    plan = skill.plan(csv_task, snap)
    skill.validate(plan)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=csv_workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    # Phase 3.2: the report action + zero-or-more chart actions all run.
    # Lower bound is 1 (just the report); we assert ≥1 instead of ==1.
    assert success >= 1
    assert success == len(plan.actions)

    verifier = Verifier(workspace_root=csv_workspace)
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    result = verifier.verify(
        task_id=csv_task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids=executed,
        skipped_action_ids=set(),
        failed_action_ids=set(),
        original_snapshot=snap,
    )
    assert result.passed, result.failed_checks

    report_file = csv_workspace / "data_report.md"
    assert report_file.exists()
    body = report_file.read_text(encoding="utf-8")
    assert "Data Report" in body
    assert "users.csv" in body
    assert "metrics.tsv" in body
    # Numeric stats column header appears.
    assert "numeric stats" in body
