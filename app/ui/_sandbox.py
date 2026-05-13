"""Soft-sandbox helpers for the Streamlit UI.

Defaults the UI's workspace picker to ``<cwd>/sandbox/`` subdirectories.
The user explicitly opts out by visiting the UI with ``?unsafe=1`` in
the URL — at which point a yellow banner surfaces in the layout.

This module is **independent of Streamlit** at import time so it can be
unit-tested without a browser runtime. Streamlit is only needed by
``get_unsafe_mode()`` (which guards the import) and by callers in the
``pages/`` directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def sandbox_root(cwd: Path | None = None) -> Path:
    """Resolve the soft-sandbox root.

    Always ``<cwd>/sandbox/``. We deliberately don't honor an env var
    override here — the UI sandbox is meant to be obviously visible
    (a directory next to where you launched ``ui-serve``), not a
    hidden config knob.
    """
    base = (cwd or Path.cwd()).resolve()
    return base / "sandbox"


def is_inside_sandbox(path: Path, *, cwd: Path | None = None) -> bool:
    """True iff ``path`` resolves to something at-or-under sandbox_root.

    Returns False for non-existent paths (you can't claim "this path
    inside sandbox" before it exists — the workspace must be real).
    """
    root = sandbox_root(cwd)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    if not resolved.exists():
        return False
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def list_sandbox_workspaces(cwd: Path | None = None) -> list[Path]:
    """Return all direct subdirectories of ``sandbox/`` as workspace
    candidates. Empty list if ``sandbox/`` doesn't exist or has no
    subdirs.

    The sandbox root itself is NOT returned as a workspace — having
    every demo directly under sandbox/ is the expected layout, but
    using the sandbox root as the workspace would mean a single
    cluttered top-level. We return only subdirs to nudge the user
    toward organized per-task workspaces.
    """
    root = sandbox_root(cwd)
    if not root.exists() or not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()])


def validate_workspace(
    candidate: str | Path,
    *,
    unsafe_mode: bool,
    cwd: Path | None = None,
) -> Path:
    """Return the resolved absolute Path or raise ``ValueError``.

    Rules:
      * ``candidate`` must resolve to an existing directory.
      * If ``unsafe_mode`` is False, the resolved path must be under
        ``sandbox_root()``. Outside-sandbox candidates raise.
      * If ``unsafe_mode`` is True, any existing directory is accepted.
        The harness's own ``policy_guard`` is still the real boundary
        for actions inside the workspace.
    """
    path = Path(candidate).expanduser()
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve workspace path: {candidate!r} ({exc})") from exc
    if not resolved.exists():
        raise ValueError(f"workspace does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"workspace must be a directory, not a file: {resolved}")
    if not unsafe_mode and not is_inside_sandbox(resolved, cwd=cwd):
        raise ValueError(
            f"workspace {resolved} is outside the soft sandbox "
            f"({sandbox_root(cwd)}). Visit the UI with ?unsafe=1 to "
            f"lift this restriction."
        )
    return resolved


def get_unsafe_mode_from_query(query_params: dict) -> bool:
    """Parse ``?unsafe=1`` from Streamlit's query_params dict.

    Accepts the usual truthy values for robustness across the various
    representations Streamlit has used over versions.
    """
    raw = query_params.get("unsafe", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def humanize_path_relative(path: Path, *, cwd: Path | None = None) -> str:
    """Render ``path`` relative to cwd if it's under cwd, else as-is.

    Cosmetic — used in the UI to keep workspace labels short.
    """
    try:
        return str(path.relative_to((cwd or Path.cwd()).resolve()))
    except ValueError:
        return str(path)


def find_eligible_workspace_choices(
    *, unsafe_mode: bool, cwd: Path | None = None
) -> Iterable[Path]:
    """Yield the workspace choices the UI should show in the dropdown.

    Default (unsafe=False): all subdirs of sandbox/.
    Unsafe (unsafe=True): same subdirs PLUS a sentinel <CUSTOM_PATH>
    handled by the caller (we just provide the safe set; the page
    layer surfaces a free-text input for the custom case).
    """
    return list_sandbox_workspaces(cwd=cwd)
