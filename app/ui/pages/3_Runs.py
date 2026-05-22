"""Runs page (v0.22, C-nav) — index of every task LocalFlow has run.

Shows a table of past runs sorted newest first, with quick links to
re-open each one in the Rollback page. Provides a workspace filter
so the user can scope the list to the active workspace.

Subsumes the run-picker that used to live exclusively at the top of
3_Rollback.py — that page still owns the actual drift preview + safe/
force rollback workflow; this page is the index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app.storage.run_store import localflow_home
from app.ui._i18n import t
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)


def main() -> None:
    configure_page("app.page_title.runs", icon="📊")
    render_header("app.page_title.runs", subtitle_key="runs.subtitle")
    render_unsafe_banner()
    workspace = render_sandbox_sidebar()

    runs = _enumerate_runs()
    if not runs:
        st.info(t("runs.empty"))
        return

    only_active = st.checkbox(
        t("runs.filter.this_workspace"),
        value=workspace is not None,
        key="runs_filter_ws",
        disabled=workspace is None,
    )
    if only_active and workspace is not None:
        target = workspace.resolve()
        runs = [r for r in runs if r["ws_resolved"] == target]
        if not runs:
            st.info(t("runs.empty_ws"))
            return

    df = pd.DataFrame(
        [
            {
                t("runs.table.col.task_id"): r["task_id"],
                t("runs.table.col.skill"): r["skill"] or "—",
                t("runs.table.col.workspace"): r["workspace"],
                t("runs.table.col.status"): (
                    t("runs.status.executed") if r["has_manifest"] else t("runs.status.planned")
                ),
                t("runs.table.col.rollback"): (
                    t("runs.rollback.available") if r["has_manifest"] else t("runs.rollback.none")
                ),
            }
            for r in runs
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.divider()
    task_ids = [r["task_id"] for r in runs]
    chosen = st.selectbox(
        t("runs.table.col.task_id"),
        options=task_ids,
        key="runs_detail_select",
    )
    chosen_row = next((r for r in runs if r["task_id"] == chosen), None)
    if chosen_row is None:
        return

    col1, col2 = st.columns(2)
    with col1:
        if chosen_row["has_manifest"]:
            if st.button(
                t("runs.action.open_rollback"),
                key="runs_open_rollback",
                type="primary",
            ):
                st.session_state["current_task_id"] = chosen_row["task_id"]
                st.switch_page("pages/7_Rollback.py")
    with col2:
        if chosen_row["final_report"] is not None:
            with st.expander(t("runs.action.view_report"), expanded=False):
                st.markdown(chosen_row["final_report"])
        else:
            st.markdown(t("runs.detail.no_report"))


def _enumerate_runs() -> list[dict]:
    runs_root = localflow_home() / "runs"
    if not runs_root.exists():
        return []
    out: list[dict] = []
    for d in sorted(runs_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        task_file = d / "task.json"
        if not task_file.exists():
            continue
        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        ws = data.get("workspace_root", "")
        try:
            ws_resolved = Path(ws).resolve() if ws else None
        except Exception:
            ws_resolved = None
        manifest = d / "rollback_manifest.json"
        report = d / "final_report.md"
        report_text: str | None = None
        if report.exists():
            try:
                report_text = report.read_text(encoding="utf-8")
            except Exception:
                report_text = None
        out.append(
            {
                "task_id": d.name,
                "skill": data.get("skill", ""),
                "workspace": _shorten_path(ws),
                "ws_resolved": ws_resolved,
                "has_manifest": manifest.exists(),
                "final_report": report_text,
            }
        )
    return out


def _shorten_path(p: str) -> str:
    if not p:
        return "—"
    path = Path(p)
    parts = path.parts
    if len(parts) <= 3:
        return p
    return ".../" + "/".join(parts[-3:])


main()
