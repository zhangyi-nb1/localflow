"""Execute page — dry-run → approval ceremony → execute → verify."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.harness import control_loop
from app.mcp.approval import ApprovalError, mint_token, validate_and_consume
from app.schemas import ExecutionStatus
from app.storage.run_store import RunStore, localflow_home
from app.ui._i18n import t
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
    configure_page("app.page_title.execute", icon="🔍")
    render_header("app.page_title.execute", "execute.subtitle")
    render_unsafe_banner()
    render_sandbox_sidebar()

    task_id = _pick_task()
    if task_id is None:
        return

    store = RunStore(task_id=task_id)
    if not store.exists(store.TASK_JSON):
        st.error(t("execute.task.missing_task", task_id=task_id))
        return
    if not store.exists(store.PLAN_JSON):
        st.error(t("execute.task.missing_plan", task_id=task_id))
        return

    task = store.load_task()
    plan = store.load_plan()

    if store.exists(store.VERIFY_JSON):
        st.success(t("execute.task.done", task_id=task_id))
        verification = store.load_verification()
        st.markdown(f"{t('execute.verifier_badge')} {status_badge(verification.passed)}")
        st.caption(verification.summary)
        st.divider()
        st.info(t("execute.task.done_hint"))
        return

    # Stage 1: dry-run + token mint
    st.subheader(t("execute.stage1.header"))
    if st.button(t("execute.stage1.button"), type="primary"):
        with st.spinner(t("execute.stage1.spinner")):
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
                st.error(t("execute.stage1.fail", err_type=type(exc).__name__, err=str(exc)))
                return

    if SESSION_DRY_RUN_KEY in st.session_state:
        info = st.session_state.get("_last_dry_assessment", {})
        col1, col2 = st.columns([1, 3])
        col1.markdown(
            f"{t('execute.stage1.risk')}<br>{risk_badge(info.get('risk_level', '—'))}",
            unsafe_allow_html=True,
        )
        col2.metric(
            t("execute.stage1.actions_to_execute"),
            len([a for a in plan.actions if a.is_write()]),
        )
        if info.get("warnings"):
            with st.expander(
                t("execute.stage1.warnings_expander", n=len(info["warnings"])), expanded=True
            ):
                for w in info["warnings"]:
                    st.warning(w)
        with st.expander(t("execute.stage1.preview_expander"), expanded=True):
            st.markdown(st.session_state[SESSION_DRY_RUN_KEY])
    else:
        st.info(t("execute.stage1.hint"))
        return

    # Stage 2: approval ceremony
    st.subheader(t("execute.stage2.header"))
    if not info.get("passed", True):
        st.error(t("execute.stage2.blocked"))
        return

    approved = st.checkbox(t("execute.stage2.checkbox"), key="approval_checkbox")

    # Stage 3: execute (only enabled after approval)
    st.subheader(t("execute.stage3.header"))
    if not approved:
        st.button(t("execute.stage3.locked"), disabled=True)
        st.caption(t("execute.stage3.locked_caption"))
        return

    if st.button(t("execute.stage3.button"), type="primary"):
        token_str = st.session_state.get(SESSION_TOKEN_KEY)
        if not token_str:
            st.error(t("execute.stage3.token_missing"))
            return

        # Phase 13 — read memory prefs to decide whether to wire the
        # semantic verifier + auto-repair loop in this execute.
        from app.memory import MemoryStore as _MS

        prefs = _MS().load()
        enable_semantic = bool(getattr(prefs, "enable_semantic_verifier", False))
        max_auto_repairs = int(getattr(prefs, "max_auto_repairs", 0))
        semantic = None
        repair_outcome = None

        try:
            with st.spinner(t("execute.stage3.token_validate")):
                validate_and_consume(store, token_str, workspace_root=task.workspace_root)
            with st.spinner(t("execute.stage3.executing")):
                snapshot = store.load_workspace()
                if enable_semantic:
                    from app.skills import get_default_registry

                    skill_obj = get_default_registry().require(task.skill)
                    plan, outcome, verification, semantic, repair_outcome = (
                        control_loop.run_with_auto_repair(
                            task,
                            plan,
                            snapshot,
                            skill=skill_obj,
                            run_store=store,
                            approved=True,
                            enable_semantic=True,
                            max_auto_repairs=max_auto_repairs,
                        )
                    )
                else:
                    outcome = control_loop.run_execute(task, plan, store, approved=True)
                    verification = control_loop.run_verify(task, plan, store, outcome, snapshot)
        except ApprovalError as exc:
            st.error(t("execute.stage3.approval_err", err=str(exc)))
            return
        except Exception as exc:
            st.error(t("execute.stage3.exec_err", err_type=type(exc).__name__, err=str(exc)))
            return

        # Render results
        success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
        skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric(t("execute.metric.executed"), success)
        col2.metric(t("execute.metric.failed"), failed, delta=None if failed == 0 else "fail")
        col3.metric(t("execute.metric.skipped"), skipped)
        col4.markdown(
            f"**{t('execute.metric.verifier')}**<br>{status_badge(verification.passed)}",
            unsafe_allow_html=True,
        )

        # Phase 13 — render the semantic verdict panel when the verifier ran.
        if semantic is not None:
            _render_semantic_panel(semantic, repair_outcome)

        if verification.passed:
            st.success(t("execute.success", task_id=task_id, path=str(store.run_dir)))
            col_btn, _ = st.columns([1, 3])
            if col_btn.button(
                t("execute.button.goto_rollback"),
                type="primary",
                key="goto_rollback_btn",
            ):
                st.switch_page("pages/3_Rollback.py")
            st.caption(t("execute.caption.goto_rollback"))
        else:
            st.error(
                t("execute.fail.verifier")
                + "\n\n"
                + "\n".join(f"- {c}" for c in verification.failed_checks)
            )

        # Clear the now-consumed token from session
        st.session_state.pop(SESSION_TOKEN_KEY, None)


def _render_semantic_panel(semantic, repair_outcome) -> None:
    """Phase 13 — render the semantic verifier verdict table + the
    auto-repair attempt count (when the loop fired)."""
    st.markdown("---")
    if semantic.passed:
        st.success(t("execute.semantic.passed", summary=semantic.summary))
    else:
        st.warning(t("execute.semantic.failed", summary=semantic.summary))

    rows = []
    for v in semantic.verdicts:
        rows.append(
            {
                t("execute.semantic.col.grader"): v.grader,
                t("execute.semantic.col.passed"): "✓" if v.passed else "✗",
                t("execute.semantic.col.reason"): (
                    (v.reason[:120] + "…") if len(v.reason) > 120 else v.reason
                ),
                t("execute.semantic.col.hint"): (v.suggested_hint or "")[:120],
            }
        )
    if rows:
        import pandas as _pd

        st.dataframe(_pd.DataFrame(rows), width="stretch", hide_index=True)

    if repair_outcome is not None and repair_outcome.attempts > 0:
        verb = (
            t("execute.semantic.repair_repaired")
            if repair_outcome.repaired
            else t("execute.semantic.repair_still_failing")
        )
        st.info(
            t(
                "execute.semantic.repair_summary",
                verb=verb,
                attempts=repair_outcome.attempts,
                halt=repair_outcome.halt_reason,
            )
        )


def _pick_task() -> str | None:
    """Workspace-scoped task picker. Returns task_id or None."""
    home = localflow_home()
    runs_root = home / "runs"
    if not runs_root.exists():
        st.info(t("execute.no_runs"))
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
        st.info(t("execute.no_runs_ws"))
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
        t("execute.task.label"),
        options=labels,
        index=default_idx,
        key="exec_task_select",
    )
    chosen = candidates[labels.index(chosen_label)][0]
    st.session_state[SESSION_TASK_KEY] = chosen
    return chosen


main()
