"""v0.9.0 — auto-detect collapsed to always-agent.

v0.8.x routed goals across five specialist skills via a keyword
heuristic. v0.9.0 replaces that with a single ``agent`` meta-skill
that handles compound goals end-to-end. These tests pin the new
contract:

  * ``autodetect_skill`` always returns ``agent`` (unless the registry
    is broken, in which case folder_organizer is a defensive fallback).
  * ``autodetect_planner`` returns ``llm`` for any non-empty goal,
    ``rule`` for empty goals (which exercise folder_organizer's
    deterministic fallback inside the agent skill).
  * ``detect_capability_gap`` always returns None — the agent covers
    every capability the UI used to gate-keep on.
  * ``is_compound_goal`` is kept for backwards compatibility (CLI
    diagnostics, future tooling) but the planner no longer consults
    it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import FileMeta, WorkspaceSnapshot
from app.skills import get_default_registry
from app.ui._autodetect import (
    DEFAULT_SKILL,
    autodetect_planner,
    autodetect_skill,
    detect_capability_gap,
    is_compound_goal,
)


def _snap(file_types: dict[str, int]) -> WorkspaceSnapshot:
    """Synthesize a snapshot with N files of each given type. Used
    only to verify autodetect *ignores* the workspace in v0.9.0."""
    files: list[FileMeta] = []
    counter = 0
    for ftype, n in file_types.items():
        for _ in range(n):
            counter += 1
            files.append(
                FileMeta(
                    path=f"{ftype}_{counter}.bin",
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


# ───────────────────────────────────── autodetect_skill — always agent


def test_default_skill_is_agent() -> None:
    """v0.9.0: the only user-facing skill is `agent`. Pin the
    module-level constant so refactors don't drift it silently."""
    assert DEFAULT_SKILL == "agent"


def test_empty_goal_still_returns_agent() -> None:
    reg = get_default_registry()
    choice = autodetect_skill("", _snap({"text": 3}), reg)
    assert choice.name == "agent"


def test_organize_goal_returns_agent() -> None:
    reg = get_default_registry()
    choice = autodetect_skill("整理 by file type", _snap({"text": 3, "pdf": 2}), reg)
    assert choice.name == "agent"


def test_chart_goal_returns_agent() -> None:
    reg = get_default_registry()
    choice = autodetect_skill("draw a bar chart", _snap({"image": 4}), reg)
    assert choice.name == "agent"


def test_compound_goal_returns_agent() -> None:
    """The user's exact testing-grade goal — v0.9.0 routes it cleanly
    to the agent for one-shot compound execution."""
    reg = get_default_registry()
    goal = "将文件按种类整理，然后总结，最后绘制柱状图"
    choice = autodetect_skill(goal, _snap({"pdf": 4, "image": 2, "excel": 1}), reg)
    assert choice.name == "agent"


def test_workspace_files_do_not_change_routing() -> None:
    """v0.8.x routed tabular workspaces to data_analyzer. v0.9.0 leaves
    routing entirely to the agent's LLM — file types in the snapshot
    no longer influence the UI's skill pick."""
    reg = get_default_registry()
    tab_choice = autodetect_skill("anything", _snap({"tabular": 5, "excel": 3}), reg)
    pdf_choice = autodetect_skill("anything", _snap({"pdf": 5}), reg)
    assert tab_choice.name == "agent"
    assert pdf_choice.name == "agent"


def test_no_snapshot_still_returns_agent() -> None:
    """If the workspace scan hasn't completed yet, autodetect must
    still produce a sane skill choice."""
    reg = get_default_registry()
    choice = autodetect_skill("organize", None, reg)
    assert choice.name == "agent"


# ───────────────────────────────────── autodetect_planner


def test_empty_goal_returns_rule() -> None:
    """An empty goal box can't drive an LLM call. Fall back to the
    deterministic rule planner so the page still works."""
    reg = get_default_registry()
    choice = autodetect_planner("", "agent", reg)
    assert choice.name == "rule"


def test_non_empty_goal_returns_llm() -> None:
    reg = get_default_registry()
    choice = autodetect_planner("organize by file type", "agent", reg)
    assert choice.name == "llm"


def test_chinese_goal_returns_llm() -> None:
    reg = get_default_registry()
    choice = autodetect_planner("按文件类型整理", "agent", reg)
    assert choice.name == "llm"


def test_unknown_skill_safely_returns_rule() -> None:
    reg = get_default_registry()
    choice = autodetect_planner("anything", "no_such_skill", reg)
    assert choice.name == "rule"


def test_rule_only_skill_stays_on_rule() -> None:
    """For skills that don't override plan_with_llm (workspace_visualizer,
    pdf_indexer, data_reporter), autodetect_planner returns rule even
    on non-empty goals. The toggle has no LLM to switch to."""
    reg = get_default_registry()
    for skill in ("workspace_visualizer", "pdf_indexer", "data_reporter"):
        choice = autodetect_planner("anything", skill, reg)
        assert choice.name == "rule", skill


def test_prefer_llm_is_a_no_op_when_already_llm() -> None:
    """v0.8.2 added prefer_llm as a way to escape rule-default. v0.9.0
    already defaults to llm; the flag still flows through but is now
    a no-op for the agent skill."""
    reg = get_default_registry()
    choice = autodetect_planner("organize files", "agent", reg, prefer_llm=True)
    assert choice.name == "llm"


# ───────────────────────────────────── detect_capability_gap — always None


def test_capability_gap_always_none_in_v090() -> None:
    """The agent handles every capability the v0.8.x UI gated on, so
    there are no capability gaps to warn about anymore."""
    snap = _snap({"pdf": 4, "image": 2})
    for goal in (
        "整理文件并绘制柱状图",
        "organize files and chart the counts",
        "make a bar chart of the data",
        "",
    ):
        gap = detect_capability_gap(goal, "agent", snap)
        assert gap is None, goal


# ───────────────────────────────────── is_compound_goal — legacy helper


def test_is_compound_goal_detects_chinese_marker() -> None:
    hit, reason = is_compound_goal("先整理，然后绘制图表")
    assert hit
    assert "然后" in reason or "marker" in reason


def test_is_compound_goal_detects_english_marker() -> None:
    hit, _ = is_compound_goal("organize the files, then draw a chart")
    assert hit


def test_is_compound_goal_single_step_is_false() -> None:
    hit, _ = is_compound_goal("organize by file type")
    assert not hit


def test_is_compound_goal_empty_is_false() -> None:
    hit, _ = is_compound_goal("")
    assert not hit
