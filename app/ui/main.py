"""Streamlit entry point — landing page.

The Plan / Execute / Rollback / Memory pages live under ``pages/``
and Streamlit's multi-page convention puts them in the sidebar nav
automatically.
"""

from __future__ import annotations

import streamlit as st

from app.ui._i18n import t
from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)


def main() -> None:
    configure_page("app.page_title.home", icon="🌀")
    render_header("app.page_title.home", subtitle_key="app.subtitle.home")
    render_unsafe_banner()
    workspace = render_sandbox_sidebar()

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
                "",
                t("home.workspace.header"),
            ]
        )
    )

    if workspace is not None:
        st.success(t("home.active_workspace", path=workspace))
        files = [p for p in workspace.rglob("*") if p.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        col1, col2 = st.columns(2)
        col1.metric(t("home.metric.files"), f"{len(files)}")
        col2.metric(t("home.metric.size"), _fmt_size(total_bytes))
    else:
        st.info(t("home.pick_workspace"))

    st.divider()
    st.caption(t("home.footer"))


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n //= 1024
    return f"{n:,.1f} TB"


if __name__ == "__main__":
    main()
