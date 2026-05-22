"""Workspace page (v0.22, C-nav) — browse the active workspace.

Shows the active workspace summary (file count, total size, number of
runs against this workspace) and a paged file list. This is the
"what's actually in my workspace?" surface that previously only
appeared as a sidebar fragment on Home.
"""

from __future__ import annotations

import json
from datetime import datetime
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

_MAX_FILES_SHOWN = 200


def main() -> None:
    configure_page("app.page_title.workspace", icon="🗂️")
    render_header("app.page_title.workspace", subtitle_key="workspace.subtitle")
    render_unsafe_banner()
    workspace = render_sandbox_sidebar()

    if workspace is None:
        st.info(t("workspace.no_workspace"))
        return

    files = sorted(
        (p for p in workspace.rglob("*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    total_bytes = sum(f.stat().st_size for f in files)
    runs_here = _count_runs_for(workspace)

    st.markdown(t("workspace.summary.title"))
    col1, col2, col3 = st.columns(3)
    col1.metric(t("workspace.summary.total_files"), f"{len(files)}")
    col2.metric(t("workspace.summary.total_size"), _fmt_size(total_bytes))
    col3.metric(t("workspace.summary.runs_here"), f"{runs_here}")

    st.markdown(t("workspace.file_list.title"))
    if not files:
        st.markdown(t("workspace.file_list.empty"))
        return

    shown = files[:_MAX_FILES_SHOWN]
    df = pd.DataFrame(
        [
            {
                t("workspace.file_list.col.path"): str(f.relative_to(workspace)),
                t("workspace.file_list.col.size"): _fmt_size(f.stat().st_size),
                t("workspace.file_list.col.modified"): _fmt_ts(f.stat().st_mtime),
            }
            for f in shown
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)
    if len(files) > _MAX_FILES_SHOWN:
        st.caption(
            t(
                "workspace.file_list.truncated",
                n=_MAX_FILES_SHOWN,
                total=len(files),
            )
        )


def _count_runs_for(workspace: Path) -> int:
    runs_root = localflow_home() / "runs"
    if not runs_root.exists():
        return 0
    target = workspace.resolve()
    n = 0
    for d in runs_root.iterdir():
        if not d.is_dir():
            continue
        task_file = d / "task.json"
        if not task_file.exists():
            continue
        try:
            data = json.loads(task_file.read_text(encoding="utf-8"))
            ws = data.get("workspace_root", "")
            if ws and Path(ws).resolve() == target:
                n += 1
        except Exception:
            continue
    return n


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n //= 1024
    return f"{n:,.1f} TB"


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


main()
