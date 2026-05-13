"""Execute page — dry-run → approval ceremony → execute → verify."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.harness import control_loop
from app.mcp.approval import ApprovalError, mint_token, validate_and_consume
from app.schemas import ExecutionStatus
from app.storage.run_store import RunStore, localflow_home
from app.ui._layout import (
    SESSION_DRY_RUN_KEY,
    SESSION_TASK_KEY,
    SESSION_TOKEN_KEY,
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
    risk_badge,
    status_badge,
)


def main() -> None:
    configure_page("Execute", icon="🔍")
    render_header("Execute", "Dry-run → review → approve → execute → verify.")
    render_unsafe_banner()
    render_sandbox_sidebar()

    task_id = _pick_task()
    if task_id is None:
        return

    store = RunStore(task_id=task_id)
    if not store.exists(store.TASK_JSON):
        st.error(f"No task.json for `{task_id}` — pick a valid task.")
        return
    if not store.exists(store.PLAN_JSON):
        st.error(f"Task `{task_id}` has no plan.json. Go to the **📋 Plan** page first.")
        return

    task = store.load_task()
    plan = store.load_plan()

    if store.exists(store.VERIFY_JSON):
        st.success(f"Task `{task_id}` already executed + verified.")
        verification = store.load_verification()
        st.markdown(f"Verifier: {status_badge(verification.passed)}")
        st.caption(verification.summary)
        st.divider()
        st.info("To re-run on a fresh state, create a new task from the **📋 Plan** page.")
        return

    # Stage 1: dry-run + token mint
    st.subheader("Stage 1 — Dry run")
    if st.button("🔍 Render dry-run", type="primary"):
        with st.spinner("Computing dry-run..."):
            try:
                assessment = control_loop.run_risk_check(task, plan)
                md = control_loop.run_dry_run(task, plan, assessment, store)
                token = mint_token(store, workspace_root=task.workspace_root)
                st.session_state[SESSION_DRY_RUN_KEY] = md
                st.session_state[SESSION_TOKEN_KEY] = token.token
                st.session_state["_last_dry_assessment"] = {
                    "risk_level": assessment.risk_level.value,
                    "passed": assessment.passed,
                    "warnings": list(assessment.warnings),
                }
            except Exception as exc:
                st.error(f"Dry-run failed: {type(exc).__name__}: {exc}")
                return

    if SESSION_DRY_RUN_KEY in st.session_state:
        info = st.session_state.get("_last_dry_assessment", {})
        col1, col2 = st.columns([1, 3])
        col1.markdown(
            f"**Risk:**<br>{risk_badge(info.get('risk_level', '—'))}", unsafe_allow_html=True
        )
        col2.metric("Actions to execute", len([a for a in plan.actions if a.is_write()]))
        if info.get("warnings"):
            with st.expander(f"⚠️ {len(info['warnings'])} warning(s)", expanded=True):
                for w in info["warnings"]:
                    st.warning(w)
        with st.expander("📄 Dry-run preview (markdown)", expanded=True):
            st.markdown(st.session_state[SESSION_DRY_RUN_KEY])
    else:
        st.info("Click **Render dry-run** above to preview every planned action.")
        return

    # Stage 2: approval ceremony
    st.subheader("Stage 2 — Approval")
    if not info.get("passed", True):
        st.error(
            "Policy guard blocked one or more actions (see warnings above). "
            "Execute will refuse the run."
        )
        return

    approved = st.checkbox(
        "✅ I've reviewed every action above and consent to commit them.",
        key="approval_checkbox",
    )

    # Stage 3: execute (only enabled after approval)
    st.subheader("Stage 3 — Execute + Verify")
    if not approved:
        st.button("Execute (locked)", disabled=True)
        st.caption("Check the approval box above to enable.")
        return

    if st.button("🚀 Execute now", type="primary"):
        token_str = st.session_state.get(SESSION_TOKEN_KEY)
        if not token_str:
            st.error("Approval token missing. Re-run dry-run.")
            return
        try:
            with st.spinner("Validating approval token..."):
                validate_and_consume(store, token_str, workspace_root=task.workspace_root)
            with st.spinner("Executing... (writing real changes)"):
                outcome = control_loop.run_execute(task, plan, store, approved=True)
                snapshot = store.load_workspace()
                verification = control_loop.run_verify(task, plan, store, outcome, snapshot)
        except ApprovalError as exc:
            st.error(f"Approval rejected: {exc}")
            return
        except Exception as exc:
            st.error(f"Execute failed: {type(exc).__name__}: {exc}")
            return

        # Render results
        success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
        skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Executed", success)
        col2.metric("Failed", failed, delta=None if failed == 0 else "fail")
        col3.metric("Skipped", skipped)
        col4.markdown(
            f"**Verifier**<br>{status_badge(verification.passed)}",
            unsafe_allow_html=True,
        )

        if verification.passed:
            st.success(
                f"✅ Task `{task_id}` complete. Run is recorded in "
                f"`{store.run_dir}`. To undo, go to the **↺ Rollback** page."
            )
        else:
            st.error(
                "❌ Verifier failed:\n\n" + "\n".join(f"- {c}" for c in verification.failed_checks)
            )

        # Clear the now-consumed token from session
        st.session_state.pop(SESSION_TOKEN_KEY, None)


def _pick_task() -> str | None:
    """Workspace-scoped task picker. Returns task_id or None."""
    home = localflow_home()
    runs_root = home / "runs"
    if not runs_root.exists():
        st.info("No tasks yet. Create one on the **📋 Plan** page first.")
        return None

    # Filter: tasks that match the current session workspace
    current_ws = st.session_state.get("current_workspace")
    candidates: list[tuple[str, str]] = []
    for d in sorted(runs_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        try:
            task_json = d / "task.json"
            if not task_json.exists():
                continue
            import json as _json

            data = _json.loads(task_json.read_text(encoding="utf-8"))
            ws = data.get("workspace_root", "")
            label = f"{d.name} — {data.get('user_goal', '')[:40]}"
            if not current_ws or Path(ws).resolve() == Path(current_ws).resolve():
                candidates.append((d.name, label))
        except Exception:
            continue

    if not candidates:
        st.info(
            "No tasks for the current workspace. Create one on **📋 Plan**, "
            "or switch workspace in the sidebar."
        )
        return None

    # Default to the most recent task (sessionStateOrFirst)
    session_task = st.session_state.get(SESSION_TASK_KEY)
    default_idx = 0
    for i, (tid, _) in enumerate(candidates):
        if tid == session_task:
            default_idx = i
            break

    labels = [lbl for _, lbl in candidates]
    chosen_label = st.selectbox(
        "Task",
        options=labels,
        index=default_idx,
        key="exec_task_select",
    )
    chosen = candidates[labels.index(chosen_label)][0]
    st.session_state[SESSION_TASK_KEY] = chosen
    return chosen


main()
