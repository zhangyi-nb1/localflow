"""i18n framework for the Streamlit UI (Phase 8.1 / v0.8.0).

Two-locale lookup with placeholder interpolation. No build step.

Why a hand-rolled dict instead of gettext / fluent / babel:
  * The UI has ~120 strings — small enough that a flat dict beats any
    catalog format on read-and-write ergonomics.
  * Streamlit reruns on every interaction; we want zero per-render
    parse cost. The dict is module-level, loaded once.
  * Designed to be Streamlit-free at import time so the dictionary
    and ``t()`` lookup are unit-testable without spinning a Streamlit
    runtime — only ``render_language_toggle`` imports streamlit.

Convention: keys are dotted ``<scope>.<element>[.<purpose>]``. The
test file enforces this with a regex.
"""

from __future__ import annotations

import re
from typing import Literal

Lang = Literal["en", "zh"]
DEFAULT_LANG: Lang = "en"
SESSION_LANG_KEY = "ui_lang"

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")


_DICT: dict[str, dict[Lang, str]] = {
    # ───────────────────────── app branding ─────────────────────────
    "app.title": {
        "en": "LocalFlow",
        "zh": "LocalFlow",
    },
    "app.subtitle.home": {
        "en": "Local-first Agent Execution Harness for safe, reviewable, rollback-ready workspace work.",
        "zh": "面向本地工作区的 Agent Execution Harness：安全、可预览、可回退、可验证。",
    },
    "app.page_title.home": {"en": "Home", "zh": "首页"},
    "app.page_title.pack": {"en": "Create Pack", "zh": "新建成果包"},
    "app.page_title.workspace": {"en": "Workspace", "zh": "工作区"},
    "app.page_title.runs": {"en": "Runs", "zh": "运行记录"},
    "app.page_title.plan": {"en": "Plan", "zh": "规划"},
    "app.page_title.execute": {"en": "Execute", "zh": "执行"},
    "app.page_title.rollback": {"en": "Rollback", "zh": "回滚"},
    "app.page_title.memory": {"en": "Memory", "zh": "偏好记忆"},
    "app.page_title.settings": {"en": "Settings", "zh": "设置"},
    "app.header_prefix": {
        "en": "🌀 LocalFlow — {title}",
        "zh": "🌀 LocalFlow — {title}",
    },
    # ───────────────────────── sidebar ─────────────────────────
    "sidebar.language.label": {
        "en": "Language / 语言",
        "zh": "Language / 语言",
    },
    "sidebar.language.en": {"en": "English", "zh": "English"},
    "sidebar.language.zh": {"en": "中文", "zh": "中文"},
    "sidebar.workspace.header": {"en": "Workspace", "zh": "工作区"},
    "sidebar.workspace.active_label": {
        "en": "**Active workspace:**",
        "zh": "**当前工作区：**",
    },
    "sidebar.workspace.none_active": {
        "en": "_(none selected — pick one below)_",
        "zh": "_(尚未选择 — 在下方挑一个)_",
    },
    "sidebar.workspace.source_label": {
        "en": "Source",
        "zh": "来源",
    },
    "sidebar.workspace.source_sandbox": {
        "en": "Sandbox subdir",
        "zh": "Sandbox 子目录",
    },
    "sidebar.workspace.source_custom": {
        "en": "Custom path (?unsafe=1 required)",
        "zh": "自定义路径（需 ?unsafe=1）",
    },
    "sidebar.workspace.custom_locked": {
        "en": "🔒 Custom path locked — reload with ?unsafe=1 to enable.",
        "zh": "🔒 自定义路径已锁定 — 在 URL 末尾追加 ?unsafe=1 后重载页面即可解锁。",
    },
    "sidebar.workspace.sandbox_root_caption": {
        "en": "Sandbox root: `{path}`",
        "zh": "Sandbox 根目录：`{path}`",
    },
    "sidebar.workspace.pick_label": {
        "en": "Pick workspace",
        "zh": "选择工作区",
    },
    "sidebar.workspace.pick_help": {
        "en": "Subdirectories of `./sandbox/`. Refresh after creating new ones.",
        "zh": "`./sandbox/` 下的子目录。新建后点 Refresh。",
    },
    "sidebar.workspace.no_choices": {
        "en": (
            "No subdirectories under `sandbox/` yet. Create one "
            "(e.g. `mkdir sandbox/demo`) or pick **Custom path** with "
            "`?unsafe=1` in the URL."
        ),
        "zh": (
            "`sandbox/` 下还没有子目录。新建一个"
            "（如 `mkdir sandbox/demo`），或在 URL 加 `?unsafe=1` 后选择 **自定义路径**。"
        ),
    },
    "sidebar.workspace.custom_label": {
        "en": "Workspace absolute path",
        "zh": "工作区的绝对路径",
    },
    "sidebar.workspace.custom_placeholder": {
        "en": "C:\\path\\to\\your\\workspace",
        "zh": "C:\\path\\to\\your\\workspace",
    },
    "sidebar.workspace.custom_empty_caption": {
        "en": "Type an absolute path to an existing directory above.",
        "zh": "请在上方输入一个已存在目录的绝对路径。",
    },
    "sidebar.workspace.custom_ok": {
        "en": "✅ Using custom workspace: `{path}`",
        "zh": "✅ 使用自定义工作区：`{path}`",
    },
    "sidebar.refresh": {"en": "🔄 Refresh", "zh": "🔄 刷新"},
    "sidebar.refresh_help": {
        "en": "Re-scan sandbox/ for new subdirs",
        "zh": "重新扫描 sandbox/ 下的子目录",
    },
    "sidebar.memory.header": {"en": "Memory", "zh": "偏好记忆"},
    "sidebar.memory.all_default": {
        "en": "All defaults — no preferences influencing runs.",
        "zh": "全部默认 — 没有偏好影响后续运行。",
    },
    "sidebar.memory.forbidden_count": {
        "en": "🚫 {n} forbidden_paths",
        "zh": "🚫 {n} 条 forbidden_paths",
    },
    "sidebar.memory.naming_style": {
        "en": "📝 naming_style: `{value}`",
        "zh": "📝 命名风格：`{value}`",
    },
    "sidebar.memory.prefer_llm": {
        "en": "🤖 prefer_llm_planner: on",
        "zh": "🤖 已开启 LLM 优先",
    },
    "sidebar.memory.error": {
        "en": "Memory store error: {err}",
        "zh": "偏好读取错误：{err}",
    },
    # ───────────────────────── unsafe banner ─────────────────────────
    "unsafe.banner": {
        "en": (
            "⚠️ **Unsafe path mode active.** The UI is allowing workspaces "
            "outside `./sandbox/`. The kernel's policy_guard + "
            "`forbidden_paths` still enforce real boundaries — but you've "
            "lifted the UI-level guard rail. To disable: remove `?unsafe=1` "
            "from the URL."
        ),
        "zh": (
            "⚠️ **已启用 Unsafe 路径模式。**UI 允许选择 `./sandbox/` 之外的工作区。"
            "内核的 policy_guard 与 `forbidden_paths` 仍在强制真正的边界，"
            "但你已经放开了 UI 层的护栏。如需关闭：从 URL 中删除 `?unsafe=1`。"
        ),
    },
    # ───────────────────────── workspace page ─────────────────────────
    "workspace.subtitle": {
        "en": "Browse the active workspace — counts, sizes, and recent runs.",
        "zh": "查看当前工作区 — 文件数、大小、最近的运行记录。",
    },
    "workspace.no_workspace": {
        "en": "Pick a workspace in the sidebar to see its contents.",
        "zh": "请在左侧栏挑选一个工作区，然后查看它的内容。",
    },
    "workspace.summary.title": {
        "en": "### Summary",
        "zh": "### 概览",
    },
    "workspace.summary.total_files": {"en": "Total files", "zh": "文件总数"},
    "workspace.summary.total_size": {"en": "Total size", "zh": "总大小"},
    "workspace.summary.runs_here": {"en": "Runs on this workspace", "zh": "在此工作区的运行数"},
    "workspace.file_list.title": {
        "en": "### Files",
        "zh": "### 文件列表",
    },
    "workspace.file_list.empty": {
        "en": "_(workspace is empty)_",
        "zh": "_(工作区是空的)_",
    },
    "workspace.file_list.col.path": {"en": "Path", "zh": "路径"},
    "workspace.file_list.col.size": {"en": "Size", "zh": "大小"},
    "workspace.file_list.col.modified": {"en": "Modified", "zh": "最近修改"},
    "workspace.file_list.truncated": {
        "en": "Showing first {n} of {total} files. Use the CLI or filesystem to browse the rest.",
        "zh": "仅显示前 {n} / {total} 个文件。其余请用 CLI 或文件管理器查看。",
    },
    # ───────────────────────── runs page ─────────────────────────
    "runs.subtitle": {
        "en": "Every task LocalFlow has executed on this machine — open, re-read, or undo.",
        "zh": "本机上 LocalFlow 跑过的每一个任务 — 可查看、重读或撤销。",
    },
    "runs.empty": {
        "en": "No runs yet. Try a pack on the **📦 Create Pack** page to get started.",
        "zh": "还没有任何运行记录。可以去 **📦 新建成果包** 页面试一下。",
    },
    "runs.empty_ws": {
        "en": "No runs for the current workspace yet. Switch workspace in the sidebar to see others.",
        "zh": "当前工作区还没有运行记录。可以在左侧栏切换工作区查看其他记录。",
    },
    "runs.filter.this_workspace": {
        "en": "Filter to the active workspace only",
        "zh": "只显示当前工作区的记录",
    },
    "runs.table.col.task_id": {"en": "Task", "zh": "任务"},
    "runs.table.col.skill": {"en": "Capability", "zh": "能力"},
    "runs.table.col.workspace": {"en": "Workspace", "zh": "工作区"},
    "runs.table.col.status": {"en": "Status", "zh": "状态"},
    "runs.table.col.rollback": {"en": "Rollback", "zh": "撤销"},
    "runs.table.col.trace": {"en": "Trace", "zh": "Trace"},
    "runs.table.col.verify": {"en": "Verify", "zh": "校验"},
    "runs.status.executed": {"en": "✅ Executed", "zh": "✅ 已执行"},
    "runs.status.planned": {"en": "📋 Planned only", "zh": "📋 仅规划"},
    "runs.status.verified": {"en": "✅ passed", "zh": "✅ 通过"},
    "runs.status.failed": {"en": "❌ failed", "zh": "❌ 未通过"},
    "runs.status.unverified": {"en": "—", "zh": "—"},
    "runs.rollback.available": {"en": "available", "zh": "可撤销"},
    "runs.rollback.none": {"en": "—", "zh": "—"},
    "runs.action.open_rollback": {
        "en": "↺ Open in Rollback",
        "zh": "↺ 打开撤销页面",
    },
    "runs.action.view_report": {
        "en": "📄 View final report",
        "zh": "📄 查看最终报告",
    },
    "runs.detail.no_report": {
        "en": "_(no final report on disk for this task)_",
        "zh": "_(该任务没有最终报告文件)_",
    },
    "runs.detail.heading": {
        "en": "### Run evidence: `{task_id}`",
        "zh": "### 运行证据：`{task_id}`",
    },
    "runs.detail.run_dir": {
        "en": "Run directory: `{path}`",
        "zh": "运行目录：`{path}`",
    },
    "runs.detail.trace_events": {"en": "Trace events", "zh": "Trace 事件"},
    "runs.detail.artifact_count": {"en": "Artifacts", "zh": "产物文件"},
    "runs.detail.rollback_entries": {"en": "Rollback entries", "zh": "回滚条目"},
    "runs.detail.dry_run": {"en": "Dry-run preview", "zh": "Dry-run 预览"},
    "runs.detail.verify_report": {"en": "Verify report", "zh": "校验报告"},
    "runs.detail.trace": {"en": "Trace tail ({n} events)", "zh": "Trace 末尾（{n} 条事件）"},
    "runs.detail.rollback": {"en": "Rollback manifest", "zh": "回滚清单"},
    "runs.detail.artifacts": {"en": "Artifacts on disk", "zh": "磁盘产物"},
    "runs.detail.missing": {"en": "_Not recorded for this run._", "zh": "_本次运行未记录。_"},
    "runs.detail.trace_missing": {"en": "_No trace events found._", "zh": "_未找到 trace 事件。_"},
    "runs.detail.verify_passed": {"en": "Check: ✅ PASSED", "zh": "校验：✅ 通过"},
    "runs.detail.verify_failed": {"en": "Check: ❌ FAILED", "zh": "校验：❌ 未通过"},
    # ───────────────────────── home page ─────────────────────────
    "home.hero.tagline": {
        "en": "Let agents act locally through typed plans, preview, approval, checks, trace, repair, and rollback.",
        "zh": "让智能体通过结构化计划、预览、确认、校验、追踪、修复和回退来安全操作本地工作区。",
    },
    "home.packs.heading": {
        "en": "### Run a harness-backed demo pack",
        "zh": "### 运行一个由 Harness 托管的示例成果包",
    },
    "home.packs.subheading": {
        "en": "Packs are ready-made workflows that exercise the same plan, preview, execute, verify, repair, and rollback path.",
        "zh": "成果包只是现成工作流，会走同一套规划、预览、执行、校验、修复和回退链路。",
    },
    "home.packs.try_button": {
        "en": "Try {title} →",
        "zh": "试试 {title} →",
    },
    "home.packs.no_workspace": {
        "en": "Pick a workspace in the sidebar to enable the pack buttons.",
        "zh": "请先在左侧栏挑选一个工作区，再选成果包。",
    },
    "home.packs.unavailable": {
        "en": "_Pack `{name}` not found in the recipe registry._",
        "zh": "_未在 recipe 注册表中找到 `{name}` 成果包。_",
    },
    "home.advanced.heading": {
        "en": "### Inspect the harness lifecycle directly",
        "zh": "### 直接查看 Harness 生命周期",
    },
    "home.intro": {
        "en": (
            "Prefer to inspect the controlled execution path yourself? "
            "Walk a workspace through the same lifecycle the packs use:\n\n"
            "```\n  Plan  →  Execute  →  Rollback\n"
            "            (preview + approve)            (drift-safe undo)\n```"
        ),
        "zh": (
            "想直接检查受控执行链路？通过下方页面让工作区走完和成果包相同的生命周期：\n\n"
            "```\n  Plan  →  Execute  →  Rollback\n"
            "            （预览 + 确认授权）        （含漂移保护的撤销）\n```"
        ),
    },
    "home.table.header": {"en": "| Page | What it does |", "zh": "| 页面 | 作用 |"},
    "home.table.divider": {"en": "|---|---|", "zh": "|---|---|"},
    "home.table.plan": {
        "en": "| **📋 Plan** | Turn a goal into typed actions with risk assessment before any write. |",
        "zh": "| **📋 Plan** | 把目标转成结构化 action，并在写入前完成风险评估。 |",
    },
    "home.table.execute": {
        "en": "| **🔍 Execute** | Preview, approve, run through the executor, and verify automatically. |",
        "zh": "| **🔍 Execute** | 预览、确认、通过 executor 执行，并自动校验结果。 |",
    },
    "home.table.rollback": {
        "en": "| **↺ Rollback** | Preview each reverse op + drift detection. `--force` to override. |",
        "zh": "| **↺ Rollback** | 预览每一个反向操作 + 漂移检测。`--force` 可覆盖。 |",
    },
    "home.table.memory": {
        "en": "| **⚙ Settings** | Edit forbidden paths + naming style. Includes audit log. |",
        "zh": "| **⚙ 设置** | 编辑禁止路径、命名风格。含审计日志。 |",
    },
    "home.workspace.header": {"en": "### Workspace", "zh": "### 工作区"},
    "home.active_workspace": {
        "en": "Active workspace: `{path}`",
        "zh": "当前工作区：`{path}`",
    },
    "home.pick_workspace": {
        "en": "Pick a workspace in the sidebar to begin.",
        "zh": "请先在左侧栏挑选一个工作区。",
    },
    "home.metric.files": {"en": "Files", "zh": "文件数"},
    "home.metric.size": {"en": "Total size", "zh": "总大小"},
    "home.footer": {
        "en": (
            "Driver layer — same backend as CLI + MCP. "
            "Every action passes through `policy_guard`, `dry_run`, "
            "approval, executor, verifier, rollback, audit."
        ),
        "zh": (
            "Driver 层 — 与 CLI / MCP 共用同一套内核。"
            "每一个 action 都会依次经过 `policy_guard`、`dry_run`、"
            "approval、executor、verifier、rollback、audit。"
        ),
    },
    # ───────────────────────── plan page ─────────────────────────
    "plan.subtitle": {
        "en": "Describe what you want — the agent decomposes and plans end-to-end.",
        "zh": "用一句话描述你想做的事 — agent 自动拆解并一次性出 plan。",
    },
    "plan.goal.label": {
        "en": "What do you want to do? / 你想做什么？",
        "zh": "What do you want to do? / 你想做什么？",
    },
    "plan.goal.placeholder": {
        "en": "e.g. organize by file type / 按文件类型整理",
        "zh": "如：按文件类型整理 / organize by file type",
    },
    "plan.goal.empty_hint": {
        "en": "_Start typing your goal above to see which capability LocalFlow will use._",
        "zh": "_先在上方输入目标，LocalFlow 会告诉你将使用哪种能力。_",
    },
    "plan.autodetect.label": {
        "en": "ℹ️ **Auto-detected** · skill=`{skill}` · planner=`{planner}`",
        "zh": "ℹ️ **自动识别** · 能力=`{skill}` · 规划方式=`{planner}`",
    },
    "plan.autodetect.reason": {
        "en": "Reason — {skill_reason} · {planner_reason}",
        "zh": "理由 — {skill_reason} · {planner_reason}",
    },
    "plan.gap.title": {
        "en": "⚠️ Capability gap detected",
        "zh": "⚠️ 检测到能力缺口",
    },
    "plan.gap.suggest_skill": {
        "en": "Suggested skill for the missing part: `{skill}`",
        "zh": "覆盖剩余部分的建议能力：`{skill}`",
    },
    "plan.gap.next_steps": {
        "en": (
            "Plan will still run, but won't cover every part of your goal. "
            "Either pick the suggested skill via **▶ Override (advanced)** "
            "below, or run a second task afterward."
        ),
        "zh": (
            "Plan 仍会运行，但不会覆盖 goal 的全部步骤。"
            "可以从下方 **▶ 高级覆盖** 切换到建议的 skill，"
            "或者先跑完这个 task 再起一个新 task 补齐缺失的部分。"
        ),
    },
    "plan.override.expander": {
        "en": "▶ Override (advanced) — pick skill / planner manually",
        "zh": "▶ 高级覆盖（手动选择能力 / 规划方式）",
    },
    "plan.override.skill": {"en": "Capability", "zh": "能力"},
    "plan.override.planner": {"en": "How to plan", "zh": "规划方式"},
    "plan.override.planner_help": {
        "en": (
            "`rule` is deterministic + instant. `llm` is slower but understands semantic goals."
        ),
        "zh": (
            "`rule` 规则引擎：确定性 + 秒级返回。`llm` 大模型：慢一些（约 20s）但理解语义目标。"
        ),
    },
    "plan.override.skill_help": {
        "en": "Override the auto-detected skill.",
        "zh": "覆盖自动识别的能力。",
    },
    "plan.button.create": {"en": "📋 Create plan", "zh": "📋 生成计划"},
    "plan.error.empty_goal": {
        "en": "Please describe a goal.",
        "zh": "请先描述你的目标。",
    },
    "plan.error.llm_unsupported": {
        "en": "Skill `{skill}` does not support the LLM planner. Use `rule` or pick another skill.",
        "zh": "能力 `{skill}` 不支持 LLM 规划方式。请改用 `rule`，或换一个能力。",
    },
    "plan.error.planning_failed": {
        "en": "Planning failed: {err_type}: {err}",
        "zh": "规划失败：{err_type}: {err}",
    },
    "plan.info.prefs_applied": {
        "en": "Applied preferences from memory: {summary}",
        "zh": "已应用偏好记忆：{summary}",
    },
    "plan.spinner.scanning": {
        "en": "Scanning workspace...",
        "zh": "正在扫描工作区…",
    },
    "plan.spinner.llm": {
        "en": "LLM planning (this may take ~20s)...",
        "zh": "LLM 正在规划（约 20 秒）…",
    },
    "plan.success.created": {
        "en": "✅ Task `{task_id}` created.",
        "zh": "✅ Task `{task_id}` 已创建。",
    },
    "plan.button.goto_execute": {
        "en": "🔍 Continue to Execute →",
        "zh": "🔍 继续 → 执行 →",
    },
    "plan.caption.goto_execute": {
        "en": "Or pick **🔍 Execute** in the left sidebar.",
        "zh": "也可以在左侧栏点 **🔍 Execute**。",
    },
    "plan.summary.title": {
        "en": "Plan `{plan_id}`",
        "zh": "Plan `{plan_id}`",
    },
    "plan.summary.metric.actions": {"en": "Actions", "zh": "动作数"},
    "plan.summary.metric.files": {"en": "Files scanned", "zh": "扫描文件数"},
    "plan.summary.metric.risk": {"en": "Risk", "zh": "风险"},
    "plan.summary.metric.outputs": {"en": "Outputs", "zh": "预期产物"},
    "plan.summary.warnings_expander": {
        "en": "⚠️ {n} warning(s)",
        "zh": "⚠️ {n} 条警告",
    },
    "plan.summary.no_actions": {
        "en": "Plan has 0 actions (workspace already organized?).",
        "zh": "Plan 没有任何 action（工作区可能已经是整齐状态？）。",
    },
    "plan.summary.col.idx": {"en": "#", "zh": "#"},
    "plan.summary.col.type": {"en": "type", "zh": "类型"},
    "plan.summary.col.path": {"en": "source → target", "zh": "源 → 目标"},
    "plan.summary.col.risk": {"en": "risk", "zh": "风险"},
    "plan.summary.col.will_run": {"en": "will run?", "zh": "会执行？"},
    "plan.summary.col.approval": {"en": "approval gate", "zh": "审批门槛"},
    "plan.summary.col.reason": {"en": "reason", "zh": "原因"},
    "plan.summary.approve.yes": {"en": "yes", "zh": "是"},
    "plan.summary.approve.no": {"en": "no", "zh": "否"},
    "plan.summary.gate.required": {"en": "required", "zh": "需要"},
    "plan.summary.gate.none": {"en": "no extra gate", "zh": "无额外门槛"},
    "plan.last_plan.expander": {
        "en": "Last plan: {task_id}",
        "zh": "上一次规划：{task_id}",
    },
    # ───────────────────────── Phase 11: refinement loop ─────────────
    "plan.summary.version_chip": {
        "en": "Plan v{version}",
        "zh": "Plan 第 {version} 版",
    },
    "plan.summary.version_chip_revised": {
        "en": "Plan v{version} (revised {revisions}×)",
        "zh": "Plan 第 {version} 版（已修正 {revisions} 次）",
    },
    "plan.refine.expander": {
        "en": "🔄 Not what you wanted? Refine the plan ({remaining} revision(s) left)",
        "zh": "🔄 计划不符合预期？补充细节重新规划（剩余 {remaining} 次）",
    },
    "plan.refine.intro": {
        "en": (
            "Tell the agent what your previous plan got wrong. It will re-plan "
            "**without** executing anything — no rollback needed. You can iterate up "
            "to {max_revisions} times per task."
        ),
        "zh": (
            "告诉 agent 上一版 plan 哪里偏离了你的意图，它会**不执行**地重新生成 plan —— "
            "不需要回滚。每个任务最多迭代 {max_revisions} 次。"
        ),
    },
    "plan.refine.hint_label": {
        "en": "What was wrong / what do you actually want?",
        "zh": "上一版哪里不对 / 你实际想要什么？",
    },
    "plan.refine.hint_placeholder": {
        "en": (
            "Example: 'I wanted you to analyze the data INSIDE the Excel file, "
            "not just organize folders.' Or: 'Use a pie chart for the category "
            "proportions instead of a bar chart.'"
        ),
        "zh": (
            "例如：「我希望你分析 Excel 表里的数据，而不是整理文件夹。」"
            "或：「用饼图展示分类占比，不要柱状图。」"
        ),
    },
    "plan.refine.button": {"en": "🔁 Re-plan with this hint", "zh": "🔁 用这条提示重新规划"},
    "plan.refine.spinner": {
        "en": "Re-planning with your clarification…",
        "zh": "根据你的提示重新规划中…",
    },
    "plan.refine.error_empty": {
        "en": "Hint cannot be empty. Tell the agent what was wrong.",
        "zh": "提示不能为空。请告诉 agent 哪里不对。",
    },
    "plan.refine.error_unsupported": {
        "en": "Skill `{skill}` does not support refinement (no LLM planner).",
        "zh": "能力 `{skill}` 不支持修正（没有 LLM 规划方式）。",
    },
    "plan.refine.error_generic": {
        "en": "Refinement failed: {err}",
        "zh": "修正失败：{err}",
    },
    "plan.refine.success": {
        "en": "Refined to v{version}. Review the new plan above.",
        "zh": "已生成第 {version} 版。请查看上方的新 plan。",
    },
    "plan.refine.max_reached": {
        "en": (
            "Plan revised {max_revisions} times already — consider restarting "
            "with a clearer initial goal."
        ),
        "zh": (
            "本 task 已修正 {max_revisions} 次 —— 建议放弃这条线索，重新从一个更清晰的目标开始。"
        ),
    },
    # ───────────────────────── execute page ─────────────────────────
    "execute.subtitle": {
        "en": "Preview → review → approve → execute → check.",
        "zh": "预览 → 审阅 → 确认授权 → 执行 → 校验。",
    },
    "execute.task.label": {"en": "Task", "zh": "Task"},
    "execute.no_runs": {
        "en": "No tasks yet. Create one on the **📋 Plan** page first.",
        "zh": "还没有任何 task。请先去 **📋 Plan** 页创建一个。",
    },
    "execute.no_runs_ws": {
        "en": "No tasks for the current workspace. Create one on **📋 Plan**, or switch workspace in the sidebar.",
        "zh": "当前工作区下没有 task。请去 **📋 Plan** 创建，或在左侧栏切换工作区。",
    },
    "execute.task.missing_task": {
        "en": "No task.json for `{task_id}` — pick a valid task.",
        "zh": "`{task_id}` 没有 task.json — 请选一个有效 task。",
    },
    "execute.task.missing_plan": {
        "en": "Task `{task_id}` has no plan.json. Go to the **📋 Plan** page first.",
        "zh": "Task `{task_id}` 没有 plan.json。请先去 **📋 Plan**。",
    },
    "execute.task.done": {
        "en": "Task `{task_id}` already executed + verified.",
        "zh": "Task `{task_id}` 已执行 + 已校验。",
    },
    "execute.verifier_badge": {"en": "Check:", "zh": "校验："},
    # Phase 13 — semantic verifier panel.
    "execute.semantic.passed": {
        "en": "Semantic verifier: ✓ {summary}",
        "zh": "语义 Verifier 通过：✓ {summary}",
    },
    "execute.semantic.failed": {
        "en": "Semantic verifier rejected: {summary}",
        "zh": "语义 Verifier 拒绝：{summary}",
    },
    "execute.semantic.col.grader": {"en": "grader", "zh": "评分器"},
    "execute.semantic.col.passed": {"en": "passed", "zh": "通过"},
    "execute.semantic.col.reason": {"en": "reason", "zh": "原因"},
    "execute.semantic.col.hint": {"en": "suggested hint", "zh": "建议提示"},
    "execute.semantic.repair_repaired": {"en": "Auto-repaired", "zh": "已自动修复"},
    "execute.semantic.repair_still_failing": {
        "en": "Auto-repair attempted but still failing",
        "zh": "已尝试自动修复但仍未通过",
    },
    "execute.semantic.repair_summary": {
        "en": "{verb} {attempts}× (halt: {halt})",
        "zh": "{verb}（{attempts} 次，停止原因：{halt}）",
    },
    "execute.task.done_hint": {
        "en": "To re-run on a fresh state, create a new task from the **📋 Plan** page.",
        "zh": "如要在干净的状态下重新跑，请回到 **📋 Plan** 新建一个 task。",
    },
    "execute.done.goto_rollback": {
        "en": "↺ Open Rollback",
        "zh": "↺ 打开回滚",
    },
    "execute.done.artifacts": {
        "en": "Run artifacts",
        "zh": "运行产物",
    },
    "execute.done.trace": {
        "en": "Trace ({n} events)",
        "zh": "Trace（{n} 条事件）",
    },
    "execute.done.trace_missing": {
        "en": "No `trace.jsonl` found for this run.",
        "zh": "本次运行没有找到 `trace.jsonl`。",
    },
    "execute.stage1.header": {"en": "Stage 1 — Preview", "zh": "阶段 1 — 预览"},
    "execute.stage1.button": {"en": "🔍 Show preview", "zh": "🔍 生成预览"},
    "execute.stage1.spinner": {
        "en": "Building preview...",
        "zh": "正在生成预览…",
    },
    "execute.stage1.fail": {
        "en": "Preview failed: {err_type}: {err}",
        "zh": "预览失败：{err_type}: {err}",
    },
    "execute.stage1.risk": {"en": "**Risk:**", "zh": "**风险：**"},
    "execute.stage1.actions_to_execute": {
        "en": "Actions to execute",
        "zh": "将要执行的动作数",
    },
    "execute.stage1.warnings_expander": {
        "en": "⚠️ {n} warning(s)",
        "zh": "⚠️ {n} 条警告",
    },
    "execute.stage1.preview_expander": {
        "en": "📄 Preview (markdown)",
        "zh": "📄 预览（Markdown）",
    },
    "execute.stage1.hint": {
        "en": "Click **Show preview** above to see every planned action.",
        "zh": "点上方 **生成预览** 查看每一个计划好的动作。",
    },
    "execute.stage2.header": {"en": "Stage 2 — Approve", "zh": "阶段 2 — 确认授权"},
    "execute.stage2.blocked": {
        "en": "Policy guard blocked one or more actions (see warnings above). Execute will refuse the run.",
        "zh": "Policy guard 拦住了一个或多个动作（见上方警告）。Execute 会拒绝运行。",
    },
    "execute.stage2.checkbox": {
        "en": "I reviewed every action above and consent to commit them.",
        "zh": "我已审阅上述每个动作并同意提交。",
    },
    "execute.stage2.approved": {
        "en": "Approval recorded. Execute is now enabled.",
        "zh": "已记录授权，Execute 已解锁。",
    },
    "execute.stage3.header": {
        "en": "Stage 3 — Execute + Verify",
        "zh": "阶段 3 — Execute + Verify（执行 + 校验）",
    },
    "execute.stage3.locked": {"en": "Execute (locked)", "zh": "Execute（已锁定）"},
    "execute.stage3.locked_caption": {
        "en": "Check the approval box above to enable.",
        "zh": "请先勾选上方审批复选框。",
    },
    "execute.stage3.button": {"en": "🚀 Execute now", "zh": "🚀 立即执行"},
    "execute.stage3.token_missing": {
        "en": "Approval missing. Please re-run the preview to refresh.",
        "zh": "确认授权已失效，请重新预览一次。",
    },
    "execute.stage3.token_validate": {
        "en": "Verifying your approval...",
        "zh": "正在核对授权…",
    },
    "execute.stage3.executing": {
        "en": "Executing... (writing real changes)",
        "zh": "正在执行…（开始写入真实磁盘）",
    },
    "execute.stage3.approval_err": {
        "en": "Approval rejected: {err}",
        "zh": "审批被拒：{err}",
    },
    "execute.stage3.exec_err": {
        "en": "Execute failed: {err_type}: {err}",
        "zh": "执行失败：{err_type}: {err}",
    },
    "execute.metric.executed": {"en": "Executed", "zh": "已执行"},
    "execute.metric.failed": {"en": "Failed", "zh": "失败"},
    "execute.metric.skipped": {"en": "Skipped", "zh": "跳过"},
    "execute.metric.verifier": {"en": "Check", "zh": "校验"},
    "execute.success": {
        "en": "✅ Task `{task_id}` complete. Run recorded in `{path}`.",
        "zh": "✅ Task `{task_id}` 已完成。Run 数据写在 `{path}`。",
    },
    "execute.button.goto_rollback": {
        "en": "↺ Continue to Rollback →",
        "zh": "↺ 继续 → 回滚 →",
    },
    "execute.caption.goto_rollback": {
        "en": "Or pick **↺ Rollback** in the left sidebar to undo.",
        "zh": "也可以在左侧栏点 **↺ Rollback** 执行撤销。",
    },
    "execute.fail.verifier": {
        "en": "❌ Check failed:",
        "zh": "❌ 校验未通过：",
    },
    # ───────────────────────── rollback page ─────────────────────────
    "rollback.subtitle": {
        "en": "Replay the rollback manifest — with hash-drift guard.",
        "zh": "回放 rollback manifest — 含哈希漂移保护。",
    },
    "rollback.select.label": {"en": "Run to rollback", "zh": "要回滚的 run"},
    "rollback.no_runs": {
        "en": "No runs in this LocalFlow store yet.",
        "zh": "本机的 LocalFlow store 里还没有 run。",
    },
    "rollback.no_runs_ws": {
        "en": "No rollbackable runs for the current workspace. Execute something on the **🔍 Execute** page first.",
        "zh": "当前工作区没有可回滚的 run。请先去 **🔍 Execute** 执行一次。",
    },
    "rollback.preview.title": {
        "en": "Preview rollback for `{task_id}`",
        "zh": "预览 `{task_id}` 的回滚",
    },
    "rollback.preview.button": {"en": "🔍 Preview", "zh": "🔍 预览"},
    "rollback.preview.hint": {
        "en": "Click **Preview** to compute drift status for each rollback entry.",
        "zh": "点 **Preview** 计算每一条 rollback entry 的漂移状态。",
    },
    "rollback.preview.metric.entries": {"en": "Entries", "zh": "Entry 数"},
    "rollback.preview.state_label": {"en": "**State:**", "zh": "**状态：**"},
    "rollback.preview.warn_conflicts": {
        "en": (
            "⚠️ One or more files have been modified since execute. "
            "Safe rollback will **skip** those entries to protect your edits."
        ),
        "zh": (
            "⚠️ 有一个或多个文件在 execute 之后被改动过。"
            "Safe 回滚会 **跳过** 这些 entry 以保护你的修改。"
        ),
    },
    "rollback.table.col.action_id": {"en": "action_id", "zh": "action_id"},
    "rollback.table.col.op": {"en": "op", "zh": "操作"},
    "rollback.table.col.target": {"en": "target", "zh": "目标"},
    "rollback.table.col.status": {"en": "status", "zh": "状态"},
    "rollback.table.col.reason": {"en": "reason", "zh": "原因"},
    "rollback.table.status.clean": {"en": "✅ clean", "zh": "✅ 干净"},
    "rollback.table.status.drift": {"en": "⚠️ drift", "zh": "⚠️ 漂移"},
    "rollback.run.header": {"en": "Run rollback", "zh": "执行回滚"},
    "rollback.btn.clean": {"en": "↺ Rollback now (clean)", "zh": "↺ 立即回滚（干净）"},
    "rollback.btn.safe": {
        "en": "↺ Safe rollback (skip conflicts)",
        "zh": "↺ Safe 回滚（跳过冲突）",
    },
    "rollback.btn.force": {
        "en": "🔥 Force rollback (clobber edits)",
        "zh": "🔥 强制回滚（覆盖你的修改）",
    },
    "rollback.btn.force_confirm": {
        "en": "⚠ I accept that forcing will **overwrite my manual edits**.",
        "zh": "⚠ 我接受强制回滚将 **覆盖我手动修改的文件**。",
    },
    "rollback.spinner.clean": {"en": "Rolling back...", "zh": "回滚中…"},
    "rollback.spinner.safe": {
        "en": "Rolling back (skipping drifted entries)...",
        "zh": "回滚中（跳过漂移的 entry）…",
    },
    "rollback.spinner.force": {"en": "Force rolling back...", "zh": "强制回滚中…"},
    "rollback.metric.undone": {"en": "Undone", "zh": "已撤销"},
    "rollback.metric.failed": {"en": "Failed", "zh": "失败"},
    "rollback.metric.conflicts": {"en": "Conflicts", "zh": "冲突"},
    "rollback.metric.status": {"en": "**Status:**", "zh": "**状态：**"},
    "rollback.success": {"en": "✅ Rollback complete.", "zh": "✅ 回滚完成。"},
    "rollback.cascaded.info": {
        "en": (
            "ℹ️ **Partial rollback by design.** "
            "{n} directory cleanup(s) were not performed because "
            "they still contain files you chose to preserve (the conflict(s) "
            "above). The harness **never deletes non-empty directories** — "
            "that's the safety guarantee that kept your edits intact. "
            "To fully clean, either:\n"
            "  1. remove your manual edits, then run rollback again, or\n"
            "  2. use **🔥 Force rollback** (will overwrite the edits)."
        ),
        "zh": (
            "ℹ️ **设计上的部分回滚。** "
            "{n} 个目录清理没有执行，因为它们里面还装着"
            "你选择保留的文件（上方的 conflict）。harness **从不删除非空目录** —— "
            "这正是保住你修改的安全保证。如要彻底清理：\n"
            "  1. 先把那些手动修改的文件删掉，再回滚一次，或\n"
            "  2. 使用 **🔥 Force rollback**（会覆盖那些修改）。"
        ),
    },
    "rollback.cascaded.expander": {
        "en": "📂 Cascaded directory cleanups skipped ({n})",
        "zh": "📂 因冲突而连带跳过的目录清理（{n}）",
    },
    "rollback.real_failures.expander": {
        "en": "❌ Real failures ({n})",
        "zh": "❌ 真正的失败（{n}）",
    },
    "rollback.conflicts.expander": {
        "en": "⚠️ Conflicts skipped ({n})",
        "zh": "⚠️ 已跳过的冲突（{n}）",
    },
    # ───────────────────────── memory page ─────────────────────────
    "memory.subtitle": {
        "en": "Persistent user preferences. Every mutation is audited.",
        "zh": "持久化的用户偏好。每一次修改都会被记录。",
    },
    "memory.tab.forbidden": {"en": "🚫 Forbidden paths", "zh": "🚫 禁止路径"},
    "memory.tab.naming": {"en": "📝 Naming style", "zh": "📝 命名风格"},
    "memory.tab.planner": {"en": "🤖 Planner preference", "zh": "🤖 Planner 偏好"},
    "memory.tab.semantic": {"en": "🔁 Semantic + Repair", "zh": "🔁 语义 + 自动修复"},
    "memory.tab.audit": {"en": "📜 Audit log", "zh": "📜 审计日志"},
    "memory.semantic.header": {
        "en": "Semantic verifier + auto-repair (v0.13)",
        "zh": "语义 Verifier + 自动修复（v0.13）",
    },
    "memory.semantic.caption": {
        "en": (
            "After execute + structural verify, run LLM-as-judge graders to "
            "catch semantic failures (empty analyses, generic boilerplate, "
            "hallucinated chart counts). On rejection, automatically rollback "
            "+ revise + re-execute up to max_auto_repairs times."
        ),
        "zh": (
            "执行 + 结构性 verify 之后再跑 LLM-as-judge 语义评分器，捕捉"
            "结构性 verifier 抓不到的语义失败（空分析、空泛模板、瞎编的图表数据）。"
            "拒绝后自动回滚 + 修正 + 重新执行，最多 max_auto_repairs 次。"
        ),
    },
    "memory.semantic.enable_toggle": {
        "en": "Enable semantic verifier",
        "zh": "启用语义 Verifier",
    },
    "memory.semantic.enable_tradeoff": {
        "en": (
            "Trade-off: adds 1+ LLM calls per execute (one per registered "
            "semantic grader). Off by default — opt-in when you want the "
            "harness to self-correct, off when you want deterministic, "
            "cost-bounded runs."
        ),
        "zh": (
            "代价：每次执行多 1+ 次 LLM 调用（每个语义评分器各一次）。"
            "默认关闭——希望 harness 自我纠错时再启用，要纯确定性、"
            "可控成本时保持关闭。"
        ),
    },
    "memory.semantic.enable_saved_on": {
        "en": "Saved. Future `localflow execute` runs will run semantic graders.",
        "zh": "已保存。后续 `localflow execute` 会跑语义评分器。",
    },
    "memory.semantic.enable_saved_off": {
        "en": "Saved. Semantic verifier disabled.",
        "zh": "已保存。语义 Verifier 已关闭。",
    },
    "memory.semantic.max_label": {
        "en": "Max auto-repair attempts",
        "zh": "自动修复最大次数",
    },
    "memory.semantic.max_slider": {
        "en": "Cap on how many rollback → revise → re-execute cycles run per task",
        "zh": "每个任务允许多少次「回滚 → 修正 → 重新执行」循环",
    },
    "memory.semantic.max_help": {
        "en": "0 = report-only (verdicts shown, but no repair). 5 = upper limit, mirrors MAX_REVISIONS.",
        "zh": "0 = 只报告不修复（显示评分但不重试）。5 = 上限，与 MAX_REVISIONS 一致。",
    },
    "memory.semantic.max_save": {
        "en": "Save: {old} → {new}",
        "zh": "保存：{old} → {new}",
    },
    "memory.planner.header": {
        "en": "Prefer LLM by default",
        "zh": "默认偏好 LLM planner",
    },
    "memory.planner.caption": {
        "en": (
            "When ON, every LLM-capable skill (folder_organizer, data_analyzer) "
            "uses the LLM planner by default — even for simple goals. When OFF, "
            "LocalFlow uses the rule planner unless the goal has semantic intent "
            "or multiple steps. Defaults to OFF."
        ),
        "zh": (
            "开启后，所有支持 LLM 的 skill（folder_organizer / data_analyzer）"
            "**默认都用 LLM**，即使是简单 goal。关闭时（默认），LocalFlow "
            "仅在 goal 含语义意图或多步骤连接词时升级到 LLM。**默认关闭**。"
        ),
    },
    "memory.planner.toggle": {
        "en": "Prefer LLM planner",
        "zh": "默认使用 LLM planner",
    },
    "memory.planner.tradeoff": {
        "en": (
            "Trade-off: LLM planner takes ~20 s + consumes API quota; rule "
            'planner is instant + free. For simple goals ("organize by type") '
            "rule is usually enough."
        ),
        "zh": (
            "权衡：LLM 慢 (~20 秒) + 烧 API 配额；rule 即时 + 免费。"
            "简单 goal（如「按类型整理」）通常 rule 就够了。"
        ),
    },
    "memory.planner.saved_on": {
        "en": "✅ prefer_llm_planner enabled. Auto-detect will now default to LLM.",
        "zh": "✅ 已开启 LLM 优先。Auto-detect 现在默认走 LLM。",
    },
    "memory.planner.saved_off": {
        "en": "✅ prefer_llm_planner disabled. Auto-detect falls back to smart upgrade.",
        "zh": "✅ 已关闭 LLM 优先。Auto-detect 回到智能升级模式。",
    },
    "memory.error.store": {
        "en": "Memory store error: {err}",
        "zh": "偏好读取错误：{err}",
    },
    "memory.forbidden.header": {
        "en": "Forbidden paths (kernel-enforced)",
        "zh": "Forbidden paths（由 kernel 强制执行）",
    },
    "memory.forbidden.caption": {
        "en": (
            "Workspace-relative paths the kernel refuses to touch. "
            "Applies to every Skill, every driver (CLI / MCP / UI)."
        ),
        "zh": (
            "kernel 拒绝触碰的、工作区相对路径。"
            "对所有 Skill / 所有 driver（CLI / MCP / UI）都生效。"
        ),
    },
    "memory.forbidden.empty": {
        "en": "No forbidden paths set. The kernel won't refuse any path on those grounds.",
        "zh": "尚未设置任何禁止路径。kernel 不会因为这个规则拒绝任何路径。",
    },
    "memory.forbidden.add_label": {"en": "Add a path", "zh": "新增一条路径"},
    "memory.forbidden.add_placeholder": {
        "en": "e.g. private/secrets",
        "zh": "如 private/secrets",
    },
    "memory.forbidden.add_help": {
        "en": "Workspace-relative. Absolute paths and `..` traversal are rejected.",
        "zh": "工作区相对路径。绝对路径与 `..` 越界路径都会被拒绝。",
    },
    "memory.forbidden.add_button": {"en": "➕ Forbid", "zh": "➕ 加入禁止"},
    "memory.forbidden.removed": {
        "en": "Removed `{path}`",
        "zh": "已移除 `{path}`",
    },
    "memory.forbidden.added": {
        "en": "Added `{path}` to forbidden_paths.",
        "zh": "已将 `{path}` 加入 forbidden_paths。",
    },
    "memory.forbidden.already": {
        "en": "`{path}` was already in forbidden_paths.",
        "zh": "`{path}` 已经在 forbidden_paths 里。",
    },
    "memory.forbidden.empty_input": {
        "en": "Type a path first.",
        "zh": "请先输入路径。",
    },
    "memory.forbidden.remove_help": {
        "en": "Unforbid {path}",
        "zh": "解除 {path} 的禁止",
    },
    "memory.naming.header": {"en": "Naming style", "zh": "命名风格"},
    "memory.naming.caption": {
        "en": (
            "Read by `folder_organizer` when renaming files. "
            "Applies to move targets in the planned ActionPlan."
        ),
        "zh": ("`folder_organizer` 在改名时会读这个设置。应用于 ActionPlan 中的 move 目标路径。"),
    },
    "memory.naming.style_label": {"en": "Style", "zh": "风格"},
    "memory.naming.style_help": {
        "en": "`original` = no transform. Otherwise stem-only transform; extension preserved.",
        "zh": "`original` = 不变换。其余仅作用于主文件名（扩展名保持原样）。",
    },
    "memory.naming.save_button": {
        "en": "Save: {old} → {new}",
        "zh": "保存：{old} → {new}",
    },
    "memory.naming.reset_button": {
        "en": "Reset to default (original)",
        "zh": "重置为默认（original）",
    },
    "memory.naming.examples_expander": {
        "en": "Example transformations",
        "zh": "示例变换",
    },
    "memory.naming.col_original": {"en": "original", "zh": "原文件名"},
    "memory.audit.header": {"en": "Audit log", "zh": "审计日志"},
    "memory.audit.caption": {
        "en": (
            "Every memory mutation (forbid / unforbid / set / unset) writes a "
            "row here. JSONL on disk at `{path}`."
        ),
        "zh": (
            "每一次偏好修改（forbid / unforbid / set / unset）都会写一行到这里。"
            "JSONL 持久化在 `{path}`。"
        ),
    },
    "memory.audit.slider": {
        "en": "Show recent N entries",
        "zh": "显示最近 N 条",
    },
    "memory.audit.empty": {
        "en": "No mutations recorded yet.",
        "zh": "暂无记录。",
    },
    "memory.audit.col_ts": {"en": "timestamp", "zh": "时间"},
    "memory.audit.col_event": {"en": "event", "zh": "事件"},
    "memory.audit.col_key": {"en": "path/key", "zh": "路径 / 字段"},
    "memory.audit.col_before": {"en": "before", "zh": "改前"},
    "memory.audit.col_after": {"en": "after", "zh": "改后"},
    # ───────────────────────── shared status badges ─────────────────────────
    "common.workspace_warning": {
        "en": "👈 Pick a workspace in the sidebar first. (Subdirectories of `./sandbox/`.)",
        "zh": "👈 请先在左侧栏选择一个工作区。（`./sandbox/` 下的子目录。）",
    },
    "common.status.passed": {"en": "PASSED", "zh": "通过"},
    "common.status.failed": {"en": "FAILED", "zh": "失败"},
    "common.status.partial": {"en": "PARTIAL", "zh": "部分完成"},
    "common.status.clean": {"en": "CLEAN", "zh": "干净"},
    "common.status.conflicts": {"en": "CONFLICTS", "zh": "冲突"},
    # ───────────────────────── pack page (Phase 17 + 18 + 19) ─────────────────────────
    "pack.heading": {
        "en": "📦 Harness demo packs",
        "zh": "📦 Harness 示例成果包",
    },
    "pack.subtitle": {
        "en": (
            "Pick a ready-made workflow that exercises the safe TaskGraph path: "
            "plan, preview, execute, verify, repair, trace, and rollback."
        ),
        "zh": (
            "选择一个现成工作流，完整体验安全 TaskGraph 链路：规划、预览、"
            "执行、校验、修复、追踪和回退。"
        ),
    },
    "pack.load_errors_title": {
        "en": "⚠️ {n} recipe load error(s)",
        "zh": "⚠️ {n} 个交付包 YAML 加载错误",
    },
    "pack.no_recipes_loaded": {
        "en": "No recipes loaded. Check that the `recipes/` directory exists at `{path}`.",
        "zh": "没有可用的交付包。请检查 `recipes/` 目录是否存在：`{path}`。",
    },
    # ── Goal Interpreter block (Phase 18)
    "pack.goal.expander_title": {
        "en": "🎯 Interpret a goal (Phase 18)",
        "zh": "🎯 解释你的目标 (Phase 18)",
    },
    "pack.goal.description": {
        "en": (
            "Type what you want; the Goal Interpreter picks a deliverable pack "
            "— using the LLM for clarifying questions when your goal is ambiguous."
        ),
        "zh": (
            "告诉我你想做什么，目标解释器会为你挑一个交付包 — "
            "目标含糊时会调用 LLM 反问你 1~3 个澄清问题。"
        ),
    },
    "pack.goal.input_label": {"en": "Goal", "zh": "目标"},
    "pack.goal.input_placeholder": {
        "en": "e.g. 'organize my research papers' / '整理我的研究资料'",
        "zh": "例如：'整理我的研究资料' / 'organize my research papers'",
    },
    "pack.goal.use_llm_label": {
        "en": "Use LLM for clarifying questions",
        "zh": "目标模糊时让 LLM 提澄清问题",
    },
    "pack.goal.use_llm_help": {
        "en": (
            "Off = router-only (deterministic; degrades to best router pick on "
            "low confidence). On = LLM may ask up to 3 short clarifying questions "
            "before committing."
        ),
        "zh": (
            "关闭 = 仅用确定性路由器（低置信度时也会兜底返回最高分的交付包）。"
            "开启 = LLM 最多反问 3 个澄清问题，再决定选哪个交付包。"
        ),
    },
    "pack.goal.button_interpret": {"en": "Interpret", "zh": "解释"},
    "pack.goal.scanning_workspace": {"en": "Scanning workspace…", "zh": "正在扫描工作区…"},
    "pack.goal.no_llm_fallback": {
        "en": "No LLM client available; falling back to router-only.",
        "zh": "未找到可用的 LLM 客户端，已退回到纯路由器模式。",
    },
    "pack.goal.suggested": {
        "en": "**Suggested pack:** `{name}`  ·  source={source}\n\n{rationale}",
        "zh": "**推荐交付包：** `{name}`  ·  来源：{source}\n\n{rationale}",
    },
    "pack.goal.run_button": {"en": "▶ Run {title}", "zh": "▶ 运行 {title}"},
    "pack.goal.need_clarification": {
        "en": "**Need clarification** ({source}). {rationale}",
        "zh": "**需要进一步澄清**（{source}）。{rationale}",
    },
    "pack.goal.answer_label": {"en": "Your answer", "zh": "你的回答"},
    "pack.goal.answer_placeholder": {
        "en": "Type your answer and press Enter…",
        "zh": "在这里输入你的回答，回车提交…",
    },
    "pack.goal.submit_clarify": {"en": "Submit clarification", "zh": "提交澄清"},
    "pack.goal.router_audit_title": {
        "en": "Router ranking (audit)",
        "zh": "路由器评分（审计明细）",
    },
    "pack.goal.audit_col_rank": {"en": "rank", "zh": "排名"},
    "pack.goal.audit_col_recipe": {"en": "recipe", "zh": "交付包"},
    "pack.goal.audit_col_score": {"en": "score", "zh": "得分"},
    "pack.goal.audit_col_why": {"en": "why", "zh": "原因"},
    "pack.goal.audit_no_signals": {"en": "(no signals)", "zh": "（无匹配信号）"},
    # ── Pack cards (Phase 17)
    "pack.cards.heading": {"en": "### Available packs", "zh": "### 可用的交付包"},
    "pack.cards.stats": {
        "en": "**Stages:** {stages}  ·  **Deliverables:** {outputs}  ·  **Tags:** {tags}",
        "zh": "**阶段数：** {stages}  ·  **交付物：** {outputs}  ·  **标签：** {tags}",
    },
    "pack.cards.tags_none": {"en": "—", "zh": "—"},
    "pack.cards.stages_label": {"en": "**Stages:**", "zh": "**阶段：**"},
    "pack.cards.stage_line": {
        "en": "  {idx}. {badge} **{title}**",
        "zh": "  {idx}. {badge} **{title}**",
    },
    "pack.cards.expected_outputs_popover": {
        "en": "Expected deliverables",
        "zh": "预期交付物",
    },
    "pack.cards.verifiers_popover": {
        "en": "Verifiers ({n})",
        "zh": "校验器（{n}）",
    },
    "pack.cards.verifiers_none": {
        "en": "_(no recipe-level verifiers)_",
        "zh": "_(未配置交付包级校验器)_",
    },
    "pack.cards.repair_map_label": {
        "en": "**Repair routing:**",
        "zh": "**修复路由：**",
    },
    "pack.cards.repair_map_line": {
        "en": "  · `{verifier}` → replays `{stage_id}`",
        "zh": "  · `{verifier}` → 重放 `{stage_id}`",
    },
    "pack.cards.repair_map_default": {
        "en": "  _(other verifiers default to the last LLM stage)_",
        "zh": "  _(其他校验器默认重放最后一个 LLM 阶段)_",
    },
    "pack.cards.enable_repair_label": {"en": "Enable repair", "zh": "开启自动修复"},
    "pack.cards.enable_repair_help": {
        "en": (
            "Promote ABORT stages to REPAIR. Requires the semantic verifier "
            "preference to actually trigger."
        ),
        "zh": (
            "把 ABORT 策略的阶段提升为 REPAIR（自动修复）。还需要在偏好里开启"
            "语义校验器才会真正触发。"
        ),
    },
    "pack.cards.run_button": {"en": "▶ Run {title}", "zh": "▶ 运行 {title}"},
    # ── Pack preview + approval
    "pack.preview.heading": {
        "en": "### Preview pack: {title}",
        "zh": "### 预览交付包：{title}",
    },
    "pack.preview.caption": {
        "en": "Review the TaskGraph contract before LocalFlow writes to the workspace.",
        "zh": "在 LocalFlow 写入工作区前，先审阅 TaskGraph 合约。",
    },
    "pack.preview.failed": {
        "en": "Pack preview failed: {err_type}: {err}",
        "zh": "交付包预览失败：{err_type}：{err}",
    },
    "pack.preview.col_stage": {"en": "stage", "zh": "阶段"},
    "pack.preview.col_title": {"en": "title", "zh": "标题"},
    "pack.preview.col_skill": {"en": "capability", "zh": "能力"},
    "pack.preview.col_planner": {"en": "planner", "zh": "规划器"},
    "pack.preview.col_failure": {"en": "failure policy", "zh": "失败策略"},
    "pack.preview.col_outputs": {"en": "outputs", "zh": "产物数"},
    "pack.preview.outputs": {
        "en": "Expected deliverables",
        "zh": "预期交付物",
    },
    "pack.preview.verifiers": {
        "en": "Verifiers ({n})",
        "zh": "校验器（{n}）",
    },
    "pack.preview.checkbox": {
        "en": "I reviewed this TaskGraph and approve running the pack.",
        "zh": "我已审阅这个 TaskGraph，并确认运行该交付包。",
    },
    "pack.preview.run_button": {
        "en": "🚀 Run approved pack",
        "zh": "🚀 运行已确认的交付包",
    },
    "pack.preview.cancel_button": {"en": "Cancel", "zh": "取消"},
    # ── Pack execution + result (Phase 17)
    "pack.exec.running": {
        "en": "Running pack `{name}` ({stages} stages)…",
        "zh": "正在执行交付包 `{name}`（共 {stages} 个阶段）…",
    },
    "pack.exec.failed": {
        "en": "Pack run failed: {err_type}: {err}",
        "zh": "交付包执行失败：{err_type}：{err}",
    },
    "pack.result.heading": {
        "en": "### {badge} Last pack run: `{name}`  ({ms} ms)",
        "zh": "### {badge} 上次交付包运行：`{name}`（{ms} 毫秒）",
    },
    "pack.result.run_id": {"en": "Run ID: `{run_id}`", "zh": "运行 ID：`{run_id}`"},
    "pack.result.col_stage": {"en": "stage", "zh": "阶段"},
    "pack.result.col_status": {"en": "status", "zh": "状态"},
    "pack.result.col_actions": {"en": "actions", "zh": "动作数"},
    "pack.result.col_verifier": {"en": "verifier", "zh": "校验"},
    "pack.result.col_ms": {"en": "ms", "zh": "毫秒"},
    "pack.result.rollback_hint": {
        "en": (
            "To undo this pack run: `localflow rollback --run-id {run_id}`  "
            "(or use the Rollback page in the sidebar)."
        ),
        "zh": (
            "如需撤销本次运行：`localflow rollback --run-id {run_id}`  "
            "（或使用左侧栏的「回滚」页）。"
        ),
    },
    # ── Recipe-level verifier table (Phase 21.1 — UI parity with CLI)
    "pack.result.verifier_heading": {
        "en": "#### Recipe verifiers",
        "zh": "#### 交付包级校验器",
    },
    "pack.result.verifier_passed": {
        "en": "✅ All recipe verifiers passed.",
        "zh": "✅ 所有交付包级校验器均通过。",
    },
    "pack.result.verifier_failed": {
        "en": "❌ One or more recipe verifiers failed.",
        "zh": "❌ 有一个或多个交付包级校验器未通过。",
    },
    "pack.result.col_verifier_name": {"en": "verifier", "zh": "校验器"},
    "pack.result.col_verifier_status": {"en": "status", "zh": "状态"},
    "pack.result.col_verifier_detail": {"en": "detail", "zh": "明细"},
    "pack.result.col_verifier_hint": {"en": "suggested hint", "zh": "建议提示"},
    "pack.result.verifier_status_passed": {"en": "✅ passed", "zh": "✅ 通过"},
    "pack.result.verifier_status_failed": {"en": "❌ failed", "zh": "❌ 失败"},
    "pack.result.verifier_status_skipped": {"en": "⏭ skipped", "zh": "⏭ 跳过"},
    # ── Recipe-level repair attempts table (Phase 21.1)
    "pack.result.repair_heading": {
        "en": "#### Auto-repair attempts",
        "zh": "#### 自动修复过程",
    },
    "pack.result.repair_summary": {
        "en": ("Rounds used: **{rounds}**  ·  Halted because: `{halt}`  ·  {verb}"),
        "zh": ("已用轮数：**{rounds}**  ·  停止原因：`{halt}`  ·  {verb}"),
    },
    "pack.result.repair_verb_repaired": {
        "en": "✅ Repaired",
        "zh": "✅ 已修复",
    },
    "pack.result.repair_verb_still_failing": {
        "en": "❌ Still failing",
        "zh": "❌ 仍未通过",
    },
    "pack.result.col_repair_attempt": {"en": "#", "zh": "#"},
    "pack.result.col_repair_verifier": {"en": "verifier", "zh": "校验器"},
    "pack.result.col_repair_target": {"en": "replays stage", "zh": "重放阶段"},
    "pack.result.col_repair_hint": {"en": "hint", "zh": "提示"},
    "pack.result.col_repair_passed": {"en": "outcome", "zh": "结果"},
    "pack.result.col_repair_ms": {"en": "ms", "zh": "毫秒"},
    "pack.result.repair_outcome_passed": {"en": "✅ passed", "zh": "✅ 通过"},
    "pack.result.repair_outcome_failed": {"en": "❌ still failing", "zh": "❌ 仍未通过"},
    "pack.result.repair_outcome_error": {"en": "💥 error: {err}", "zh": "💥 错误：{err}"},
    "pack.exec.verifier_exception": {
        "en": "Recipe verifier raised: {err_type}: {err}",
        "zh": "交付包级校验器抛出异常：{err_type}：{err}",
    },
    # ── Goal Interpreter rationale (Phase 18 — router-only branches)
    "goal_interp.rationale.router_confident": {
        "en": (
            "Router scored {name} at {score} (margin {margin} over next "
            "candidate); deterministic pick."
        ),
        "zh": ("路由器给 {name} 评 {score} 分（领先第二名 {margin} 分）；确定性挑选。"),
    },
    "goal_interp.rationale.no_llm_router_pick": {
        "en": (
            "No LLM available; falling back to router top pick ({name}, score "
            "{score}). Confidence is low — consider re-running with a clearer "
            "goal."
        ),
        "zh": (
            "未找到可用的 LLM 客户端，已退回到路由器最高分选项（{name}，得分 "
            "{score}）。置信度偏低 — 建议用更明确的目标重试。"
        ),
    },
    "goal_interp.rationale.no_llm_clarify": {
        "en": ("No LLM available and router has no positive-scoring recipe."),
        "zh": ("未找到可用的 LLM 客户端，且路由器没有正向得分的交付包。"),
    },
    "goal_interp.rationale.no_recipes_loaded": {
        "en": ("No recipes are loaded; ask the user to install or configure recipes."),
        "zh": "尚未加载任何交付包；请先在 recipes/ 目录或环境变量里配置。",
    },
    "goal_interp.rationale.llm_failed_pick": {
        "en": (
            "LLM call failed ({err}); falling back to router. Top pick: {name} (score {score})."
        ),
        "zh": ("LLM 调用失败（{err}），已退回到路由器。最高分：{name}（得分 {score}）。"),
    },
    "goal_interp.rationale.llm_failed_clarify": {
        "en": ("LLM call failed ({err}); router has no positive-scoring recipe either."),
        "zh": ("LLM 调用失败（{err}），且路由器也没有正向得分的交付包。"),
    },
    "goal_interp.rationale.llm_invalid_envelope": {
        "en": (
            "LLM returned an invalid envelope ({err}); falling back to router top pick ({name})."
        ),
        "zh": ("LLM 返回了无效的结构（{err}），已退回到路由器最高分（{name}）。"),
    },
    "goal_interp.rationale.llm_unknown_recipe": {
        "en": ("LLM picked unknown recipe '{ghost}'; router fallback to {name}."),
        "zh": ("LLM 选了一个不存在的交付包 '{ghost}'，已退回到路由器选项 {name}。"),
    },
}


