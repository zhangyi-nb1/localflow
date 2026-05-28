"""Settings page (v0.22, C-nav: renamed from Memory) — edit
forbidden_paths + naming_style, browse audit."""

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
    configure_page("app.page_title.settings", icon="⚙")
    render_header("app.page_title.settings", "memory.subtitle")
    render_unsafe_banner()
    render_sandbox_sidebar()

    store = MemoryStore()
    try:
        prefs = store.load()
    except MemoryStoreError as exc:
        st.error(t("memory.error.store", err=str(exc)))
        return

    tab1, tab2, tab3, tab4, tab_backend, tab5 = st.tabs(
        [
            t("memory.tab.forbidden"),
            t("memory.tab.naming"),
            t("memory.tab.planner"),
            t("memory.tab.semantic"),
            # Phase 34.2 — F-3 fix. New tab for Workspace backend.
            "🛰 Workspace backend",
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
        _render_semantic_pref(store, prefs)

    with tab_backend:
        _render_workspace_backend(store, prefs)

    with tab5:
        _render_audit(store)


def _render_workspace_backend(store: MemoryStore, prefs) -> None:
    """Phase 34.2 — F-3 fix. UI exposure of the four Workspace
    backends Phases 28-33 built (Local / Docker / Remote / +
    AgentServer mode opt-in for Docker + Remote). Previously
    reachable only via the CLI ``--workspace`` flag; now persists
    into ``memory.workspace_backend_spec`` for every UI page to
    consume on executor wire-up.
    """
    st.subheader("Workspace backend")
    st.caption(
        "Pick which Workspace Protocol backend the UI uses to plan / "
        "execute / verify / rollback. Defaults to `local` (host fs). "
        "Mirrors the CLI `--workspace` flag."
    )

    current_spec = prefs.workspace_backend_spec or "local"

    # Parse the current spec to set the right radio + populate fields.
    if current_spec == "local" or current_spec == "":
        current_kind = "local"
        current_image = "python:3.12-slim"
        current_host = ""
        current_ssh_port = 22
        current_ssh_root = ""
    elif current_spec.startswith("docker:"):
        current_kind = "docker"
        current_image = current_spec[len("docker:") :].strip() or "python:3.12-slim"
        current_host = ""
        current_ssh_port = 22
        current_ssh_root = ""
    elif current_spec.startswith("ssh:"):
        current_kind = "ssh"
        body = current_spec[len("ssh:") :].strip()
        current_image = "python:3.12-slim"
        # Reuse the same right-to-left parse as parse_workspace_spec.
        current_ssh_root = ""
        current_ssh_port = 22
        if ":/" in body:
            head, _, root_part = body.rpartition(":/")
            current_ssh_root = "/" + root_part
            body = head
        if ":" in body:
            head, _, last = body.rpartition(":")
            try:
                current_ssh_port = int(last)
                body = head
            except ValueError:
                pass
        current_host = body
    else:
        current_kind = "local"
        current_image = "python:3.12-slim"
        current_host = ""
        current_ssh_port = 22
        current_ssh_root = ""

    kind = st.radio(
        "Backend",
        options=["local", "docker", "ssh"],
        index=["local", "docker", "ssh"].index(current_kind),
        horizontal=True,
        help=(
            "**local** = host fs (default; ~10μs/op). "
            "**docker** = container-isolated (Phase 29; ~100-300ms/op, "
            "or ~5-20ms with use_agent_server). "
            "**ssh** = remote Linux host (Phase 31; ~100-300ms/op + RTT)."
        ),
        key="settings_workspace_kind",
    )

    new_spec = "local"
    if kind == "local":
        st.code("local", language="text")
        new_spec = "local"
    elif kind == "docker":
        image = st.text_input(
            "Docker image",
            value=current_image,
            help="Any OCI image with `sh` + `coreutils` + `python3`.",
            key="settings_workspace_docker_image",
        )
        new_spec = f"docker:{image.strip()}"
        st.code(new_spec, language="text")
    elif kind == "ssh":
        c1, c2 = st.columns([2, 1])
        host = c1.text_input(
            "SSH host (user@host or ~/.ssh/config alias)",
            value=current_host,
            placeholder="bob@example.com",
            key="settings_workspace_ssh_host",
        )
        port = c2.number_input(
            "Port",
            min_value=1,
            max_value=65535,
            value=int(current_ssh_port),
            step=1,
            key="settings_workspace_ssh_port",
        )
        root = st.text_input(
            "Remote workspace root (must start with /)",
            value=current_ssh_root,
            placeholder="/tmp/localflow-ws",
            help="Absolute path on the remote. Defaults to /tmp/localflow-ws.",
            key="settings_workspace_ssh_root",
        )
        parts = [host.strip()] if host.strip() else []
        if parts:
            if int(port) != 22:
                parts.append(str(int(port)))
            if root.strip():
                if not root.strip().startswith("/"):
                    st.warning("Remote root must start with `/` — defaulting to /tmp/localflow-ws.")
                else:
                    parts.append(root.strip())
            new_spec = "ssh:" + ":".join(parts)
        else:
            new_spec = "ssh:"
            st.warning("Enter an SSH host to enable the remote backend.")
        st.code(new_spec, language="text")

    st.divider()

    if new_spec != current_spec:
        col_save, col_help = st.columns([1, 3])
        if col_save.button(
            f"Save backend: `{current_spec}` → `{new_spec}`",
            type="primary",
            key="settings_workspace_save",
        ):
            try:
                result = store.set_workspace_backend_spec(new_spec)
                if result.changed:
                    st.success(result.detail)
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        col_help.caption(
            "The kernel boundary doesn't change — only the Workspace facade "
            "the executor uses. Each backend's quirks are documented in "
            "`docs/{WORKSPACE,DOCKER_WORKSPACE,REMOTE_WORKSPACE}.md`."
        )
    else:
        st.info(f"Current backend is **`{current_spec}`** (no pending change).")


def _render_semantic_pref(store: MemoryStore, prefs) -> None:
    """Phase 13 — toggle for the semantic verifier + auto-repair loop."""
    st.subheader(t("memory.semantic.header"))
    st.caption(t("memory.semantic.caption"))

    enable_new = st.toggle(
        t("memory.semantic.enable_toggle"),
        value=prefs.enable_semantic_verifier,
        key="memory_enable_semantic_toggle",
    )
    st.caption(t("memory.semantic.enable_tradeoff"))
    if enable_new != prefs.enable_semantic_verifier:
        result = store.set_enable_semantic_verifier(enable_new)
        if result.changed:
            if enable_new:
                st.success(t("memory.semantic.enable_saved_on"))
            else:
                st.success(t("memory.semantic.enable_saved_off"))
            st.rerun()

    st.divider()
    st.markdown(f"**{t('memory.semantic.max_label')}**")
    new_max = st.slider(
        t("memory.semantic.max_slider"),
        min_value=0,
        max_value=5,
        value=prefs.max_auto_repairs,
        key="memory_max_auto_repairs_slider",
    )
    st.caption(t("memory.semantic.max_help"))
    if new_max != prefs.max_auto_repairs:
        if st.button(
            t("memory.semantic.max_save", old=prefs.max_auto_repairs, new=new_max),
            type="primary",
        ):
            result = store.set_max_auto_repairs(int(new_max))
            if result.changed:
                st.success(result.detail)
                st.rerun()


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
