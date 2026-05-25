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
from typing import Any

import pandas as pd
import streamlit as st

from app.storage.run_store import RunStore, localflow_home
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
                t("runs.table.col.status"): (
                    t("runs.status.executed") if r["has_manifest"] else t("runs.status.planned")
                ),
                t("runs.table.col.verify"): _verify_label(r["verify_passed"]),
                t("runs.table.col.trace"): r["trace_count"] or "—",
                t("runs.table.col.rollback"): (
                    t("runs.rollback.available") if r["has_manifest"] else t("runs.rollback.none")
                ),
                t("runs.table.col.skill"): r["skill"] or "—",
                t("runs.table.col.workspace"): r["workspace"],
            }
            for r in runs
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            t("runs.table.col.task_id"): st.column_config.TextColumn(width="medium"),
            t("runs.table.col.status"): st.column_config.TextColumn(width="small"),
            t("runs.table.col.verify"): st.column_config.TextColumn(width="small"),
            t("runs.table.col.trace"): st.column_config.TextColumn(width="small"),
            t("runs.table.col.rollback"): st.column_config.TextColumn(width="small"),
            t("runs.table.col.skill"): st.column_config.TextColumn(width="small"),
            t("runs.table.col.workspace"): st.column_config.TextColumn(width="medium"),
        },
    )

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

    if chosen_row["has_manifest"]:
        if st.button(
            t("runs.action.open_rollback"),
            key="runs_open_rollback",
            type="primary",
        ):
            st.session_state["current_task_id"] = chosen_row["task_id"]
            st.switch_page("pages/7_Rollback.py")

    _render_run_evidence(chosen_row)


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
        manifest = d / RunStore.ROLLBACK_JSON
        verify = _read_json(d / RunStore.VERIFY_JSON)
        rollback = _read_json(manifest)
        out.append(
            {
                "task_id": d.name,
                "skill": data.get("skill", ""),
                "workspace": _shorten_path(ws),
                "ws_resolved": ws_resolved,
                "has_manifest": manifest.exists(),
                "run_dir": d,
                "trace_count": _jsonl_count(d / RunStore.TRACE_JSONL),
                "verify_passed": _verify_passed(verify),
                "rollback_count": len(rollback.get("entries", [])) if rollback else 0,
                "artifact_count": len(_artifact_files(d)),
            }
        )
    return out


def _render_run_evidence(row: dict[str, Any]) -> None:
    run_dir = row["run_dir"]
    trace_rows = _read_jsonl(run_dir / RunStore.TRACE_JSONL)
    verify = _read_json(run_dir / RunStore.VERIFY_JSON)
    rollback = _read_json(run_dir / RunStore.ROLLBACK_JSON)
    dry_run = _read_text(run_dir / RunStore.DRY_RUN_MD)
    final_report = _read_text(run_dir / RunStore.FINAL_REPORT_MD)
    artifacts = _artifact_files(run_dir)

    st.markdown(t("runs.detail.heading", task_id=row["task_id"]))
    st.caption(t("runs.detail.run_dir", path=str(run_dir)))
    col1, col2, col3 = st.columns(3)
    col1.metric(t("runs.detail.trace_events"), str(len(trace_rows)))
    col2.metric(t("runs.detail.artifact_count"), str(len(artifacts)))
    col3.metric(
        t("runs.detail.rollback_entries"),
        str(len(rollback.get("entries", [])) if rollback else 0),
    )

    if final_report is not None:
        with st.expander(t("runs.action.view_report"), expanded=False):
            st.markdown(final_report)
    else:
        st.caption(t("runs.detail.no_report"))

    with st.expander(t("runs.detail.dry_run"), expanded=False):
        if dry_run is None:
            st.markdown(t("runs.detail.missing"))
        else:
            st.markdown(dry_run)

    with st.expander(t("runs.detail.verify_report"), expanded=True):
        if verify is None:
            st.markdown(t("runs.detail.missing"))
        else:
            passed = bool(verify.get("passed"))
            st.markdown(
                t("runs.detail.verify_passed") if passed else t("runs.detail.verify_failed")
            )
            summary = verify.get("summary")
            if summary:
                st.caption(str(summary))
            checks = verify.get("checks") or []
            if checks:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "check": c.get("name", ""),
                                "passed": "yes" if c.get("passed") else "no",
                                "detail": c.get("detail", ""),
                            }
                            for c in checks
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )

    tail = trace_rows[-30:]
    with st.expander(t("runs.detail.trace", n=len(tail)), expanded=False):
        if not tail:
            st.markdown(t("runs.detail.trace_missing"))
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "ts": r.get("ts", ""),
                            "event": r.get("event", ""),
                            "detail": (r.get("payload") or {}).get("detail", ""),
                            "status": (r.get("payload") or {}).get("status", ""),
                        }
                        for r in tail
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    with st.expander(t("runs.detail.rollback"), expanded=False):
        entries = rollback.get("entries", []) if rollback else []
        if not entries:
            st.markdown(t("runs.detail.missing"))
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "action_id": e.get("action_id", ""),
                            "op": e.get("op", ""),
                            "source": e.get("source_path") or "",
                            "target": e.get("target_path") or "",
                        }
                        for e in entries
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    with st.expander(t("runs.detail.artifacts"), expanded=False):
        if not artifacts:
            st.markdown(t("runs.detail.missing"))
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "path": str(p.relative_to(run_dir)),
                            "size": p.stat().st_size,
                        }
                        for p in artifacts
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _verify_label(passed: bool | None) -> str:
    if passed is True:
        return t("runs.status.verified")
    if passed is False:
        return t("runs.status.failed")
    return t("runs.status.unverified")


def _verify_passed(data: dict[str, Any] | None) -> bool | None:
    if data is None or "passed" not in data:
        return None
    return bool(data.get("passed"))


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    except OSError:
        return []
    return rows


def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _artifact_files(run_dir: Path) -> list[Path]:
    if not run_dir.exists():
        return []
    try:
        paths = [
            p
            for p in run_dir.rglob("*")
            if p.is_file() and RunStore.BACKUPS_DIR not in p.relative_to(run_dir).parts
        ]
    except OSError:
        return []
    return sorted(paths)


def _shorten_path(p: str) -> str:
    if not p:
        return "—"
    path = Path(p)
    parts = path.parts
    if len(parts) <= 3:
        return p
    return ".../" + "/".join(parts[-3:])


main()
