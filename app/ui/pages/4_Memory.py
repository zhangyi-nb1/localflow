"""Memory page — edit forbidden_paths + naming_style, browse audit."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.memory import MemoryStore, MemoryStoreError, NamingStyle
from app.ui._i18n import t
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)


def main() -> None:
    configure_page("app.page_title.memory", icon="⚙")
    render_header("app.page_title.memory", "memory.subtitle")
    render_unsafe_banner()
    render_sandbox_sidebar()

    store = MemoryStore()
    try:
        prefs = store.load()
    except MemoryStoreError as exc:
        st.error(t("memory.error.store", err=str(exc)))
        return

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            t("memory.tab.forbidden"),
            t("memory.tab.naming"),
            t("memory.tab.planner"),
            t("memory.tab.audit"),
        ]
    )

    with tab1:
        _render_forbidden_paths(store, prefs)

    with tab2:
        _render_naming_style(store, prefs)

    with tab3:
        _render_planner_pref(store, prefs)

    with tab4:
        _render_audit(store)


def _render_planner_pref(store: MemoryStore, prefs) -> None:
    st.subheader(t("memory.planner.header"))
    st.caption(t("memory.planner.caption"))

    new_value = st.toggle(
        t("memory.planner.toggle"),
        value=prefs.prefer_llm_planner,
        key="memory_prefer_llm_toggle",
    )
    st.caption(t("memory.planner.tradeoff"))

    if new_value != prefs.prefer_llm_planner:
        result = store.set_prefer_llm_planner(new_value)
        if result.changed:
            if new_value:
                st.success(t("memory.planner.saved_on"))
            else:
                st.success(t("memory.planner.saved_off"))
            st.rerun()


def _render_forbidden_paths(store: MemoryStore, prefs) -> None:
    st.subheader(t("memory.forbidden.header"))
    st.caption(t("memory.forbidden.caption"))

    if prefs.forbidden_paths:
        for p in prefs.forbidden_paths:
            cols = st.columns([5, 1])
            cols[0].code(p, language="text")
            if cols[1].button(
                "🗑", key=f"remove_{p}", help=t("memory.forbidden.remove_help", path=p)
            ):
                try:
                    result = store.remove_forbidden_path(p)
                    if result.changed:
                        st.success(t("memory.forbidden.removed", path=p))
                        st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        st.info(t("memory.forbidden.empty"))

    st.divider()
    with st.form("add_forbidden"):
        new_path = st.text_input(
            t("memory.forbidden.add_label"),
            placeholder=t("memory.forbidden.add_placeholder"),
            help=t("memory.forbidden.add_help"),
        )
        if st.form_submit_button(t("memory.forbidden.add_button")):
            if not new_path.strip():
                st.warning(t("memory.forbidden.empty_input"))
            else:
                try:
                    result = store.add_forbidden_path(new_path.strip())
                    if result.changed:
                        st.success(t("memory.forbidden.added", path=new_path))
                        st.rerun()
                    else:
                        st.info(t("memory.forbidden.already", path=new_path))
                except ValueError as exc:
                    st.error(str(exc))


def _render_naming_style(store: MemoryStore, prefs) -> None:
    st.subheader(t("memory.naming.header"))
    st.caption(t("memory.naming.caption"))

    current = prefs.naming_style.value
    styles = [s.value for s in NamingStyle]
    new_style = st.radio(
        t("memory.naming.style_label"),
        options=styles,
        index=styles.index(current),
        help=t("memory.naming.style_help"),
        horizontal=True,
    )

    if new_style != current:
        if st.button(
            t("memory.naming.save_button", old=current, new=new_style),
            type="primary",
        ):
            try:
                result = store.set_naming_style(new_style)
                if result.changed:
                    st.success(result.detail)
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if current != NamingStyle.ORIGINAL.value:
        if st.button(t("memory.naming.reset_button")):
            store.clear_naming_style()
            st.rerun()

    with st.expander(t("memory.naming.examples_expander")):
        from app.memory.naming import apply_naming_style

        examples = [
            "Report (Final).pdf",
            "MY NOTES.txt",
            "data set v2.csv",
            "Q1+Q2 results.xlsx",
        ]
        rows = []
        original_col = t("memory.naming.col_original")
        for ex in examples:
            row = {original_col: ex}
            for s in styles:
                row[s] = apply_naming_style(ex, s)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_audit(store: MemoryStore) -> None:
    st.subheader(t("memory.audit.header"))
    st.caption(t("memory.audit.caption", path=store.audit_path))

    limit = st.slider(t("memory.audit.slider"), min_value=10, max_value=200, value=50)
    entries = store.read_audit(limit=limit)
    if not entries:
        st.info(t("memory.audit.empty"))
        return

    # Render newest-first.
    rows = []
    for e in reversed(entries):
        rows.append(
            {
                t("memory.audit.col_ts"): e.get("ts", ""),
                t("memory.audit.col_event"): e.get("event", ""),
                t("memory.audit.col_key"): e.get("path") or e.get("key") or "",
                t("memory.audit.col_before"): _stringify(e.get("before")),
                t("memory.audit.col_after"): _stringify(e.get("after")),
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
