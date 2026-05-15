"""Phase 11 — auto-detect routes data-analysis goals to data_analyzer.

v0.9.0's autodetect always picked the agent meta-skill. v0.12.0 adds
ONE explicit branch: if the goal mentions analysis verbs AND the
workspace contains a tabular file, route to data_analyzer instead.
Everything else still routes to the agent. These tests pin the
heuristic so a routing regression doesn't slip through.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import FileMeta, WorkspaceSnapshot
from app.skills import get_default_registry
from app.ui._autodetect import autodetect_skill


def _snap(file_types: dict[str, int]) -> WorkspaceSnapshot:
    files: list[FileMeta] = []
    n = 0
    for ftype, count in file_types.items():
        for _ in range(count):
            n += 1
            files.append(
                FileMeta(
                    path=f"{ftype}_{n}.bin",
                    file_type=ftype,
                    size_bytes=1,
                    modified_at=datetime.now(timezone.utc),
                )
            )
    return WorkspaceSnapshot(
        snapshot_id="snap-test",
        task_id="t-test",
        root="/fake",
        files=files,
        total_files=len(files),
        total_size_bytes=len(files),
    )


def test_chinese_analysis_verb_plus_excel_routes_to_data_analyzer() -> None:
    reg = get_default_registry()
    # User's exact failing goal from the v0.11.0 bug report.
    goal = "给我介绍数据文件以及解读对应的数据文件内的数据信息"
    choice = autodetect_skill(goal, _snap({"excel": 1, "pdf": 3}), reg)
    assert choice.name == "data_analyzer"
    assert "data_analyzer" in choice.reason


def test_english_analysis_verb_plus_csv_routes_to_data_analyzer() -> None:
    reg = get_default_registry()
    goal = "analyze the sales numbers and chart them"
    choice = autodetect_skill(goal, _snap({"tabular": 2}), reg)
    assert choice.name == "data_analyzer"


def test_organize_goal_with_excel_still_routes_to_agent() -> None:
    """Workspace has .xlsx but goal is about organisation, not analysis
    — agent meta-skill is the right destination (it can do organise +
    chart-of-file-types). Heuristic should NOT trigger on file presence
    alone."""
    reg = get_default_registry()
    goal = "整理文件按种类，然后画一个柱状图"
    choice = autodetect_skill(goal, _snap({"excel": 1, "pdf": 3}), reg)
    assert choice.name == "agent"


def test_analysis_goal_without_data_file_routes_to_agent() -> None:
    """The other half of the AND: analysis verb present but workspace
    has no .xlsx/.csv → routing to data_analyzer would point the user
    at a skill that can't help. Stay on agent."""
    reg = get_default_registry()
    goal = "analyze these documents"
    choice = autodetect_skill(goal, _snap({"pdf": 5}), reg)
    assert choice.name == "agent"


def test_empty_goal_with_excel_still_routes_to_agent() -> None:
    """Empty goal triggers the agent's 'describe what you want' path
    regardless of workspace contents."""
    reg = get_default_registry()
    choice = autodetect_skill("", _snap({"excel": 1}), reg)
    assert choice.name == "agent"


def test_no_snapshot_returns_agent_even_with_analysis_verb() -> None:
    """Defensive: if the scan hasn't completed yet, we can't see file
    types, so don't speculate. Stay on agent."""
    reg = get_default_registry()
    choice = autodetect_skill("analyze the data", None, reg)
    assert choice.name == "agent"
