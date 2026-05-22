"""Phase 22 (v0.22) — locale → label-dict mapping for the bilingual
report templates under ``app/templates/reports/``.

Each label dict has the SAME KEYS in both locales so a template can
say ``{{ T.execution_summary_h2 }}`` and trust the field exists. The
labels lean toward natural product language ("最终报告" not "最终结果
报告") and intentionally drop tech-coupled words like "Verifier" in
favour of "验证" / "Checks".

Adding a new label: add it to BOTH dicts. The renderer uses
``StrictUndefined`` so any template reference to a missing label
fails loudly at render time — that's a feature, not a bug.
"""

from __future__ import annotations

from typing import Any

# fmt: off
_LABELS_ZH: dict[str, Any] = {
    "_locale_code":              "zh-CN",
    # Headings
    "final_report_h1_prefix":    "最终报告 ·",
    "execution_summary_h2":      "执行概览",
    "verifier_verdict_h2":       "验证结果",
    "failed_actions_h2":         "失败的操作",
    "generated_files_h2":        "生成的文件",
    "created_dirs_h2":           "创建的目录",
    "outputs_h2":                "输出",
    "expected_outputs_h2":       "预期输出",
    "how_to_undo_h2":            "如何撤销",
    "checks_table_header_check": "检查项",
    "checks_table_header_result":"结果",
    "checks_table_header_detail":"详情",
    # Fields
    "skill_label":               "能力",
    "workspace_label":           "工作区",
    "goal_label":                "目标",
    "total_actions_label":       "操作总数",
    "succeeded_label":           "成功",
    "failed_label":              "失败",
    "skipped_label":             "已跳过(断点)",
    "rollback_entries_label":    "回滚条目",
    # data_analyzer / data_reporter / pdf_indexer specifics
    "outcome_h2":                "执行结果",
    "actions_inline":            "操作",
    "succeeded_inline":          "成功",
    "failed_inline":             "失败",
    "skipped_inline":            "已跳过",
    "verifier_inline":           "验证",
    "sources_analyzed_h2":       "已分析的数据源",
    "sources_summary_template":  "扫描了 {total} 个表格文件；解析 {ok} 个，跳过 {bad} 个。",
    "rows_cols_template":        "{rows} 行 × {cols} 列",
    "truncated_suffix":          "(截断)",
    "error_label":               "错误",
    "index_sources_h2":          "索引来源",
    "index_sources_summary_template": "综合自 {count} 个 PDF 源文件：",
    "preview_marker":            "含预览",
    "filename_only_marker":      "仅文件名",
    "output_h2":                 "输出",
    "rollback_restores_suffix":  "(可通过撤销恢复)",
    "written_rollback_suffix":   "(已写入；可通过撤销恢复)",
    # Verdict badges
    "passed_badge":              "通过",
    "failed_badge":              "失败",
    "ok_short":                  "通过",
    "fail_short":                "失败",
    # Undo
    "how_to_undo_intro":         "运行以下命令可一键撤销本次执行：",
}

_LABELS_EN: dict[str, Any] = {
    "_locale_code":              "en-US",
    "final_report_h1_prefix":    "Final report ·",
    "execution_summary_h2":      "Execution summary",
    "verifier_verdict_h2":       "Verifier verdict",
    "failed_actions_h2":         "Failed actions",
    "generated_files_h2":        "Generated files",
    "created_dirs_h2":           "Created directories",
    "outputs_h2":                "Outputs",
    "expected_outputs_h2":       "Expected outputs",
    "how_to_undo_h2":            "How to undo",
    "checks_table_header_check": "Check",
    "checks_table_header_result":"Result",
    "checks_table_header_detail":"Detail",
    "skill_label":               "Skill",
    "workspace_label":           "Workspace",
    "goal_label":                "Goal",
    "total_actions_label":       "Total actions",
    "succeeded_label":           "Succeeded",
    "failed_label":              "Failed",
    "skipped_label":             "Skipped (checkpoint)",
    "rollback_entries_label":    "Rollback entries recorded",
    "outcome_h2":                "Outcome",
    "actions_inline":            "Actions",
    "succeeded_inline":          "succeeded",
    "failed_inline":             "failed",
    "skipped_inline":            "skipped",
    "verifier_inline":           "Verifier",
    "sources_analyzed_h2":       "Sources analyzed",
    "sources_summary_template":  "Scanned {total} tabular file(s); {ok} parsed, {bad} skipped.",
    "rows_cols_template":        "{rows} rows × {cols} cols",
    "truncated_suffix":          "(truncated)",
    "error_label":               "error",
    "index_sources_h2":          "Index sources",
    "index_sources_summary_template": "Synthesized from {count} source PDF(s):",
    "preview_marker":            "preview",
    "filename_only_marker":      "filename-only",
    "output_h2":                 "Output",
    "rollback_restores_suffix":  "(rollback restores)",
    "written_rollback_suffix":   "(written; rollback restores)",
    "passed_badge":              "PASSED",
    "failed_badge":              "FAILED",
    "ok_short":                  "ok",
    "fail_short":                "fail",
    "how_to_undo_intro":         "Run the following command to undo this execution:",
}
# fmt: on


def labels_for(locale: str | None) -> dict[str, Any]:
    """Return the label dict for ``locale``. Defaults to ``zh-CN`` when
    ``locale`` is None, the empty string, or any unrecognised value —
    matches :data:`app.schemas.task.DEFAULT_LOCALE` so reporters
    without explicit locale plumbing still produce Chinese output."""
    if locale == "en-US":
        return _LABELS_EN
    return _LABELS_ZH
