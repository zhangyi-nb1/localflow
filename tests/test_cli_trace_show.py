"""Phase 25.2 — ``localflow trace show / summary`` CLI surface.

Phase 25.1 wired ActionTraceEvent into trace.jsonl. These tests prove
the new CLI commands actually expose that data: a run with one
PYTHON_COMPUTE-free MKDIR action should show up as ``action.start`` +
``action.end`` rows, the ``--show-thought`` flag should print the
plan's LLM thought when present, and ``trace summary`` should report
the ActionTraceEvent row count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "lf"
    monkeypatch.setenv("LOCALFLOW_HOME", str(home))
    return home


def _seed_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.pdf").write_text("doc", encoding="utf-8")
    (ws / "b.txt").write_text("note", encoding="utf-8")
    return ws


def _run_plan_and_execute(runner: CliRunner, ws: Path, env: dict[str, str]) -> str:
    """Plan + execute one rule-based plan under the isolated home.
    Returns the task_id of the run that was created."""
    plan_result = runner.invoke(
        app,
        ["plan", str(ws), "--goal", "organize by file type", "--planner", "rule"],
        env=env,
    )
    assert plan_result.exit_code == 0, plan_result.output
    runs_root = Path(env["LOCALFLOW_HOME"]) / "runs"
    runs = sorted(p for p in runs_root.iterdir() if p.is_dir())
    assert runs, "plan did not create any run"
    task_id = runs[-1].name

    exec_result = runner.invoke(
        app,
        ["execute", "--task-id", task_id, "--yes"],
        env=env,
    )
    assert exec_result.exit_code == 0, exec_result.output
    return task_id


def _read_trace(home: Path, task_id: str) -> list[dict]:
    path = home / "runs" / task_id / "trace.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestTraceShow:
    """``localflow trace show --task-id X`` renders a table from
    trace.jsonl."""

    def test_show_lists_action_events(self, tmp_path: Path, isolated_home: Path):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        ws = _seed_workspace(tmp_path)
        task_id = _run_plan_and_execute(runner, ws, env)

        result = runner.invoke(
            app,
            ["trace", "show", "--task-id", task_id],
            env=env,
        )
        assert result.exit_code == 0, result.output
        # Both action lifecycle events should appear in the rendered table.
        assert "action.start" in result.output
        assert "action.end" in result.output

    def test_show_filter_by_event_type(self, tmp_path: Path, isolated_home: Path):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        ws = _seed_workspace(tmp_path)
        task_id = _run_plan_and_execute(runner, ws, env)

        result = runner.invoke(
            app,
            [
                "trace",
                "show",
                "--task-id",
                task_id,
                "--event-type",
                "action.end",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        assert "action.end" in result.output
        # ``action.start`` rows are filtered out — they should not appear
        # in the table body. (We can't simply assert ``not in`` against the
        # whole output because the title / footer might mention totals;
        # check the body lines explicitly.)
        body_lines = [
            row_line for row_line in result.output.splitlines()
            if "action." in row_line and "│" in row_line
        ]
        assert body_lines, "expected some action rows after filter"
        for line in body_lines:
            assert "action.start" not in line, f"action.start leaked through filter: {line}"

    def test_show_observation_flag_prints_observation(
        self, tmp_path: Path, isolated_home: Path
    ):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        ws = _seed_workspace(tmp_path)
        task_id = _run_plan_and_execute(runner, ws, env)

        # First confirm there's at least one ACTION_END row with observation.
        rows = _read_trace(isolated_home, task_id)
        end_rows_with_obs = [
            r for r in rows
            if r.get("event") == "action.end"
            and (r.get("payload") or {}).get("observation") is not None
        ]
        assert end_rows_with_obs, (
            "no action.end row has observation populated — Phase 25.1 "
            "executor change may have regressed"
        )

        result = runner.invoke(
            app,
            [
                "trace",
                "show",
                "--task-id",
                task_id,
                "--event-type",
                "action.end",
                "--show-observation",
            ],
            env=env,
        )
        assert result.exit_code == 0, result.output
        # observation header + at least one of the standard observation
        # keys (action_type / source / target / hash_before / hash_after).
        assert "observation" in result.output
        assert (
            "action_type" in result.output
            or "hash_before" in result.output
            or "target" in result.output
        ), result.output

    def test_show_missing_task_returns_error(self, isolated_home: Path):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        result = runner.invoke(
            app,
            ["trace", "show", "--task-id", "2099-01-01-001"],
            env=env,
        )
        assert result.exit_code == 1
        assert "No trace.jsonl" in result.output


class TestTraceSummary:
    """``localflow trace summary --task-id X`` reports counts."""

    def test_summary_reports_event_histogram(
        self, tmp_path: Path, isolated_home: Path
    ):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        ws = _seed_workspace(tmp_path)
        task_id = _run_plan_and_execute(runner, ws, env)

        result = runner.invoke(
            app,
            ["trace", "summary", "--task-id", task_id],
            env=env,
        )
        assert result.exit_code == 0, result.output
        # Histogram must include the lifecycle markers.
        assert "action.start" in result.output
        assert "action.end" in result.output
        # ActionTraceEvent count line is the key Phase 25.1 signal.
        assert "ActionTraceEvent rows" in result.output

    def test_summary_counts_action_trace_events(
        self, tmp_path: Path, isolated_home: Path
    ):
        runner = CliRunner()
        env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
        ws = _seed_workspace(tmp_path)
        task_id = _run_plan_and_execute(runner, ws, env)

        rows = _read_trace(isolated_home, task_id)
        expected_rich = sum(
            1 for r in rows
            if (r.get("payload") or {}).get("observation") is not None
        )
        assert expected_rich > 0, (
            "test harness expected at least one ActionTraceEvent in the run"
        )

        result = runner.invoke(
            app,
            ["trace", "summary", "--task-id", task_id],
            env=env,
        )
        assert result.exit_code == 0, result.output
        # The summary prints e.g. "ActionTraceEvent rows (Phase 25.1 shape): N".
        # Just confirm the count is non-zero in the rendered line.
        for line in result.output.splitlines():
            if "ActionTraceEvent" in line:
                # Pull the trailing number out of the line.
                digits = [tok for tok in line.replace(":", " ").split() if tok.isdigit()]
                assert digits, f"could not parse count from: {line!r}"
                assert int(digits[-1]) > 0, f"count was zero: {line!r}"
                break
        else:
            raise AssertionError("ActionTraceEvent line not found in summary output")
