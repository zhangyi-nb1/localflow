"""Rollback page — preview drift, then safe/force rollback."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from app.harness.rollback import Rollback
from app.harness.trace import TraceLogger
from app.storage.run_store import RunStore, localflow_home
from app.ui._i18n import t
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
    status_badge,
)


def main() -> None:
    configure_page("app.page_title.rollback", icon="↺")
    render_header("app.page_title.rollback", "rollback.subtitle")
    render_unsafe_banner()
    render_sandbox_sidebar()

    task_id = _pick_rollbackable_task()
    if not task_id:
        return

    store = RunStore(task_id=task_id)
    task = store.load_task()
    manifest = store.load_rollback()
    trace = TraceLogger(store.trace_path)

    rb = Rollback(workspace_root=Path(task.workspace_root), run_store=store, trace=trace)

    st.subheader(t("rollback.preview.title", task_id=task_id))
    if st.button(t("rollback.preview.button"), type="primary"):
        st.session_state["_rb_preview"] = rb.preview(manifest)

    preview = st.session_state.get("_rb_preview")
    if not preview:
        st.info(t("rollback.preview.hint"))
        return

    col1, col2 = st.columns(2)
    col1.metric(t("rollback.preview.metric.entries"), preview.entry_count)
    col2.markdown(
        f"{t('rollback.preview.state_label')}<br>"
        + status_badge(
            not preview.has_conflicts,
            label_ok=t("common.status.clean"),
            label_fail=t("common.status.conflicts"),
        ),
        unsafe_allow_html=True,
    )

    if preview.has_conflicts:
        st.warning(t("rollback.preview.warn_conflicts"))

    rows = []
    clean_label = t("rollback.table.status.clean")
    drift_label = t("rollback.table.status.drift")
    for e in preview.entries:
        drift = e.get("drift")
        rows.append(
            {
                t("rollback.table.col.action_id"): e["action_id"],
                t("rollback.table.col.op"): e["op"],
                t("rollback.table.col.target"): (
                    e.get("target_path") or e.get("source_path") or "—"
                ),
                t("rollback.table.col.status"): clean_label if drift is None else drift_label,
                t("rollback.table.col.reason"): (
                    "" if drift is None else (drift[:120] + ("…" if len(drift) > 120 else ""))
                ),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.divider()
    st.subheader(t("rollback.run.header"))

    if not preview.has_conflicts:
        if st.button(t("rollback.btn.clean"), type="primary"):
            with st.spinner(t("rollback.spinner.clean")):
                outcome = rb.run(manifest, force=False)
            _render_outcome(outcome)
            st.session_state.pop("_rb_preview", None)
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button(t("rollback.btn.safe")):
                with st.spinner(t("rollback.spinner.safe")):
                    outcome = rb.run(manifest, force=False)
                _render_outcome(outcome)
                st.session_state.pop("_rb_preview", None)
        with col2:
            confirm = st.checkbox(
                t("rollback.btn.force_confirm"),
                key="force_confirm",
            )
            if st.button(
                t("rollback.btn.force"),
                disabled=not confirm,
                type="secondary",
            ):
                with st.spinner(t("rollback.spinner.force")):
                    outcome = rb.run(manifest, force=True)
                _render_outcome(outcome)
                st.session_state.pop("_rb_preview", None)


def _pick_rollbackable_task() -> str | None:
    """Pick a task that has a rollback_manifest.json (i.e. was executed)."""
    home = localflow_home()
    runs_root = home / "runs"
    if not runs_root.exists():
        st.info(t("rollback.no_runs"))
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
        st.info(t("rollback.no_runs_ws"))
        return None

    session_task = st.session_state.get("current_task_id")
    default_idx = 0
    for i, (tid, _) in enumerate(candidates):
        if tid == session_task:
            default_idx = i
            break

    labels = [lbl for _, lbl in candidates]
    chosen_label = st.selectbox(
        t("rollback.select.label"),
        options=labels,
        index=default_idx,
        key=_workspace_select_key("rb_task_select", current_ws),
    )
    chosen = candidates[labels.index(chosen_label)][0]
    st.session_state["current_task_id"] = chosen
    return chosen


def _workspace_select_key(prefix: str, current_ws: str | None) -> str:
    """Keep rollback task selectbox widget state scoped to the active workspace."""
    if not current_ws:
        return f"{prefix}::all"
    try:
        raw = str(Path(current_ws).resolve())
    except (OSError, RuntimeError):
        raw = current_ws
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}::{digest}"


def _render_outcome(outcome) -> None:
    # Distinguish "real failures" from "cascaded-from-conflict" failures.
    cascaded, real_failed = _split_cascaded_failures(outcome)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("rollback.metric.undone"), len(outcome.undone))
    col2.metric(t("rollback.metric.failed"), len(real_failed))
    col3.metric(t("rollback.metric.conflicts"), len(outcome.conflicts) + len(cascaded))
    real_success = not real_failed and not outcome.conflicts
    col4.markdown(
        f"{t('rollback.metric.status')}<br>"
        + status_badge(real_success, label_fail=t("common.status.partial")),
        unsafe_allow_html=True,
    )

    if cascaded:
        st.info(t("rollback.cascaded.info", n=len(cascaded)))
        with st.expander(
            t("rollback.cascaded.expander", n=len(cascaded)),
            expanded=False,
        ):
            for f in cascaded:
                st.write(
                    f"- `{f.get('action_id')}` `{f.get('op')}` on `{_extract_path_from_error(f)}`"
                )

    if real_failed:
        with st.expander(t("rollback.real_failures.expander", n=len(real_failed)), expanded=True):
            for f in real_failed:
                st.error(f)
    if outcome.conflicts:
        with st.expander(
            t("rollback.conflicts.expander", n=len(outcome.conflicts)), expanded=False
        ):
            for c in outcome.conflicts:
                st.warning(
                    f"`{c.get('action_id')}` ({c.get('op')}) on "
                    f"`{c.get('target_path')}`: {c.get('reason')}"
                )

    if real_success:
        st.success(t("rollback.success"))


def _split_cascaded_failures(outcome) -> tuple[list[dict], list[dict]]:
    """Split ``outcome.failed`` into (cascaded, real_failed).

    A ``delete_created_dir`` op that failed because the dir was non-empty
    AND any conflict's target lives at-or-under that dir → cascaded.
    Everything else is a real failure.
    """
    conflict_paths = [c.get("target_path") or c.get("source_path") or "" for c in outcome.conflicts]
    cascaded: list[dict] = []
    real: list[dict] = []
    for f in outcome.failed:
        if f.get("op") == "delete_created_dir" and "not empty" in str(f.get("error", "")):
            dir_path = _extract_path_from_error(f)
            if dir_path and any(
                cp == dir_path or cp.startswith(dir_path + "/") for cp in conflict_paths
            ):
                cascaded.append(f)
                continue
        real.append(f)
    return cascaded, real


def _extract_path_from_error(failed_entry: dict) -> str:
    """Pull the path out of 'refusing to remove: <path>' error string."""
    msg = str(failed_entry.get("error", ""))
    marker = "refusing to remove: "
    idx = msg.find(marker)
    if idx == -1:
        return ""
    return msg[idx + len(marker) :].rstrip("'\" }")


main()
