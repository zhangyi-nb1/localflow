"""Streamlit entry point — landing page.

The Plan / Execute / Rollback / Memory pages live under ``pages/``
and Streamlit's multi-page convention puts them in the sidebar nav
automatically.
"""

from __future__ import annotations

import streamlit as st

from app.ui._layout import (
    configure_page,
    render_header,
    render_sandbox_sidebar,
    render_unsafe_banner,
)


def main() -> None:
    configure_page("Home", icon="🌀")
    render_header(
        "Home",
        subtitle="Safe execution harness for LLM agents on local workspaces.",
    )
    render_unsafe_banner()
    workspace = render_sandbox_sidebar()

    st.markdown(
        """
The LLM proposes; the harness disposes. Use the sidebar pages to
walk a workspace through the lifecycle:

```
  Plan  →  Execute  →  Rollback
            (with dry-run + approval)   (with hash-drift guard)
```

| Page | What it does |
|---|---|
| **📋 Plan** | Pick a skill, write a goal, see the structured ActionPlan. |
| **🔍 Execute** | Render dry-run, approve, commit. Verifier runs automatically. |
| **↺ Rollback** | Preview each reverse op + drift detection. `--force` to override. |
| **⚙ Memory** | Edit `forbidden_paths` + `naming_style`. Audit log. |

### Workspace
"""
    )

    if workspace is not None:
        st.success(f"Active workspace: `{workspace}`")
        # Show a quick file count + total size.
        files = [p for p in workspace.rglob("*") if p.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        col1, col2 = st.columns(2)
        col1.metric("Files", f"{len(files)}")
        col2.metric("Total size", _fmt_size(total_bytes))
    else:
        st.info("Pick a workspace in the sidebar to begin.")

    st.divider()
    st.caption(
        "Driver layer — same backend as CLI + MCP. "
        "Every action passes through `policy_guard`, `dry_run`, "
        "approval, executor, verifier, rollback, audit."
    )


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n //= 1024
    return f"{n:,.1f} TB"


if __name__ == "__main__":
    main()
