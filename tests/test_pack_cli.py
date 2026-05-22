"""Phase 17 — pack CLI surface tests (list / describe / suggest).

These tests invoke the Typer app via the runner — they don't kick off
real pack runs (those go through the existing taskgraph runner tests).
Focus is on:
  * `pack list` enumerates the shipped recipes.
  * `pack describe <name>` prints the spec, returns 2 for unknown names.
  * `pack suggest <ws>` ranks recipes against a real (tiny) workspace.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_pack_list_shows_all_three_flagships() -> None:
    result = runner.invoke(app, ["pack", "list"])
    assert result.exit_code == 0, result.stdout
    # Rich truncates long names mid-column; check the un-truncated prefix
    # for the long name + the full short ones.
    assert "research_pack" in result.stdout
    assert "data_report_pack" in result.stdout
    assert "project_handoff_" in result.stdout  # truncated form is fine
    assert "Recipe catalog" in result.stdout


def test_pack_describe_known_recipe_succeeds() -> None:
    result = runner.invoke(app, ["pack", "describe", "research_pack"])
    assert result.exit_code == 0, result.stdout
    assert "Research Pack" in result.stdout
    # Rich may truncate the skill column — check the un-truncated stage_id +
    # the deliverables block instead.
    assert "s1_organize" in result.stdout
    assert "README.md" in result.stdout  # one of the expected_outputs


def test_pack_describe_unknown_recipe_exits_with_code_2() -> None:
    result = runner.invoke(app, ["pack", "describe", "no_such_recipe"])
    assert result.exit_code == 2
    assert "no_such_recipe" in result.stdout.lower()


def test_pack_suggest_recommends_research_pack_for_mixed_workspace(
    tmp_path: Path,
) -> None:
    # Plant a mini workspace with a PDF stub, a CSV, and a note.
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    (tmp_path / "notes.md").write_text("# notes\n")
    result = runner.invoke(
        app,
        ["pack", "suggest", str(tmp_path), "--goal", "research papers"],
    )
    assert result.exit_code == 0, result.stdout
    assert "research_pack" in result.stdout
    assert "Suggested" in result.stdout


def test_pack_suggest_handles_missing_workspace() -> None:
    result = runner.invoke(app, ["pack", "suggest", "/nonexistent/path/xyz"])
    assert result.exit_code == 2
    assert "Workspace not found" in result.stdout


def test_pack_run_rejects_unknown_recipe(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["pack", "run", "no_such", "--workspace", str(tmp_path), "--yes"],
    )
    assert result.exit_code == 2
    assert "no_such" in result.stdout.lower()


def test_pack_run_rejects_missing_workspace() -> None:
    result = runner.invoke(
        app,
        [
            "pack",
            "run",
            "research_pack",
            "--workspace",
            "/path/that/does/not/exist",
            "--yes",
        ],
    )
    assert result.exit_code == 2
    assert "Workspace not found" in result.stdout
