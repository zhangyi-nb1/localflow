"""Shared Streamlit layout components — sidebar, banner, badges.

Imports streamlit; only call from page scripts (not from CLI or tests
without a Streamlit runtime).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.memory import MemoryStore, MemoryStoreError
from app.ui._sandbox import (
    find_eligible_workspace_choices,
    get_unsafe_mode_from_query,
    humanize_path_relative,
    sandbox_root,
    validate_workspace,
)

SESSION_WORKSPACE_KEY = "current_workspace"
SESSION_TASK_KEY = "current_task_id"
SESSION_TOKEN_KEY = "last_minted_token"
SESSION_DRY_RUN_KEY = "dry_run_markdown"


def configure_page(title: str, icon: str = "🌀") -> None:
    """Standard page config — call once per page at the top."""
    st.set_page_config(
        page_title=f"LocalFlow · {title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_header(title: str, subtitle: str | None = None) -> None:
    st.title(f"🌀 LocalFlow — {title}")
    if subtitle:
        st.caption(subtitle)


def render_unsafe_banner() -> bool:
    """Show the yellow banner if ``?unsafe=1`` is set. Returns the
    parsed unsafe-mode bool for the page to use downstream."""
    unsafe = get_unsafe_mode_from_query(dict(st.query_params))
    if unsafe:
        st.warning(
            "⚠️ **Unsafe path mode active.** The UI is allowing workspaces "
            "outside `./sandbox/`. The kernel's policy_guard + "
            "`forbidden_paths` still enforce real boundaries — but you've "
            "lifted the UI-level guard rail. To disable: remove `?unsafe=1` "
            "from the URL.",
            icon="⚠️",
        )
    return unsafe


def render_sandbox_sidebar() -> Path | None:
    """Sidebar workspace picker. Returns the selected absolute Path or
    None if the user hasn't picked one yet.

    Side effect: stores the choice in ``st.session_state[SESSION_WORKSPACE_KEY]``.
    """
    unsafe = get_unsafe_mode_from_query(dict(st.query_params))

    with st.sidebar:
        st.header("Workspace")
        st.caption(f"Sandbox root: `{humanize_path_relative(sandbox_root())}`")

        choices = list(find_eligible_workspace_choices(unsafe_mode=unsafe))
        choice_labels = [humanize_path_relative(p) for p in choices]
        if not choices:
            st.info(
                "No subdirectories under `sandbox/` yet. Create one "
                "(e.g. `mkdir sandbox/demo`) or use `?unsafe=1` in the "
                "URL to enter a custom path."
            )

        selected_label = st.selectbox(
            "Pick workspace",
            options=choice_labels,
            index=_default_index(choices) if choices else None,
            key="sb_workspace_select",
            help="Subdirectories of `./sandbox/`. Refresh after creating new ones.",
        )

        # Map back to absolute Path.
        selected: Path | None = None
        if selected_label is not None:
            for p, label in zip(choices, choice_labels):
                if label == selected_label:
                    selected = p
                    break

        # Custom path input — only enabled in unsafe mode.
        with st.expander("Custom path (unsafe)", expanded=False):
            if not unsafe:
                st.caption(
                    "Locked. To enable, reload with `?unsafe=1` at the end "
                    "of the URL — e.g. http://127.0.0.1:8501/?unsafe=1"
                )
            else:
                custom = st.text_input(
                    "Workspace absolute path",
                    key="sb_custom_path",
                    placeholder="C:\\path\\to\\your\\workspace",
                )
                if custom.strip():
                    try:
                        candidate = validate_workspace(custom.strip(), unsafe_mode=True)
                        selected = candidate
                        st.success(f"Using custom workspace: `{candidate}`")
                    except ValueError as exc:
                        st.error(str(exc))

        if st.button("🔄 Refresh", help="Re-scan sandbox/ for new subdirs"):
            st.rerun()

        st.divider()
        _render_memory_summary()

    # Persist the choice.
    if selected is not None:
        st.session_state[SESSION_WORKSPACE_KEY] = str(selected)
    return selected


def _default_index(choices: list[Path]) -> int:
    """Pick the previously-selected workspace if still valid, else 0."""
    prior = st.session_state.get(SESSION_WORKSPACE_KEY)
    if prior:
        prior_abs = Path(prior).resolve()
        for i, p in enumerate(choices):
            if p.resolve() == prior_abs:
                return i
    return 0


def _render_memory_summary() -> None:
    """Compact memory status in the sidebar."""
    try:
        prefs = MemoryStore().load()
    except MemoryStoreError as exc:
        st.error(f"Memory store error: {exc}")
        return

    st.subheader("Memory")
    if prefs.is_default():
        st.caption("All defaults — no preferences influencing runs.")
    else:
        if prefs.forbidden_paths:
            st.caption(f"🚫 {len(prefs.forbidden_paths)} forbidden_paths")
        if prefs.naming_style.value != "original":
            st.caption(f"📝 naming_style: `{prefs.naming_style.value}`")


def risk_badge(risk_level: str) -> str:
    """Markdown-friendly colored risk badge."""
    color = {"low": "🟢", "medium": "🟡", "high": "🔴", "blocked": "⛔"}.get(
        risk_level.lower(), "⚪"
    )
    return f"{color} **{risk_level.upper()}**"


def status_badge(passed: bool, label_ok: str = "PASSED", label_fail: str = "FAILED") -> str:
    return f"✅ **{label_ok}**" if passed else f"❌ **{label_fail}**"


def require_workspace() -> Path:
    """Pages call this to read the session workspace. Stop the page
    with a clear message if not set yet."""
    raw = st.session_state.get(SESSION_WORKSPACE_KEY)
    if not raw:
        st.warning("👈 Pick a workspace in the sidebar first. (Subdirectories of `./sandbox/`.)")
        st.stop()
    return Path(raw)
