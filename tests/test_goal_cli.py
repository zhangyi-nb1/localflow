"""Phase 18 — `localflow goal` CLI surface.

Smoke tests that:
  * the command is registered + parses arguments;
  * --no-llm short-circuits the LLM lookup;
  * missing workspace exits with code 2;
  * a confident router pick is surfaced as 'Suggested pack'.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_goal_command_is_registered() -> None:
    result = runner.invoke(app, ["goal", "--help"])
    assert result.exit_code == 0
    assert "Phase 18" in result.stdout or "natural-language" in result.stdout


def test_goal_rejects_missing_workspace() -> None:
    result = runner.invoke(app, ["goal", "anything", "--workspace", "/no/such/dir"])
    assert result.exit_code == 2
    assert "Workspace not found" in result.stdout


def test_goal_no_llm_returns_router_pick(tmp_path: Path) -> None:
    # Plant a mixed workspace that strongly matches research_pack.
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    (tmp_path / "notes.md").write_text("# notes\n")
    result = runner.invoke(
        app,
        [
            "goal",
            "research papers analysis",
            "--workspace",
            str(tmp_path),
            "--no-llm",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "research_pack" in result.stdout
    # Phase 21.1: render now leads with structured Decision: / Recipe: lines
    # instead of "Suggested pack".
    assert "Decision:" in result.stdout
    assert "pick" in result.stdout
