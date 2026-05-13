"""Phase 8.1 / v0.8.0 — auto-detect skill + planner heuristics.

The Plan page no longer asks the user to pick a Skill manually. The
heuristic in ``app/ui/_autodetect.py`` chooses a skill based on:

  * Keywords in the goal (English + Chinese)
  * The workspace's file-type distribution
  * Whether the chosen skill supports LLM planning

These tests exercise every branch of that decision tree using
synthetic ``WorkspaceSnapshot`` instances. They don't import
Streamlit — the auto-detect module is deliberately Streamlit-free
for exactly this reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas import FileMeta, WorkspaceSnapshot
from app.skills import get_default_registry
from app.ui._autodetect import (
    autodetect_planner,
    autodetect_skill,
    detect_capability_gap,
    is_compound_goal,
)


def _snap(file_types: dict[str, int]) -> WorkspaceSnapshot:
    """Build a synthetic snapshot with N files of each given type."""
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
        total_size_bytes=sum(f.size_bytes for f in files),
    )


# ───────────────────────────────────── autodetect_skill


def test_empty_goal_defaults_to_folder_organizer() -> None:
    """Until the user types a goal we have nothing to go on — default
    to the most generic skill."""
    reg = get_default_registry()
    choice = autodetect_skill("", _snap({"text": 3}), reg)
    assert choice.name == "folder_organizer"
    assert "goal" in choice.reason.lower()


def test_data_analyzer_for_csv_plus_analyze_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"tabular": 2, "text": 1})
    choice = autodetect_skill("analyze the sales data", snap, reg)
    assert choice.name == "data_analyzer"
    assert "tabular" in choice.reason.lower()


def test_data_analyzer_chinese_keyword() -> None:
    """Chinese intent words should trigger the same routing."""
    reg = get_default_registry()
    snap = _snap({"tabular": 2})
    choice = autodetect_skill("帮我分析这些数据", snap, reg)
    assert choice.name == "data_analyzer"


def test_data_reporter_for_csv_plus_report_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"tabular": 2})
    choice = autodetect_skill("generate a report and summary", snap, reg)
    assert choice.name == "data_reporter"


def test_data_reporter_for_xlsx_treated_as_tabular() -> None:
    """xlsx files classify as "excel" not "tabular"; the heuristic
    treats them as tabular for routing purposes."""
    reg = get_default_registry()
    snap = _snap({"excel": 2})
    choice = autodetect_skill("statistics on this", snap, reg)
    assert choice.name == "data_reporter"


def test_pdf_indexer_for_pdfs_plus_pdf_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"pdf": 3})
    choice = autodetect_skill("index all the papers", snap, reg)
    assert choice.name == "pdf_indexer"


def test_pdf_indexer_chinese_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"pdf": 3})
    choice = autodetect_skill("给所有论文生成索引", snap, reg)
    assert choice.name == "pdf_indexer"


def test_folder_organizer_for_organize_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"text": 2, "image": 2, "pdf": 1})
    choice = autodetect_skill("organize by file type", snap, reg)
    assert choice.name == "folder_organizer"


def test_folder_organizer_chinese_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"text": 2, "image": 2})
    choice = autodetect_skill("按文件类型整理", snap, reg)
    assert choice.name == "folder_organizer"


def test_fallback_to_data_reporter_when_tabular_no_keyword() -> None:
    """Workspace heavily tabular but goal lacks a clear keyword →
    default to producing a data report, since that's the most useful
    thing to do with CSV files when the user is vague."""
    reg = get_default_registry()
    snap = _snap({"tabular": 5})
    choice = autodetect_skill("do something useful", snap, reg)
    assert choice.name == "data_reporter"


def test_fallback_to_pdf_indexer_when_pdfs_no_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"pdf": 4})
    choice = autodetect_skill("do something useful", snap, reg)
    assert choice.name == "pdf_indexer"


def test_universal_fallback_folder_organizer() -> None:
    """No tabular, no PDFs, no recognized keyword → universal fallback."""
    reg = get_default_registry()
    snap = _snap({"image": 3, "video": 2})
    choice = autodetect_skill("do something useful", snap, reg)
    assert choice.name == "folder_organizer"


def test_empty_snapshot_default_organizer() -> None:
    reg = get_default_registry()
    snap = _snap({})
    choice = autodetect_skill("organize", snap, reg)
    assert choice.name == "folder_organizer"


def test_none_snapshot_handled() -> None:
    """The Plan page passes None when the cheap scan hasn't completed
    yet — the function must not crash."""
    reg = get_default_registry()
    choice = autodetect_skill("organize", None, reg)
    assert choice.name == "folder_organizer"


# ───────────────────────────────────── autodetect_planner


def test_planner_rule_when_skill_does_not_support_llm() -> None:
    """pdf_indexer + data_reporter don't override plan_with_llm.
    Even with semantic intent keywords, the planner falls back to rule."""
    reg = get_default_registry()
    choice = autodetect_planner("summarize semantically", "pdf_indexer", reg)
    assert choice.name == "rule"
    assert "doesn't support" in choice.reason.lower()


def test_planner_llm_when_intent_keyword_and_skill_supports() -> None:
    """folder_organizer supports LLM; goal mentions semantic intent →
    pick llm."""
    reg = get_default_registry()
    choice = autodetect_planner("organize by content", "folder_organizer", reg)
    assert choice.name == "llm"


def test_planner_llm_chinese_intent() -> None:
    reg = get_default_registry()
    choice = autodetect_planner("按内容智能分类", "folder_organizer", reg)
    assert choice.name == "llm"


def test_planner_rule_when_no_semantic_keyword() -> None:
    """folder_organizer + plain 'organize' goal → rule planner is
    sufficient and 100× faster than LLM."""
    reg = get_default_registry()
    choice = autodetect_planner("organize by file type", "folder_organizer", reg)
    assert choice.name == "rule"


def test_planner_for_unknown_skill_safely_defaults_to_rule() -> None:
    """If the skill registry doesn't know the skill (shouldn't happen
    in production, but guard against it), default to the safer rule
    planner rather than crashing."""
    reg = get_default_registry()
    choice = autodetect_planner("anything", "no_such_skill", reg)
    assert choice.name == "rule"


def test_data_analyzer_supports_llm_branch() -> None:
    """data_analyzer overrides plan_with_llm; semantic intent should
    route to llm."""
    reg = get_default_registry()
    choice = autodetect_planner("analyze data by topic", "data_analyzer", reg)
    assert choice.name == "llm"


# ───────────────────────────────────── v0.8.2 — compound goal → LLM


def test_compound_goal_chinese_marker() -> None:
    """Goals with 然后/再/最后 are multi-step → LLM."""
    hit, reason = is_compound_goal("先整理，然后绘制图表")
    assert hit
    assert "然后" in reason or "marker" in reason


def test_compound_goal_english_marker() -> None:
    hit, reason = is_compound_goal("organize the files, then make a report")
    assert hit
    assert "then" in reason or "marker" in reason


def test_compound_goal_multiple_verbs() -> None:
    """Three+ distinct action verbs also trigger the compound path."""
    hit, _ = is_compound_goal("organize sort summarize and visualize the workspace")
    assert hit


def test_single_step_is_not_compound() -> None:
    hit, _ = is_compound_goal("organize by file type")
    assert not hit


def test_compound_goal_routes_planner_to_llm() -> None:
    """The user's exact testing-grade goal — should land on LLM
    because of the multi-step compound marker."""
    reg = get_default_registry()
    goal = "将文件按种类整理，然后总结，最后绘制柱状图"
    choice = autodetect_planner(goal, "folder_organizer", reg)
    assert choice.name == "llm"
    assert "compound" in choice.reason.lower()


# ───────────────────────────────────── v0.8.2 — prefer_llm pref


def test_prefer_llm_overrides_simple_goal() -> None:
    """When the user has flipped prefer_llm_planner on, even a simple
    goal lands on the LLM planner."""
    reg = get_default_registry()
    choice = autodetect_planner("organize by file type", "folder_organizer", reg, prefer_llm=True)
    assert choice.name == "llm"
    assert "preference" in choice.reason.lower()


def test_prefer_llm_does_not_force_rule_skills_to_llm() -> None:
    """If the skill itself doesn't support LLM (pdf_indexer,
    data_reporter, workspace_visualizer), the toggle is silently
    ignored — we can't conjure an LLM planner that doesn't exist."""
    reg = get_default_registry()
    choice = autodetect_planner("anything", "pdf_indexer", reg, prefer_llm=True)
    assert choice.name == "rule"


# ───────────────────────────────────── v0.8.2 — workspace_visualizer routing


def test_workspace_visualizer_for_chart_no_tabular() -> None:
    """No tabular data + chart keyword → workspace_visualizer (real PNG)."""
    reg = get_default_registry()
    snap = _snap({"pdf": 3, "image": 2})
    choice = autodetect_skill("draw a bar chart of my file counts", snap, reg)
    assert choice.name == "workspace_visualizer"


def test_workspace_visualizer_chinese_chart_keyword() -> None:
    reg = get_default_registry()
    snap = _snap({"image": 4})
    choice = autodetect_skill("绘制一个柱状图", snap, reg)
    assert choice.name == "workspace_visualizer"


def test_data_analyzer_wins_on_tabular_with_chart_keyword() -> None:
    """If tabular files dominate, route to data_analyzer (it draws
    per-column charts), not workspace_visualizer (which charts file
    counts)."""
    reg = get_default_registry()
    snap = _snap({"tabular": 3, "excel": 2})
    choice = autodetect_skill("chart the sales data", snap, reg)
    assert choice.name == "data_analyzer"


def test_organize_wins_in_mixed_workspace_with_stray_xlsx() -> None:
    """v0.8.2 regression: a workspace with a single xlsx + many other
    files was hijacked into data_analyzer when the goal asked for
    organize+chart. The user wanted to organize their workspace,
    not analyze the spreadsheet's columns."""
    reg = get_default_registry()
    snap = _snap({"excel": 1, "pdf": 4, "image": 4, "text": 2})
    goal = "将文件按种类整理，然后绘制柱状图"
    choice = autodetect_skill(goal, snap, reg)
    assert choice.name == "folder_organizer"


