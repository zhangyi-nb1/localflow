"""Plan page — describe a goal, agent decides the rest.

Phase 8.3 / v0.9.0 simplification. The page is now a goal text area,
a one-line auto-detect status, and a "Create plan" button. No Override
expander, no capability-gap warning — the new ``agent`` meta-skill
handles compound goals end-to-end in a single ActionPlan, so there's
nothing to override and no gap to warn about.

The auto-detect module always returns ``agent`` + ``llm`` (or ``rule``
fallback for empty goals). Specialist skills remain in the registry
for CLI / MCP callers but are no longer surfaced in the UI.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.harness import control_loop
from app.harness.audit import AuditLogger
from app.memory import MemoryStore, NamingStyle
from app.schemas import TaskSpec
from app.skills import SkillError, get_default_registry
from app.storage.run_store import RunStore
from app.ui._autodetect import autodetect_planner, autodetect_skill
from app.ui._i18n import t
from app.ui._layout import (
    SESSION_TASK_KEY,
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
    require_workspace,
    risk_badge,
)


def main() -> None:
    configure_page("app.page_title.plan", icon="📋")
    render_header("app.page_title.plan", "plan.subtitle")
    render_unsafe_banner()
    render_sandbox_sidebar()
    workspace = require_workspace()

    registry = get_default_registry()

    # Goal input — outside any form so Streamlit reruns on every change
    # and the auto-detect line updates live.
    goal = st.text_area(
        t("plan.goal.label"),
        placeholder=t("plan.goal.placeholder"),
        height=80,
        key="plan_goal_input",
    )

    # Cheap workspace scan kept around for any future heuristics; the
    # v0.9.0 autodetect ignores it.
    detect_snapshot = _detect_snapshot(workspace)

    try:
        prefer_llm = MemoryStore().load().prefer_llm_planner
    except Exception:
        prefer_llm = False

    skill_choice = autodetect_skill(goal, detect_snapshot, registry)
    planner_choice = autodetect_planner(goal, skill_choice.name, registry, prefer_llm=prefer_llm)

    if goal.strip():
        st.markdown(
            t(
                "plan.autodetect.label",
                skill=skill_choice.name,
                planner=planner_choice.name,
            )
        )
        st.caption(
            t(
                "plan.autodetect.reason",
                skill_reason=skill_choice.reason,
                planner_reason=planner_choice.reason,
            )
        )
    else:
        st.markdown(t("plan.goal.empty_hint"))

    skill_name = skill_choice.name
    planner = planner_choice.name

    submitted = st.button(t("plan.button.create"), type="primary", key="plan_submit")
    if not submitted:
        _maybe_show_last_plan()
        return

    if not goal.strip():
        st.error(t("plan.error.empty_goal"))
        return

    skill_obj = registry.require(skill_name)
    if planner == "llm" and not skill_obj.supports_llm():
        st.error(t("plan.error.llm_unsupported", skill=skill_name))
        return

    prefs = MemoryStore().load()
    preferences: dict = {}
    if prefs.naming_style != NamingStyle.ORIGINAL:
        preferences["naming_style"] = prefs.naming_style.value

    store = RunStore.create()
    task = TaskSpec(
        task_id=store.task_id,
        user_goal=goal,
        workspace_root=str(workspace),
        skill=skill_name,
        constraints=[
            "do not delete any file",
            "do not overwrite existing files",
            "all paths must remain inside workspace_root",
        ],
        allowed_actions=list(skill_obj.manifest.allowed_actions),
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=list(prefs.forbidden_paths),
        preferences=preferences,
    )
    if not prefs.is_default():
        applied = []
        if prefs.forbidden_paths:
            applied.append(f"{len(prefs.forbidden_paths)} forbidden_paths")
        if prefs.naming_style != NamingStyle.ORIGINAL:
            applied.append(f"naming_style={prefs.naming_style.value}")
        st.info(t("plan.info.prefs_applied", summary=", ".join(applied)))

    store.save_task(task)
    audit = AuditLogger(store.audit_log_path)
    audit.log(
        "task.created.ui",
        task_id=task.task_id,
        goal=goal,
        skill=skill_name,
        planner=planner,
    )

    with st.spinner(t("plan.spinner.scanning")):
        snapshot = control_loop.run_inspect(
            workspace, task_id=task.task_id, compute_hash=True, compute_preview=True
        )
    store.save_workspace(snapshot)

    try:
        if planner == "rule":
            plan = skill_obj.plan(task, snapshot)
        else:
            with st.spinner(t("plan.spinner.llm")):
                plan = skill_obj.plan_with_llm(task, snapshot)
        skill_obj.validate(plan)
        store.save_plan(plan)
    except (SkillError, Exception) as exc:
        st.error(t("plan.error.planning_failed", err_type=type(exc).__name__, err=str(exc)))
        return

    assessment = control_loop.run_risk_check(task, plan)
    st.session_state[SESSION_TASK_KEY] = task.task_id

    _render_plan_summary(task, plan, assessment, snapshot)

    next_steps = st.container()
    with next_steps:
        st.success(t("plan.success.created", task_id=task.task_id))
        col_btn, _ = st.columns([1, 3])
        if col_btn.button(
            t("plan.button.goto_execute"),
            type="primary",
            key="goto_execute_btn",
        ):
            st.switch_page("pages/2_Execute.py")
        st.caption(t("plan.caption.goto_execute"))


def _detect_snapshot(workspace):
    """Cheap workspace scan cached per workspace path. v0.9.0 autodetect
    doesn't actually consume the snapshot, but the helper is retained
    so any future per-workspace UI hints can plug in cheaply."""
    key = f"_detect_snap::{workspace}"
    cached = st.session_state.get(key)
    if cached is not None:
        return cached
    try:
        snap = control_loop.run_inspect(
            workspace,
            task_id="autodetect",
            compute_hash=False,
            compute_preview=False,
        )
    except Exception:
        snap = None
    st.session_state[key] = snap
    return snap


def _maybe_show_last_plan() -> None:
    """If user revisits Plan page after creating one, show it again."""
    task_id = st.session_state.get(SESSION_TASK_KEY)
    if not task_id:
        return
    store = RunStore(task_id=task_id)
    if not (store.exists(store.TASK_JSON) and store.exists(store.PLAN_JSON)):
        return
    with st.expander(t("plan.last_plan.expander", task_id=task_id), expanded=False):
        task = store.load_task()
        plan = store.load_plan()
        assessment = control_loop.run_risk_check(task, plan)
        snapshot = store.load_workspace()
        _render_plan_summary(task, plan, assessment, snapshot)


def _render_plan_summary(task, plan, assessment, snapshot) -> None:
    st.subheader(t("plan.summary.title", plan_id=plan.plan_id))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("plan.summary.metric.actions"), len(plan.actions))
    col2.metric(t("plan.summary.metric.files"), snapshot.total_files)
    col3.markdown(
        f"**{t('plan.summary.metric.risk')}**<br>{risk_badge(assessment.risk_level.value)}",
        unsafe_allow_html=True,
    )
    col4.metric(t("plan.summary.metric.outputs"), len(plan.expected_outputs))

    if assessment.warnings:
        with st.expander(
            t("plan.summary.warnings_expander", n=len(assessment.warnings)), expanded=True
        ):
            for w in assessment.warnings:
                st.warning(w)

    if plan.actions:
        yes_label = t("plan.summary.approve.yes")
        no_label = t("plan.summary.approve.no")
        rows = []
        for i, a in enumerate(plan.actions, start=1):
            rows.append(
                {
                    t("plan.summary.col.idx"): i,
                    t("plan.summary.col.type"): a.action_type.value,
                    t("plan.summary.col.path"): _format_path_pair(a.source_path, a.target_path),
                    t("plan.summary.col.risk"): a.risk_level.value,
                    t("plan.summary.col.approve"): yes_label if a.requires_approval else no_label,
                    t("plan.summary.col.reason"): (
                        (a.reason[:80] + "…") if len(a.reason) > 80 else a.reason
                    ),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info(t("plan.summary.no_actions"))


def _format_path_pair(src: str | None, tgt: str | None) -> str:
    if src and tgt:
        return f"{src} → {tgt}"
    if tgt:
        return f"(new) → {tgt}"
    if src:
        return f"{src} → ?"
    return "—"


main()