def t(key: str, **kwargs) -> str:
    """Translate ``key`` to the current session language.

    Falls back: requested lang → English → ``!!key!!`` sentinel so a
    missing translation is unmistakable on screen during development.

    Streamlit is imported lazily so ``t()`` works in unit tests without
    a Streamlit runtime — in that case it always returns the
    ``DEFAULT_LANG`` text.
    """
    lang: Lang = DEFAULT_LANG
    try:
        import streamlit as st

        lang = st.session_state.get(SESSION_LANG_KEY, DEFAULT_LANG)  # type: ignore[assignment]
    except Exception:
        # Streamlit not available or no script run context (tests). Stay
        # in the default language; this is the documented behaviour.
        pass

    entry = _DICT.get(key, {})
    text = entry.get(lang) or entry.get("en") or f"!!{key}!!"
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def current_lang() -> Lang:
    """Return the active language for the current session, or
    ``DEFAULT_LANG`` if Streamlit isn't available."""
    try:
        import streamlit as st

        return st.session_state.get(SESSION_LANG_KEY, DEFAULT_LANG)  # type: ignore[return-value]
    except Exception:
        return DEFAULT_LANG


def current_locale() -> str:
    """v0.22 — map the UI's short language code (``en`` / ``zh``) to the
    BCP-47-style :class:`app.schemas.task.Locale` (``en-US`` / ``zh-CN``)
    used by TaskSpec / TaskGraph for generated-content language. Callers
    pass the result into ``compile_to_taskgraph(locale=...)`` so the LLM
    produces README / SOURCES / verifier rationales in the user's
    language."""
    return {"en": "en-US", "zh": "zh-CN"}.get(current_lang(), "zh-CN")


def render_language_toggle() -> None:
    """Sidebar widget for switching between English and Chinese.

    Streamlit reruns automatically on widget change. The new value is
    persisted in ``st.session_state[SESSION_LANG_KEY]``.
    """
    import streamlit as st

    current = st.session_state.get(SESSION_LANG_KEY, DEFAULT_LANG)
    options: list[Lang] = ["en", "zh"]
    label_map = {
        "en": t("sidebar.language.en"),
        "zh": t("sidebar.language.zh"),
    }
    new = st.radio(
        t("sidebar.language.label"),
        options=options,
        index=options.index(current),
        format_func=lambda code: label_map[code],
        horizontal=True,
        key="ui_lang_radio",
    )
    if new != current:
        st.session_state[SESSION_LANG_KEY] = new
        st.rerun()


def all_keys() -> list[str]:
    """Return every key in the dictionary — used by tests + tooling."""
    return list(_DICT.keys())


def get_dict() -> dict[str, dict[Lang, str]]:
    """Expose the underlying dict for tests. Do not mutate."""
    return _DICT