# ───────────────────────────────────── v0.8.2 — capability gap


def test_capability_gap_for_organize_plus_chart_picks_folder_organizer() -> None:
    """folder_organizer can't draw charts. Gap helper should warn and
    nudge toward workspace_visualizer as the second step."""
    reg = get_default_registry()
    snap = _snap({"pdf": 4, "image": 2, "text": 2})
    goal = "整理文件并绘制柱状图"
    sk = autodetect_skill(goal, snap, reg)
    assert sk.name == "folder_organizer"
    gap = detect_capability_gap(goal, sk.name, snap)
    assert gap is not None
    assert "workspace_visualizer" == gap.suggested_skill


def test_capability_gap_for_organize_plus_chart_picks_visualizer() -> None:
    """The mirror case: when workspace_visualizer is somehow picked
    (e.g. user manually overrode), warn that the organize part is
    missing and nudge toward folder_organizer."""
    snap = _snap({"pdf": 4, "image": 2})
    goal = "organize files and chart the counts"
    gap = detect_capability_gap(goal, "workspace_visualizer", snap)
    assert gap is not None
    assert gap.suggested_skill == "folder_organizer"


def test_capability_gap_warns_when_chart_routed_to_data_reporter() -> None:
    """data_reporter writes markdown, not real PNGs. If something
    routes a chart-asking goal there, warn the user."""
    snap = _snap({"tabular": 2})
    gap = detect_capability_gap("draw a chart of the data", "data_reporter", snap)
    assert gap is not None
    assert "markdown" in gap.message.lower()
    assert gap.suggested_skill in ("data_analyzer", "workspace_visualizer")


def test_capability_gap_none_for_clean_routing() -> None:
    """workspace_visualizer + chart-only goal → no gap."""
    snap = _snap({"pdf": 3})
    gap = detect_capability_gap("draw a chart", "workspace_visualizer", snap)
    assert gap is None
