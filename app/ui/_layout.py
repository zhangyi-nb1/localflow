"""Shared Streamlit layout components — sidebar, banner, badges.

Imports streamlit; only call from page scripts (not from CLI or tests
without a Streamlit runtime).

Phase 8.1 (v0.8.0) rewrite:
  * Sidebar now uses a **radio** for workspace source (Sandbox subdir
    vs Custom path), surfacing the custom-path input prominently
    rather than buried in a collapsed expander. The previous layout
    (dropdown + collapsed expander) silently fought for the user's
    selection — see docs/PHASES.md → Phase 8.1.
  * **Active workspace** badge sits at the top so every page reads
    from one obvious place.
  * Every UI string flows through :func:`app.ui._i18n.t` so the
    language toggle at the top of the sidebar switches the whole app.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.memory import MemoryStore, MemoryStoreError
from app.ui._i18n import render_language_toggle, t
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
SESSION_WS_SOURCE_KEY = "ws_source_mode"  # "sandbox" | "custom"


def configure_page(title_key: str, icon: str = "🌀") -> None:
    """Standard page config — call once per page at the top.

    ``title_key`` is an i18n key (e.g. ``"app.page_title.plan"``);
    the resolved title is what appears in the browser tab.
    """
    title = t(title_key)
    st.set_page_config(
        page_title=f"LocalFlow · {title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_header(title_key: str, subtitle_key: str | None = None) -> None:
    title = t(title_key)
    st.title(t("app.header_prefix", title=title))
    if subtitle_key:
        st.caption(t(subtitle_key))


def render_unsafe_banner() -> bool:
    """Show the yellow banner if ``?unsafe=1`` is set. Returns the
    parsed unsafe-mode bool for the page to use downstream."""
    unsafe = get_unsafe_mode_from_query(dict(st.query_params))
    if unsafe:
        st.warning(t("unsafe.banner"), icon="⚠️")
    return unsafe


def render_sandbox_sidebar() -> Path | None:
    """Sidebar workspace picker. Returns the selected absolute Path or
    None if the user hasn't picked one yet.

    Side effects:
      * Renders the language toggle at the top of the sidebar.
      * Stores the chosen workspace in
        ``st.session_state[SESSION_WORKSPACE_KEY]``.
      * Stores the source mode (sandbox vs custom) in
        ``st.session_state[SESSION_WS_SOURCE_KEY]`` so it persists
        across page switches.
    """
    unsafe = get_unsafe_mode_from_query(dict(st.query_params))

    with st.sidebar:
        # Language toggle first — affects every label rendered below.
        render_language_toggle()
        st.divider()

        st.header(t("sidebar.workspace.header"))

        # Active-workspace badge: always visible, computed from session.
        active_raw = st.session_state.get(SESSION_WORKSPACE_KEY)
        if active_raw:
            st.markdown(f"{t('sidebar.workspace.active_label')} `{active_raw}`")
        else:
            st.markdown(
                f"{t('sidebar.workspace.active_label')} {t('sidebar.workspace.none_active')}"
            )
        st.caption(
            t(
                "sidebar.workspace.sandbox_root_caption",
                path=humanize_path_relative(sandbox_root()),
            )
        )

        # Source radio — sandbox vs custom path.
        selected = _render_source_radio(unsafe)

        if st.button(t("sidebar.refresh"), help=t("sidebar.refresh_help")):
            st.rerun()

        st.divider()
        _render_memory_summary()

    # Persist the choice. Only overwrite when the radio produced a
    # value — otherwise leave the prior selection so a page change
    # doesn't blow it away.
    if selected is not None:
        st.session_state[SESSION_WORKSPACE_KEY] = str(selected)
    return selected


def _render_source_radio(unsafe: bool) -> Path | None:
    """Render the workspace-source radio and the input for the
    selected source. Returns the resolved absolute Path, or None.

    When ``unsafe`` is False we hide the Custom-path option from the
    radio entirely — Streamlit doesn't natively allow disabling a
    single radio option, and showing it greyed-out is more confusing
    than not showing it at all. A caption explains how to enable it.
    """
    option_sandbox = t("sidebar.workspace.source_sandbox")
    option_custom = t("sidebar.workspace.source_custom")

    options = [option_sandbox]
    if unsafe:
        options.append(option_custom)

    # Determine the default index from the persisted source mode.
    persisted_mode = st.session_state.get(SESSION_WS_SOURCE_KEY, "sandbox")
    default_index = 0
    if persisted_mode == "custom" and unsafe:
        default_index = 1

    chosen = st.radio(
        t("sidebar.workspace.source_label"),
        options=options,
        index=default_index,
        key="ws_source_radio",
    )
    if not unsafe:
        st.caption(t("sidebar.workspace.custom_locked"))

    if chosen == option_sandbox:
        st.session_state[SESSION_WS_SOURCE_KEY] = "sandbox"
        return _render_sandbox_picker()
    # Custom path branch.
    st.session_state[SESSION_WS_SOURCE_KEY] = "custom"
    return _render_custom_picker()


def _render_sandbox_picker() -> Path | None:
    """Dropdown for choosing a sandbox subdir as the workspace."""
    choices = list(find_eligible_workspace_choices(unsafe_mode=False))
    choice_labels = [humanize_path_relative(p) for p in choices]
    if not choices:
        st.info(t("sidebar.workspace.no_choices"))
        return None

    selected_label = st.selectbox(
        t("sidebar.workspace.pick_label"),
        options=choice_labels,
        index=_default_index(choices),
        key="sb_workspace_select",
        help=t("sidebar.workspace.pick_help"),
    )
    if selected_label is None:
        return None
    for p, label in zip(choices, choice_labels):
        if label == selected_label:
            return p
    return None


def _render_custom_picker() -> Path | None:
    """Free-form absolute-path input. Validation happens live and
    surfaces directly below the input."""
    custom = st.text_input(
        t("sidebar.workspace.custom_label"),
        key="sb_custom_path",
        placeholder=t("sidebar.workspace.custom_placeholder"),
        value=_seed_custom_input(),
    )
    if not custom.strip():
        st.caption(t("sidebar.workspace.custom_empty_caption"))
        return None
    try:
        candidate = validate_workspace(custom.strip(), unsafe_mode=True)
    except ValueError as exc:
        st.error(str(exc))
        return None
    st.success(t("sidebar.workspace.custom_ok", path=candidate))
    return candidate


def _seed_custom_input() -> str:
    """If the active workspace is outside sandbox, default the input
    to that value so a page reload doesn't drop the user back to
    'empty'."""
    raw = st.session_state.get(SESSION_WORKSPACE_KEY)
    if not raw:
        return ""
    try:
        p = Path(raw).resolve()
        root = sandbox_root().resolve()
        p.relative_to(root)
        # Inside sandbox — don't pollute the custom-path input.
        return ""
    except ValueError:
        # Outside sandbox — this is exactly the value we want pre-filled.
        return str(raw)


def _default_index(choices: list[Path]) -> int:
    """Pick the previously-selected workspace if still valid, else 0."""
    prior = st.session_state.get(SESSION_WORKSPACE_KEY)
    if prior:
        try:
            prior_abs = Path(prior).resolve()
            for i, p in enumerate(choices):
                if p.resolve() == prior_abs:
                    return i
        except (OSError, RuntimeError):
            pass
    return 0


def _render_memory_summary() -> None:
    """Compact memory status in the sidebar."""
    try:
        prefs = MemoryStore().load()
    except MemoryStoreError as exc:
        st.error(t("sidebar.memory.error", err=str(exc)))
        return

    st.subheader(t("sidebar.memory.header"))
    if prefs.is_default():
        st.caption(t("sidebar.memory.all_default"))
        return
    if prefs.forbidden_paths:
        st.caption(t("sidebar.memory.forbidden_count", n=len(prefs.forbidden_paths)))
    if prefs.naming_style.value != "original":
        st.caption(t("sidebar.memory.naming_style", value=prefs.naming_style.value))


def risk_badge(risk_level: str) -> str:
    """Markdown-friendly colored risk badge."""
    color = {"low": "🟢", "medium": "🟡", "high": "🔴", "blocked": "⛔"}.get(
        risk_level.lower(), "⚪"
    )
    return f"{color} **{risk_level.upper()}**"


def status_badge(passed: bool, label_ok: str | None = None, label_fail: str | None = None) -> str:
    """Translated status badge. Labels default to PASSED / FAILED but
    callers can pass explicit i18n-resolved overrides (e.g. CLEAN /
    CONFLICTS for the rollback page)."""
    ok = label_ok if label_ok is not None else t("common.status.passed")
    fail = label_fail if label_fail is not None else t("common.status.failed")
    return f"✅ **{ok}**" if passed else f"❌ **{fail}**"


def require_workspace() -> Path:
    """Pages call this to read the session workspace. Stop the page
    with a clear message if not set yet."""
    raw = st.session_state.get(SESSION_WORKSPACE_KEY)
    if not raw:
        st.warning(t("common.workspace_warning"))
        st.stop()
    return Path(raw)
