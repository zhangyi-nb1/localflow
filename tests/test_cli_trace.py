"""v0.10.1 — regular CLI commands now emit trace.jsonl by default.

Phase 9 only wired trace through the eval runner (additive-only,
back-compat). v0.10.1 closes the gap: every CLI command that drives
the kernel now attaches a TraceLogger, so users running
`localflow plan/execute/verify/rollback` get the same observability
the eval suite gets.

These tests drive the CLI via Typer's CliRunner against an isolated
LOCALFLOW_HOME so they never touch the user's real run history.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app


@pytest.fixture
def isolated_run_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force RunStore to use a tmpdir for its <home>/runs/ tree, so CLI
    invocations don't pollute the user's real ~/.localflow/. Returns
    the run_dir parent (so tests can scan for trace.jsonl)."""
    home = tmp_path / "lf"
    monkeypatch.setenv("LOCALFLOW_HOME", str(home))
    return home


def _seed_workspace(tmp_path: Path) -> Path:
    """Plant a tiny workspace the CLI plan command can work on."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.pdf").write_text("doc", encoding="utf-8")
    (ws / "b.txt").write_text("note", encoding="utf-8")
    return ws


def test_cli_plan_emits_trace_jsonl(
    tmp_path: Path,
    isolated_run_home: Path,
) -> None:
    """v0.10.1: `localflow plan` writes trace.jsonl with at least one
    event (the policy_check from run_risk_check).

    Note: ruff sees the typer.CliRunner-driven CLI as a fresh import
    of `app.cli`, so the `app` object below is the same one Typer
    decorates at import time.
    """
    runner = CliRunner()
    ws = _seed_workspace(tmp_path)
    result = runner.invoke(
        app,
        [
            "plan",
            str(ws),
            "--goal",
            "organize by file type",
            "--planner",
            "rule",
        ],
        env=os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_run_home)},
    )
    assert result.exit_code == 0, result.output

    # Find the run dir CLI just created.
    runs = sorted((isolated_run_home / "runs").iterdir())
    assert runs, f"no runs created under {isolated_run_home}"
    run_dir = runs[-1]
    trace_path = run_dir / "trace.jsonl"
    assert trace_path.exists(), f"trace.jsonl missing from {run_dir}"
    # At least one event recorded (policy_check from run_risk_check).
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert lines, "trace.jsonl is empty after plan"


def test_cli_execute_emits_action_and_verifier_events(
    tmp_path: Path,
    isolated_run_home: Path,
) -> None:
    """End-to-end: `localflow plan` then `localflow execute --yes`
    populates trace.jsonl with action.start/end + verifier.check
    events. Proves the kernel emission sites all reach trace.jsonl
    through the CLI driver path (not just through the eval runner)."""
    from app.harness.trace import TraceLogger

    runner = CliRunner()
    ws = _seed_workspace(tmp_path)
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_run_home)}
    plan_result = runner.invoke(
        app,
        ["plan", str(ws), "--goal", "organize", "--planner", "rule"],
        env=env,
    )
    assert plan_result.exit_code == 0, plan_result.output

    # Extract task_id from the plan output (which prints "Task created: <id>")
    task_id: str | None = None
    for line in plan_result.output.splitlines():
        if "Task created:" in line:
            # Strip Rich's box characters + colours; grab the YYYY-MM-DD-NNN token.
            for token in line.split():
                if token.count("-") == 3 and token[:4].isdigit():
                    task_id = token
                    break
    assert task_id is not None, f"could not parse task_id from: {plan_result.output}"

    exec_result = runner.invoke(app, ["execute", "--task-id", task_id, "--yes"], env=env)
    assert exec_result.exit_code == 0, exec_result.output

    trace_path = isolated_run_home / "runs" / task_id / "trace.jsonl"
    assert trace_path.exists()
    events = TraceLogger(trace_path).read_all()
    types = {e.event_type.value for e in events}
    assert "action.start" in types
    assert "action.end" in types
    assert "verifier.check" in types
    assert "dry_run.rendered" in types
