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
from app.ui._autodetect import autodetect_planner, autodetect_skill


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
