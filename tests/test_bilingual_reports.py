"""Phase 22 (v0.22) — bilingual report templates substrate tests.

Covers:
  - labels_for() returns zh-CN for missing/unknown locale, en-US only on exact match.
  - render_report() routes to the right .j2 file with the right T dict.
  - Each of the 5 reporters' templates renders cleanly in BOTH locales
    given a minimal context dict.
  - Reporter functions plumb task.locale through into the rendered
    output (the integration check that B2 + D actually compose).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.executor import ExecutionOutcome
from app.schemas import (
    ActionPlan,
    ExecutionRecord,
    ExecutionStatus,
    RollbackManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
)
from app.schemas.action import Action, ActionType, RiskLevel
from app.templates import render_report
from app.templates._labels import labels_for

# ─────────────────────── labels_for

def test_labels_for_defaults_to_zh_when_locale_missing():
    assert labels_for(None)["_locale_code"] == "zh-CN"
    assert labels_for("")["_locale_code"] == "zh-CN"
    assert labels_for("fr-FR")["_locale_code"] == "zh-CN"


def test_labels_for_returns_en_on_exact_match():
    assert labels_for("en-US")["_locale_code"] == "en-US"
    assert labels_for("en-US")["passed_badge"] == "PASSED"


def test_label_dicts_share_keys():
    """Catch the worst class of i18n bug: a key added in one locale only
    breaks StrictUndefined-rendering at runtime in the other locale."""
    zh = set(labels_for("zh-CN").keys())
    en = set(labels_for("en-US").keys())
    assert zh == en, f"locale label drift: zh-only={zh - en}, en-only={en - zh}"


# ─────────────────────── render_report — template routing

_MIN_FOLDER_CTX = {
    "task_id": "t-001",
    "skill": "folder_organizer",
    "workspace_root": "/tmp/ws",
    "user_goal": "整理文件",
    "total_actions": 3,
    "succeeded": 3,
    "failed": 0,
    "skipped": 0,
    "rollback_entries": 3,
    "verifier_passed": True,
    "verifier_summary": "all checks passed",
    "verifier_checks": [],
    "failed_actions": [],
    "generated_files": ["papers/index.md"],
    "created_dirs": ["papers"],
    "run_id": "2026-05-22-001",
}


def test_render_report_zh_renders_chinese_headings():
    md = render_report("folder_organizer", locale="zh-CN", ctx=_MIN_FOLDER_CTX)
    assert "## 执行概览" in md
    assert "## 验证结果" in md
    assert "## 如何撤销" in md
    assert "**通过**" in md
    assert "PASSED" not in md


def test_render_report_en_renders_english_headings():
    md = render_report("folder_organizer", locale="en-US", ctx=_MIN_FOLDER_CTX)
    assert "## Execution summary" in md
    assert "## Verifier verdict" in md
    assert "## How to undo" in md
    assert "**PASSED**" in md
    assert "执行概览" not in md


def test_render_report_failure_badge_zh():
    ctx = {**_MIN_FOLDER_CTX, "verifier_passed": False, "verifier_summary": "1 fail"}
    md = render_report("folder_organizer", locale="zh-CN", ctx=ctx)
    assert "**失败**" in md


def test_render_report_unknown_template_raises():
    from jinja2 import TemplateNotFound

    with pytest.raises(TemplateNotFound):
        render_report("does_not_exist", locale="zh-CN", ctx={})


# ─────────────────────── reporter integration — task.locale flows through

def _make_outcome(*, run_id: str = "2026-05-22-002") -> ExecutionOutcome:
    return ExecutionOutcome(
        run_id=run_id,
        records=[
            ExecutionRecord(
                run_id=run_id,
                action_id="a-001",
                status=ExecutionStatus.SUCCESS,
                error=None,
            )
        ],
        manifest=RollbackManifest(run_id=run_id, task_id="t-locale", entries=[]),
        success=True,
    )


def _make_task(locale: str) -> TaskSpec:
    return TaskSpec(
        task_id="t-locale",
        skill="folder_organizer",
        user_goal="整理",
        workspace_root="/tmp/ws",
        locale=locale,
    )


def _make_plan(*, expected_outputs: list[str] | None = None, with_index_action: bool = False) -> ActionPlan:
    actions: list[Action] = []
    if with_index_action:
        actions = [
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="r",
                risk_level=RiskLevel.LOW,
                metadata={
                    "content": "hello",
                    "provenance": {
                        "sources": [
                            {
                                "path": "data.csv",
                                "rows_read": 10,
                                "cols": 3,
                                "truncated": False,
                            }
                        ]
                    },
                },
            )
        ]
    return ActionPlan(
        plan_id="p-locale",
        task_id="t-locale",
        summary="test plan",
        actions=actions,
        expected_outputs=expected_outputs or [],
        risk_summary="ok",
    )


def _make_verification() -> VerificationResult:
    return VerificationResult(
        task_id="t-locale",
        run_id="2026-05-22-002",
        passed=True,
        summary="all good",
        checks=[VerificationCheck(name="c1", passed=True, detail="ok")],
        failed_checks=[],
    )


def test_folder_organizer_reporter_respects_locale_zh():
    from app.skills.folder_organizer.reporter import render_final_report

    out = render_final_report(
        task=_make_task("zh-CN"),
        plan=_make_plan(),
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "执行概览" in out
    assert "Execution summary" not in out


def test_folder_organizer_reporter_respects_locale_en():
    from app.skills.folder_organizer.reporter import render_final_report

    out = render_final_report(
        task=_make_task("en-US"),
        plan=_make_plan(),
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "Execution summary" in out
    assert "执行概览" not in out


def test_data_analyzer_reporter_renders_both_locales():
    from app.skills.data_analyzer.reporter import render_final_report

    zh = render_final_report(
        task=_make_task("zh-CN"),
        plan=_make_plan(),
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    en = render_final_report(
        task=_make_task("en-US"),
        plan=_make_plan(),
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "data_analyzer" in zh and "data_analyzer" in en
    assert "执行概览" in zh
    assert "Execution summary" in en


def test_workspace_visualizer_reporter_renders_both_locales():
    from app.skills.workspace_visualizer.reporter import render_final_report

    plan = _make_plan(expected_outputs=["images/file_counts.png", "file_counts_summary.md"])
    zh = render_final_report(
        task=_make_task("zh-CN"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    en = render_final_report(
        task=_make_task("en-US"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "## 输出" in zh
    assert "## Outputs" in en


def test_data_reporter_renders_both_locales():
    from app.skills.data_reporter.reporter import render_data_report

    plan = _make_plan(with_index_action=True)
    zh = render_data_report(
        task=_make_task("zh-CN"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    en = render_data_report(
        task=_make_task("en-US"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "已分析的数据源" in zh
    assert "Sources analyzed" in en


def test_pdf_indexer_reporter_renders_both_locales():
    from app.skills.pdf_indexer.reporter import render_pdf_index_report

    plan = ActionPlan(
        plan_id="p-locale",
        task_id="t-locale",
        summary="pdf plan",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="pdf_index.md",
                reason="r",
                risk_level=RiskLevel.LOW,
                metadata={
                    "content": "pdf",
                    "provenance": {
                        "sources": [
                            {"path": "a.pdf", "title": "Agents", "has_preview": True}
                        ]
                    },
                },
            )
        ],
        risk_summary="ok",
    )
    zh = render_pdf_index_report(
        task=_make_task("zh-CN"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    en = render_pdf_index_report(
        task=_make_task("en-US"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "索引来源" in zh
    assert "Index sources" in en


def test_agent_reporter_renders_both_locales():
    from app.skills.agent.reporter import render_final_report

    plan = _make_plan(expected_outputs=["README.md", "SOURCES.md"])
    zh = render_final_report(
        task=_make_task("zh-CN"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    en = render_final_report(
        task=_make_task("en-US"),
        plan=plan,
        outcome=_make_outcome(),
        verification=_make_verification(),
    )
    assert "agent (LLM)" in zh and "agent (LLM)" in en
    assert "执行概览" in zh
    assert "Execution summary" in en


# ─────────────────────── template files exist on disk

def test_all_expected_templates_ship_on_disk():
    """A missing .j2 would only show up at runtime via the rendered report."""
    root = Path(__file__).parents[1] / "app" / "templates" / "reports"
    for name in (
        "folder_organizer",
        "workspace_visualizer",
        "data_analyzer",
        "data_reporter",
        "pdf_indexer",
        "agent",
    ):
        assert (root / f"{name}.md.j2").is_file(), f"missing template: {name}"
