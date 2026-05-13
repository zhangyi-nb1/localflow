"""Rollback page — preview drift, then safe/force rollback."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from app.harness.rollback import Rollback
from app.storage.run_store import RunStore, localflow_home
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
    status_badge,
)


def main() -> None:
    configure_page("Rollback", icon="↺")
    render_header("Rollback", "Replay the rollback manifest — with hash-drift guard.")
    render_unsafe_banner()
    render_sandbox_sidebar()

    task_id = _pick_rollbackable_task()
    if not task_id:
        return

    store = RunStore(task_id=task_id)
    task = store.load_task()
    manifest = store.load_rollback()

    rb = Rollback(workspace_root=Path(task.workspace_root), run_store=store)

    st.subheader(f"Preview rollback for `{task_id}`")
    if st.button("🔍 Preview", type="primary"):
        st.session_state["_rb_preview"] = rb.preview(manifest)

    preview = st.session_state.get("_rb_preview")
    if not preview:
        st.info("Click **Preview** to compute drift status for each rollback entry.")
        return

    col1, col2 = st.columns(2)
    col1.metric("Entries", preview.entry_count)
    col2.markdown(
        f"**State:**<br>{status_badge(not preview.has_conflicts, label_ok='CLEAN', label_fail='CONFLICTS')}",
        unsafe_allow_html=True,
    )

    if preview.has_conflicts:
        st.warning(
            "⚠️ One or more files have been modified since execute. "
            "Safe rollback will **skip** those entries to protect your edits."
        )

    rows = []
    for e in preview.entries:
        drift = e.get("drift")
        rows.append(
            {
                "action_id": e["action_id"],
                "op": e["op"],
                "target": e.get("target_path") or e.get("source_path") or "—",
                "status": "✅ clean" if drift is None else "⚠️ drift",
                "reason": ""
                if drift is None
                else (drift[:120] + ("…" if len(drift) > 120 else "")),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Run rollback")

    if not preview.has_conflicts:
        if st.button("↺ Rollback now (clean)", type="primary"):
            with st.spinner("Rolling back..."):
                outcome = rb.run(manifest, force=False)
            _render_outcome(outcome)
            st.session_state.pop("_rb_preview", None)
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("↺ Safe rollback (skip conflicts)"):
                with st.spinner("Rolling back (skipping drifted entries)..."):
                    outcome = rb.run(manifest, force=False)
                _render_outcome(outcome)
                st.session_state.pop("_rb_preview", None)
        with col2:
            confirm = st.checkbox(
                "⚠ I accept that forcing will **overwrite my manual edits**.",
                key="force_confirm",
            )
            if st.button(
                "🔥 Force rollback (clobber edits)",
                disabled=not confirm,
                type="secondary",
            ):
                with st.spinner("Force rolling back..."):
                    outcome = rb.run(manifest, force=True)
                _render_outcome(outcome)
                st.session_state.pop("_rb_preview", None)


def _pick_rollbackable_task() -> str | None:
    """Pick a task that has a rollback_manifest.json (i.e. was executed)."""
    home = localflow_home()
    runs_root = home / "runs"
    if not runs_root.exists():
        st.info("No runs in this LocalFlow store yet.")
        return None

    current_ws = st.session_state.get("current_workspace")
    candidates: list[tuple[str, str]] = []
    for d in sorted(runs_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        if not (d / "rollback_manifest.json").exists():
            continue
        try:
            import json as _json

            task_data = _json.loads((d / "task.json").read_text(encoding="utf-8"))
            ws = task_data.get("workspace_root", "")
            if current_ws and Path(ws).resolve() != Path(current_ws).resolve():
                continue
            label = f"{d.name} — {task_data.get('user_goal', '')[:40]}"
            candidates.append((d.name, label))
        except Exception:
            continue

    if not candidates:
        st.info(
            "No rollbackable runs for the current workspace. "
            "Execute something on the **🔍 Execute** page first."
        )
        return None

    labels = [lbl for _, lbl in candidates]
    chosen_label = st.selectbox("Run to rollback", options=labels, key="rb_task_select")
    return candidates[labels.index(chosen_label)][0]


def _render_outcome(outcome) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Undone", len(outcome.undone))
    col2.metric("Failed", len(outcome.failed))
    col3.metric("Conflicts", len(outcome.conflicts))
    col4.markdown(
        f"**Status:**<br>{status_badge(outcome.success)}",
        unsafe_allow_html=True,
    )

    if outcome.failed:
        with st.expander(f"❌ Failed ({len(outcome.failed)})", expanded=True):
            for f in outcome.failed:
                st.error(f)
    if outcome.conflicts:
        with st.expander(f"⚠️ Conflicts skipped ({len(outcome.conflicts)})", expanded=False):
            for c in outcome.conflicts:
                st.warning(
                    f"`{c.get('action_id')}` ({c.get('op')}) on "
                    f"`{c.get('target_path')}`: {c.get('reason')}"
                )

    if outcome.success:
        st.success("✅ Rollback complete.")


main()
