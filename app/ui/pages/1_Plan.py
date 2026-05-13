"""Plan page — create a new task + ActionPlan."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.harness import control_loop
from app.harness.audit import AuditLogger
from app.memory import MemoryStore, NamingStyle
from app.schemas import TaskSpec
from app.skills import SkillError, get_default_registry
from app.storage.run_store import RunStore
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
    configure_page("Plan", icon="📋")
    render_header("Plan", "Create a structured ActionPlan from a goal.")
    render_unsafe_banner()
    render_sandbox_sidebar()
    workspace = require_workspace()

    registry = get_default_registry()
    skill_names = registry.list_names()

    with st.form("plan_form"):
        col1, col2 = st.columns([2, 1])
        skill_name = col1.selectbox(
            "Skill",
            options=skill_names,
            index=skill_names.index("folder_organizer") if "folder_organizer" in skill_names else 0,
            help="Built-in or external skill to use.",
        )
        planner = col2.radio(
            "Planner",
            options=["rule", "llm"],
            index=0,
            help="`rule` is deterministic + instant. `llm` is slower but understands semantic goals.",
        )
        goal = st.text_area(
            "Goal",
            placeholder="e.g. organize this folder by file type",
            height=80,
        )
        submitted = st.form_submit_button("📋 Create plan", type="primary")

    if not submitted:
        _maybe_show_last_plan()
        return

    if not goal.strip():
        st.error("Please describe a goal.")
        return

    skill_obj = registry.require(skill_name)
    if planner == "llm" and not skill_obj.supports_llm():
        st.error(
            f"Skill `{skill_name}` does not support the LLM planner. "
            f"Use `rule` or pick a different skill."
        )
        return

    # Mirror CLI: load memory prefs and project onto TaskSpec.
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
        st.info(f"Applied preferences from memory: {', '.join(applied)}")

    store.save_task(task)
    audit = AuditLogger(store.audit_log_path)
    audit.log("task.created.ui", task_id=task.task_id, goal=goal, planner=planner)

    with st.spinner("Scanning workspace..."):
        snapshot = control_loop.run_inspect(
            workspace, task_id=task.task_id, compute_hash=True, compute_preview=True
        )
    store.save_workspace(snapshot)

    try:
        if planner == "rule":
            plan = skill_obj.plan(task, snapshot)
        else:
            with st.spinner("LLM planning (this may take ~20s)..."):
                plan = skill_obj.plan_with_llm(task, snapshot)
        skill_obj.validate(plan)
        store.save_plan(plan)
    except (SkillError, Exception) as exc:
        st.error(f"Planning failed: {type(exc).__name__}: {exc}")
        return

    assessment = control_loop.run_risk_check(task, plan)
    st.session_state[SESSION_TASK_KEY] = task.task_id

    _render_plan_summary(task, plan, assessment, snapshot)

    next_steps = st.container()
    with next_steps:
        st.success(
            f"✅ Task `{task.task_id}` created. Head to the **🔍 Execute** "
            f"page to dry-run and commit."
        )


def _maybe_show_last_plan() -> None:
    """If user revisits Plan page after creating one, show it again."""
    task_id = st.session_state.get(SESSION_TASK_KEY)
    if not task_id:
        return
    store = RunStore(task_id=task_id)
    if not (store.exists(store.TASK_JSON) and store.exists(store.PLAN_JSON)):
        return
    with st.expander(f"Last plan: {task_id}", expanded=False):
        task = store.load_task()
        plan = store.load_plan()
        assessment = control_loop.run_risk_check(task, plan)
        snapshot = store.load_workspace()
        _render_plan_summary(task, plan, assessment, snapshot)


def _render_plan_summary(task, plan, assessment, snapshot) -> None:
    st.subheader(f"Plan `{plan.plan_id}`")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Actions", len(plan.actions))
    col2.metric("Files scanned", snapshot.total_files)
    col3.markdown(f"**Risk**<br>{risk_badge(assessment.risk_level.value)}", unsafe_allow_html=True)
    col4.metric("Outputs", len(plan.expected_outputs))

    if assessment.warnings:
        with st.expander(f"⚠️ {len(assessment.warnings)} warning(s)", expanded=True):
            for w in assessment.warnings:
                st.warning(w)

    if plan.actions:
        rows = []
        for i, a in enumerate(plan.actions, start=1):
            rows.append(
                {
                    "#": i,
                    "type": a.action_type.value,
                    "source → target": _format_path_pair(a.source_path, a.target_path),
                    "risk": a.risk_level.value,
                    "approve?": "yes" if a.requires_approval else "no",
                    "reason": (a.reason[:80] + "…") if len(a.reason) > 80 else a.reason,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Plan has 0 actions (workspace already organized?).")


def _format_path_pair(src: str | None, tgt: str | None) -> str:
    if src and tgt:
        return f"{src} → {tgt}"
    if tgt:
        return f"(new) → {tgt}"
    if src:
        return f"{src} → ?"
    return "—"


main()
