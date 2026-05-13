"""Memory page — edit forbidden_paths + naming_style, browse audit."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.memory import MemoryStore, MemoryStoreError, NamingStyle
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)


def main() -> None:
    configure_page("Memory", icon="⚙")
    render_header("Memory", "Persistent user preferences. Every mutation is audited.")
    render_unsafe_banner()
    render_sandbox_sidebar()

    store = MemoryStore()
    try:
        prefs = store.load()
    except MemoryStoreError as exc:
        st.error(f"Memory store error: {exc}")
        return

    tab1, tab2, tab3 = st.tabs(["🚫 Forbidden paths", "📝 Naming style", "📜 Audit log"])

    with tab1:
        _render_forbidden_paths(store, prefs)

    with tab2:
        _render_naming_style(store, prefs)

    with tab3:
        _render_audit(store)


def _render_forbidden_paths(store: MemoryStore, prefs) -> None:
    st.subheader("Forbidden paths (kernel-enforced)")
    st.caption(
        "Workspace-relative paths the kernel refuses to touch. "
        "Applies to every Skill, every driver (CLI / MCP / UI)."
    )

    if prefs.forbidden_paths:
        for p in prefs.forbidden_paths:
            cols = st.columns([5, 1])
            cols[0].code(p, language="text")
            if cols[1].button("🗑", key=f"remove_{p}", help=f"Unforbid {p}"):
                try:
                    result = store.remove_forbidden_path(p)
                    if result.changed:
                        st.success(f"Removed `{p}`")
                        st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info("No forbidden paths set. The kernel won't refuse any path on those grounds.")

    st.divider()
    with st.form("add_forbidden"):
        new_path = st.text_input(
            "Add a path",
            placeholder="e.g. private/secrets",
            help="Workspace-relative. Absolute paths and `..` traversal are rejected.",
        )
        if st.form_submit_button("➕ Forbid"):
            if not new_path.strip():
                st.warning("Type a path first.")
            else:
                try:
                    result = store.add_forbidden_path(new_path.strip())
                    if result.changed:
                        st.success(f"Added `{new_path}` to forbidden_paths.")
                        st.rerun()
                    else:
                        st.info(f"`{new_path}` was already in forbidden_paths.")
                except ValueError as exc:
                    st.error(str(exc))


def _render_naming_style(store: MemoryStore, prefs) -> None:
    st.subheader("Naming style")
    st.caption(
        "Read by `folder_organizer` when renaming files. "
        "Applies to move targets in the planned ActionPlan."
    )

    current = prefs.naming_style.value
    styles = [s.value for s in NamingStyle]
    new_style = st.radio(
        "Style",
        options=styles,
        index=styles.index(current),
        help="`original` = no transform. Otherwise stem-only transform; extension preserved.",
        horizontal=True,
    )

    if new_style != current:
        if st.button(f"Save: {current} → {new_style}", type="primary"):
            try:
                result = store.set_naming_style(new_style)
                if result.changed:
                    st.success(result.detail)
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if current != NamingStyle.ORIGINAL.value:
        if st.button("Reset to default (original)"):
            store.clear_naming_style()
            st.rerun()

    with st.expander("Example transformations"):
        from app.memory.naming import apply_naming_style

        examples = [
            "Report (Final).pdf",
            "MY NOTES.txt",
            "data set v2.csv",
            "Q1+Q2 results.xlsx",
        ]
        rows = []
        for ex in examples:
            row = {"original": ex}
            for s in styles:
                row[s] = apply_naming_style(ex, s)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_audit(store: MemoryStore) -> None:
    st.subheader("Audit log")
    st.caption(
        "Every memory mutation (forbid / unforbid / set / unset) writes a "
        "row here. JSONL on disk at "
        f"`{store.audit_path}`."
    )

    limit = st.slider("Show recent N entries", min_value=10, max_value=200, value=50)
    entries = store.read_audit(limit=limit)
    if not entries:
        st.info("No mutations recorded yet.")
        return

    # Render newest-first.
    rows = []
    for e in reversed(entries):
        rows.append(
            {
                "timestamp": e.get("ts", ""),
                "event": e.get("event", ""),
                "path/key": e.get("path") or e.get("key") or "",
                "before": _stringify(e.get("before")),
                "after": _stringify(e.get("after")),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)


main()
