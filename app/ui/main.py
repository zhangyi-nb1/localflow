"""Streamlit entry point — product landing page.

v0.22 (Lane A-home): landing page lead with a hero + three featured
deliverable packs (research / data report / project handoff). The
existing Plan / Execute / Rollback / Memory pages still live under
``pages/`` and the user can drop into the manual lifecycle from the
"Or take manual control" section below the pack chooser.

Why packs-first: the productisation guide §5.1 says new users land on
"pick a deliverable" rather than "pick a skill". The Pack page is
where the full chooser lives; this Home is the front door that
points the eye at the three flagship outcomes.
"""

from __future__ import annotations

import streamlit as st

from app.recipes import get_default_registry
from app.schemas import RecipeSpec
from app.ui._i18n import t
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)

# The three flagship packs we feature on the landing page. Order
# matches the productisation guide §8 ranking (Research is the
# canonical demo).
_FEATURED_PACKS: tuple[str, ...] = (
    "research_pack",
    "data_report_pack",
    "project_handoff_pack",
)

_PACK_SELECT_KEY = "_home_pack_select"


def main() -> None:
    configure_page("app.page_title.home", icon="🌀")
    render_header("app.page_title.home", subtitle_key="app.subtitle.home")
    render_unsafe_banner()
    workspace = render_sandbox_sidebar()

    st.markdown(f"#### {t('home.hero.tagline')}")
    st.divider()

    _render_pack_chooser(workspace)
    st.divider()

    _render_workspace_summary(workspace)
    st.divider()

    _render_manual_section()
    st.divider()
    st.caption(t("home.footer"))


def _render_pack_chooser(workspace) -> None:
    st.markdown(t("home.packs.heading"))
    st.caption(t("home.packs.subheading"))

    registry = get_default_registry()
    if workspace is None:
        st.info(t("home.packs.no_workspace"))

    cols = st.columns(len(_FEATURED_PACKS))
    for col, pack_name in zip(cols, _FEATURED_PACKS):
        with col:
            try:
                recipe: RecipeSpec = registry.get(pack_name)
            except Exception:
                st.markdown(t("home.packs.unavailable", name=pack_name))
                continue
            _render_pack_card(recipe, workspace_ready=workspace is not None)


def _render_pack_card(recipe: RecipeSpec, *, workspace_ready: bool) -> None:
    with st.container(border=True):
        st.markdown(f"#### {recipe.title}")
        # First paragraph of the description — keeps the card scannable.
        first_para = recipe.description.strip().split("\n\n", 1)[0]
        st.markdown(first_para)
        if recipe.tags:
            tag_line = " ".join(f"`{tag}`" for tag in recipe.tags)
            st.caption(tag_line)
        clicked = st.button(
            t("home.packs.try_button", title=recipe.title),
            key=f"home_pack_try_{recipe.name}",
            type="primary",
            disabled=not workspace_ready,
            use_container_width=True,
        )
        if clicked:
            st.session_state[_PACK_SELECT_KEY] = recipe.name
            st.switch_page("pages/0_Pack.py")


def _render_workspace_summary(workspace) -> None:
    st.markdown(t("home.workspace.header"))
    if workspace is None:
        st.info(t("home.pick_workspace"))
        return
    st.success(t("home.active_workspace", path=workspace))
    files = [p for p in workspace.rglob("*") if p.is_file()]
    total_bytes = sum(f.stat().st_size for f in files)
    col1, col2 = st.columns(2)
    col1.metric(t("home.metric.files"), f"{len(files)}")
    col2.metric(t("home.metric.size"), _fmt_size(total_bytes))


def _render_manual_section() -> None:
    st.markdown(t("home.advanced.heading"))
    st.markdown(
        "\n".join(
            [
                t("home.intro"),
                "",
                t("home.table.header"),
                t("home.table.divider"),
                t("home.table.plan"),
                t("home.table.execute"),
                t("home.table.rollback"),
                t("home.table.memory"),
            ]
        )
    )


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n //= 1024
    return f"{n:,.1f} TB"


if __name__ == "__main__":
    main()
