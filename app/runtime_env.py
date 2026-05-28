"""Phase 36.x — optional ``.env`` auto-loading at the CLI / UI entry.

Background: LocalFlow reads credentials from ``os.environ`` only — it
never auto-loaded a ``.env`` file. A user who put ``OPENAI_API_KEY`` /
``ANTHROPIC_API_KEY`` in a project ``.env`` (a very common convention)
would find their LLM-backed runs silently degrading to the rule planner
/ lexical grounding / SKIPPED stages, because the var never reached the
process. This module closes that gap.

Design constraints:

  * **stdlib only** — no ``python-dotenv`` dependency.
  * **entry-point only** — called from the CLI root callback (and thus
    inherited by the ``ui-serve`` / ``mcp-serve`` subprocesses), NOT at
    library import time. Library imports stay env-free so the test
    suite stays deterministic + key-independent.
  * **real env wins** — uses ``os.environ.setdefault``; an explicitly
    exported variable is never overwritten by ``.env``.
  * **pytest-safe** — skips entirely under pytest so CliRunner-driven
    tests can't accidentally pick up a real client (which would break
    the deterministic "no LLM key" assertions across the suite).

§10.7: application-layer plumbing. No kernel import; not re-exported
through ``localflow_kernel``.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_project_dotenv", "find_dotenv"]


def find_dotenv() -> Path | None:
    """Locate a ``.env`` to load.

    Order: ``$CWD/.env`` (the convention — users run from the project
    root), then the repo root inferred from this file's location
    (``app/runtime_env.py`` → repo root is two parents up). Returns the
    first that exists, else None.
    """
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse one ``.env`` line into ``(key, value)`` or None to skip.

    Supports an optional ``export `` prefix, ignores blank / comment
    lines, and strips a single layer of matching surrounding quotes.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if not key:
        return None
    return key, value


def load_project_dotenv(*, force: bool = False) -> list[str]:
    """Load a project ``.env`` into ``os.environ`` (setdefault).

    Returns the list of keys that were newly set (already-present keys
    are left untouched). No-ops + returns ``[]`` when:
      * running under pytest (unless ``force=True``),
      * ``LOCALFLOW_NO_DOTENV`` is set truthy,
      * no ``.env`` is found,
      * the file can't be read.
    """
    # An explicit user opt-out is always honoured (even under force —
    # force only exists to bypass the pytest-skip guard for the
    # loader's own tests).
    if os.environ.get("LOCALFLOW_NO_DOTENV", "").strip().lower() in ("1", "true", "yes"):
        return []
    if not force and os.environ.get("PYTEST_CURRENT_TEST"):
        return []

    path = find_dotenv()
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    loaded: list[str] = []
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
