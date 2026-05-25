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
SESSION_UNSAFE_KEY = "unsafe_mode_enabled"  # sticky across page navigations


def _resolve_unsafe() -> bool:
    """Return True if unsafe mode is active for this session.

    Streamlit's multi-page navigation **drops URL query parameters** —
    so a user who lands on ``/?unsafe=1``, picks a custom workspace,
    and then clicks "Plan" in the sidebar lands on ``/Plan`` with
    ``unsafe`` no longer in the URL. Without persistence the custom
    workspace silently reverts to a sandbox subdir.

    Fix: latch the bit. Once ``?unsafe=1`` is seen in any page's URL
    during this Streamlit session, it stays enabled until the tab is
    closed (session_state is per-tab). Opening a fresh tab without
    ``?unsafe=1`` still starts in safe mode — this only stops a single
    session from accidentally losing its opt-in.
    """
    if get_unsafe_mode_from_query(dict(st.query_params)):
        st.session_state[SESSION_UNSAFE_KEY] = True
        return True
    return bool(st.session_state.get(SESSION_UNSAFE_KEY, False))


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
    """Show the yellow banner if unsafe mode is active for this session
    (either ``?unsafe=1`` in the current URL OR previously enabled in
    the same session). Returns the resolved bool for downstream use."""
    unsafe = _resolve_unsafe()
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
    unsafe = _resolve_unsafe()
    _preseed_default_workspace()

    # v0.22.x — Streamlit's auto-generated nav reads labels straight
    # from the page filenames ("0_Create_Pack" → "Create Pack") with
    # no i18n hook. In zh mode this leaves a row of English page
    # links visible above our translated controls. Hide it via CSS
    # and render our own translated nav below.
    _hide_streamlit_autonav()

    with st.sidebar:
        # Language toggle first — affects every label rendered below.
        render_language_toggle()
        st.divider()

        _render_translated_nav()
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


def _preseed_default_workspace() -> None:
    """Make the first sidebar render agree with the picker default.

    Streamlit renders top-to-bottom. Without this seed, the active
    workspace badge can say "none selected" on first load while the
    selectbox below has already defaulted to a sandbox workspace.
    """
    if st.session_state.get(SESSION_WORKSPACE_KEY):
        return
    if st.session_state.get(SESSION_WS_SOURCE_KEY, "sandbox") != "sandbox":
        return
    try:
        first = next(iter(find_eligible_workspace_choices(unsafe_mode=False)), None)
    except Exception:
        first = None
    if first is not None:
        st.session_state[SESSION_WORKSPACE_KEY] = str(first)


def _hide_streamlit_autonav() -> None:
    """Inject CSS that hides Streamlit's auto-generated sidebar page
    list. Without this, every page (Create Pack / Workspace / Runs /
    Settings / Plan / Execute / Rollback) is listed by its filename-
    derived English label even when the user has switched to zh.

    Idempotent — Streamlit re-renders on every interaction and
    duplicating the ``<style>`` block costs nothing visually.
    """
    st.markdown(
        """
        <style>
          /* Streamlit has moved the auto-nav between section/div/nav
             wrappers across releases; hide every known wrapper/item
             shape so only LocalFlow's translated nav remains. */
          section[data-testid="stSidebarNav"],
          div[data-testid="stSidebarNav"],
          nav[data-testid="stSidebarNav"],
          [data-testid="stSidebarNav"],
          ul[data-testid="stSidebarNavItems"],
          div[data-testid="stSidebarNavItems"],
          [data-testid="stSidebarNavItems"] {
            display: none !important;
            height: 0 !important;
            overflow: hidden !important;
            visibility: hidden !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


# Page registry for the translated sidebar nav. Each entry is
# ``(page_path_relative_to_entrypoint, i18n_label_key, icon)``.
# The entrypoint is ``app/ui/main.py`` so the home link is
# ``main.py`` and the rest live under ``pages/``. Order matches the
# previous Streamlit auto-nav so muscle memory survives the switch.
_NAV_PAGES: tuple[tuple[str, str, str], ...] = (
    ("main.py", "app.page_title.home", "🌀"),
    ("pages/0_Create_Pack.py", "app.page_title.pack", "📦"),
    ("pages/1_Workspace.py", "app.page_title.workspace", "🗂️"),
    ("pages/3_Runs.py", "app.page_title.runs", "📋"),
    ("pages/4_Settings.py", "app.page_title.settings", "⚙️"),
    ("pages/5_Plan.py", "app.page_title.plan", "🧭"),
    ("pages/6_Execute.py", "app.page_title.execute", "⚡"),
    ("pages/7_Rollback.py", "app.page_title.rollback", "↩️"),
)


def _render_translated_nav() -> None:
    """Render our own sidebar nav using ``st.page_link`` so the labels
    flow through :func:`t`. Caller must already be inside the
    ``with st.sidebar:`` context — :func:`render_sandbox_sidebar`
    invokes this from the top of its sidebar block.

    Falls back to skipping a particular link (sidebar still works) if
    a page module can't be resolved — e.g. a user vendoring the
    package and stripping pages.
    """
    for path, label_key, icon in _NAV_PAGES:
        try:
            st.page_link(path, label=t(label_key), icon=icon)
        except Exception:
            # Streamlit raises if the target file is missing; skip
            # silently so a partial install still renders a sidebar.
            continue


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
    if prefs.prefer_llm_planner:
        st.caption(t("sidebar.memory.prefer_llm"))


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
