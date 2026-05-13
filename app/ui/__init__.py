"""Phase 8.0 / v0.7.0 — Streamlit UI MVP.

A localhost-only browser UI for the LocalFlow harness. The UI is a
**driver** — same layer as the CLI and the MCP server. It calls into
``app.harness.control_loop`` and other shared helpers; it never adds
new actions or new safety primitives.

Safety posture:
  * Defaults to binding ``127.0.0.1`` (the CLI ``ui-serve`` command
    requires explicit ``--host 0.0.0.0`` to expose).
  * Workspace selector is **soft-sandboxed** to ``<cwd>/sandbox/`` by
    default. The user can lift this by visiting the UI with
    ``?unsafe=1`` in the URL, which surfaces a prominent banner.
  * The kernel's ``policy_guard.resolve_inside`` + ``forbidden_paths``
    still enforce the real workspace boundary — UI sandboxing is the
    SECOND line, not the first.

See [docs/UI.md](../../docs/UI.md) for the user guide.
"""

from pathlib import Path


def main_path() -> Path:
    """Return the absolute path of the Streamlit entry script.

    Used by ``localflow ui-serve`` (which spawns
    ``python -m streamlit run <main_path()>``).
    """
    return Path(__file__).parent / "main.py"


__all__ = ["main_path"]
