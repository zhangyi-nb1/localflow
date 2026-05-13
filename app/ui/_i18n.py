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
        "en": "Safe execution harness for LLM agents on local workspaces.",
        "zh": "面向本地工作区的 LLM 智能体安全执行框架。",
    },
    "app.page_title.home": {"en": "Home", "zh": "首页"},
    "app.page_title.plan": {"en": "Plan", "zh": "规划"},
    "app.page_title.execute": {"en": "Execute", "zh": "执行"},
    "app.page_title.rollback": {"en": "Rollback", "zh": "回滚"},
    "app.page_title.memory": {"en": "Memory", "zh": "偏好记忆"},
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
    # ───────────────────────── home page ─────────────────────────
    "home.intro": {
        "en": (
            "The LLM proposes; the harness disposes. Use the sidebar pages "
            "to walk a workspace through the lifecycle:\n\n"
            "```\n  Plan  →  Execute  →  Rollback\n"
            "            (with dry-run + approval)   (with hash-drift guard)\n```"
        ),
        "zh": (
            "AI 出方案，harness 决定能不能动。通过左侧栏的页面让一个 workspace "
            "走完完整生命周期：\n\n"
            "```\n  Plan  →  Execute  →  Rollback\n"
            "            （含 dry-run + 审批）         （含哈希漂移保护）\n```"
        ),
    },
    "home.table.header": {"en": "| Page | What it does |", "zh": "| 页面 | 作用 |"},
    "home.table.divider": {"en": "|---|---|", "zh": "|---|---|"},
    "home.table.plan": {
        "en": "| **📋 Plan** | Write a goal — LocalFlow picks the skill + planner. |",
        "zh": "| **📋 Plan** | 写下你的目标 — LocalFlow 自动挑选 skill 与 planner。 |",
    },
    "home.table.execute": {
        "en": "| **🔍 Execute** | Render dry-run, approve, commit. Verifier runs automatically. |",
        "zh": "| **🔍 Execute** | 渲染预演、审批、执行。Verifier 会自动运行。 |",
    },
    "home.table.rollback": {
        "en": "| **↺ Rollback** | Preview each reverse op + drift detection. `--force` to override. |",
        "zh": "| **↺ Rollback** | 预览每一个反向操作 + 漂移检测。`--force` 可覆盖。 |",
    },
    "home.table.memory": {
        "en": "| **⚙ Memory** | Edit `forbidden_paths` + `naming_style`. Audit log. |",
        "zh": "| **⚙ Memory** | 编辑 `forbidden_paths` 与 `naming_style`。含审计日志。 |",
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
        "en": "Describe what you want — LocalFlow picks the skill + planner.",
        "zh": "用一句话描述你想做的事 — LocalFlow 自动选择 skill 与 planner。",
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
        "en": "_Start typing your goal above to see what skill + planner LocalFlow will use._",
        "zh": "_先在上方输入目标，LocalFlow 会自动告诉你将使用哪个 skill + planner。_",
    },
    "plan.autodetect.label": {
        "en": "ℹ️ **Auto-detected** · skill=`{skill}` · planner=`{planner}`",
        "zh": "ℹ️ **自动识别** · skill=`{skill}` · planner=`{planner}`",
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
        "zh": "覆盖剩余部分的建议 skill：`{skill}`",
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
        "zh": "▶ 高级覆盖（手动选择 skill / planner）",
    },
    "plan.override.skill": {"en": "Skill", "zh": "Skill（技能）"},
    "plan.override.planner": {"en": "Planner", "zh": "Planner（规划方式）"},
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
        "zh": "覆盖自动识别的 skill。",
    },
    "plan.button.create": {"en": "📋 Create plan", "zh": "📋 生成计划"},
    "plan.error.empty_goal": {
        "en": "Please describe a goal.",
        "zh": "请先描述你的目标。",
    },
    "plan.error.llm_unsupported": {
        "en": "Skill `{skill}` does not support the LLM planner. Use `rule` or pick another skill.",
        "zh": "Skill `{skill}` 不支持 LLM planner。请改用 `rule`，或换一个 skill。",
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
    "plan.summary.col.approve": {"en": "approve?", "zh": "需审批？"},
    "plan.summary.col.reason": {"en": "reason", "zh": "原因"},
    "plan.summary.approve.yes": {"en": "yes", "zh": "是"},
    "plan.summary.approve.no": {"en": "no", "zh": "否"},
    "plan.last_plan.expander": {
        "en": "Last plan: {task_id}",
        "zh": "上一次规划：{task_id}",
    },
    # ───────────────────────── execute page ─────────────────────────
    "execute.subtitle": {
        "en": "Dry-run → review → approve → execute → verify.",
        "zh": "Dry-run → 审阅 → 审批 → 执行 → Verify。",
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
    "execute.verifier_badge": {"en": "Verifier:", "zh": "Verifier："},
    "execute.task.done_hint": {
        "en": "To re-run on a fresh state, create a new task from the **📋 Plan** page.",
        "zh": "如要在干净的状态下重新跑，请回到 **📋 Plan** 新建一个 task。",
    },
    "execute.stage1.header": {"en": "Stage 1 — Dry run", "zh": "阶段 1 — Dry run（预演）"},
    "execute.stage1.button": {"en": "🔍 Render dry-run", "zh": "🔍 渲染预演"},
    "execute.stage1.spinner": {
        "en": "Computing dry-run...",
        "zh": "正在计算预演…",
    },
    "execute.stage1.fail": {
        "en": "Dry-run failed: {err_type}: {err}",
        "zh": "预演失败：{err_type}: {err}",
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
        "en": "📄 Dry-run preview (markdown)",
        "zh": "📄 Dry-run 预览（Markdown）",
    },
    "execute.stage1.hint": {
        "en": "Click **Render dry-run** above to preview every planned action.",
        "zh": "点上方 **Render dry-run** 预览每一个计划好的动作。",
    },
    "execute.stage2.header": {"en": "Stage 2 — Approval", "zh": "阶段 2 — Approval（审批）"},
    "execute.stage2.blocked": {
        "en": "Policy guard blocked one or more actions (see warnings above). Execute will refuse the run.",
        "zh": "Policy guard 拦住了一个或多个动作（见上方警告）。Execute 会拒绝运行。",
    },
    "execute.stage2.checkbox": {
        "en": "✅ I've reviewed every action above and consent to commit them.",
        "zh": "✅ 我已审阅上述每个动作并同意提交。",
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
        "en": "Approval token missing. Re-run dry-run.",
        "zh": "缺少 approval_token。请重新跑 dry-run。",
    },
    "execute.stage3.token_validate": {
        "en": "Validating approval token...",
        "zh": "正在校验 approval_token…",
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
    "execute.metric.verifier": {"en": "Verifier", "zh": "Verifier"},
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
        "en": "❌ Verifier failed:",
        "zh": "❌ Verifier 失败：",
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
    "memory.tab.audit": {"en": "📜 Audit log", "zh": "📜 审计日志"},
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
