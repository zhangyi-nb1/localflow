"""Phase 4.1 — filesystem-based external skill discovery.

This is what turns LocalFlow from "tool with 4 hard-coded skills" into a
real **plug-in framework**: drop a skill folder into a known location,
LocalFlow finds it at startup, instantiates the ``Skill`` subclass, and
registers it next to the built-ins. The Harness Kernel, CLI, executor,
verifier — none of them change. Outline §10.7's extensibility rule taken
to its conclusion.

Search paths (in priority order):
  1. ``$LOCALFLOW_SKILLS_DIR`` — colon/semicolon-separated list of dirs
     (env var override, for power users / CI)
  2. ``<cwd>/.localflow/skills/`` — per-workspace skills
  3. ``~/.localflow/skills/`` — user-global skills

Each skill is a subdirectory containing at minimum a ``skill.py`` file
that defines exactly one subclass of :class:`app.skills._base.Skill`.
The subclass is instantiated and ``SkillRegistry.register`` is called.

Errors are isolated: a broken skill logs and is skipped — other skills
still load. The findings list returned by
:func:`discover_and_register_external` is exposed via the ``localflow
skills`` CLI command so users can debug load failures.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skills._base import SkillRegistry
    from app.tools._registry import ToolRegistry


logger = logging.getLogger(__name__)


@dataclass
class LoadFinding:
    """One row of the load-attempt audit log. Surfaced by the
    ``localflow skills`` command so users can diagnose why an external
    skill didn't appear."""

    source_dir: str
    status: str  # "registered" | "skipped" | "error"
    skill_name: str | None = None
    class_name: str | None = None
    error: str | None = None
    extras: dict = field(default_factory=dict)


def default_external_skill_dirs() -> list[Path]:
    """Return the ordered list of directories LocalFlow will search.

    Empty + missing dirs are kept in the list (so the audit shows them
    as "no skills found there" rather than silently absent).
    """
    dirs: list[Path] = []

    env = os.environ.get("LOCALFLOW_SKILLS_DIR", "")
    if env:
        for raw in env.split(os.pathsep):
            raw = raw.strip()
            if raw:
                p = Path(raw).expanduser().resolve()
                if p not in dirs:
                    dirs.append(p)

    try:
        cwd = Path.cwd().resolve()
        cwd_skills = cwd / ".localflow" / "skills"
        if cwd_skills not in dirs:
            dirs.append(cwd_skills)
    except OSError:
        pass

    try:
        home_skills = (Path.home() / ".localflow" / "skills").resolve()
        if home_skills not in dirs:
            dirs.append(home_skills)
    except (OSError, RuntimeError):
        pass

    return dirs


DISABLE_ENV = "LOCALFLOW_DISABLE_EXTERNAL_SKILLS"


def _external_skills_disabled() -> bool:
    """Read the kill-switch env var with the usual truthy semantics."""
    raw = os.environ.get(DISABLE_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def discover_and_register_external(
    registry: "SkillRegistry",
    dirs: list[Path],
    *,
    tool_registry: "ToolRegistry | None" = None,
) -> list[LoadFinding]:
    """Walk each ``dir`` looking for subdirs containing ``skill.py``.
    Import each, register every Skill subclass found, and return an
    audit list. Failures don't propagate — they're recorded and the
    next skill is tried.

    Phase 7.1 (v0.6.3) security note: external skills are **trusted
    Python code**, not sandboxed. A malicious skill could ``import os``
    and bypass the harness. See docs/SECURITY.md. Two safety knobs:

      * ``LOCALFLOW_DISABLE_EXTERNAL_SKILLS=1`` kill switch — when set,
        this function refuses to load anything and returns one finding
        per skipped dir.
      * A one-line **warning** is emitted (via stderr) the first time
        any external skill registers in a process, listing the source
        dir and the trust caveat. Visible in ``localflow skills``
        audit table.

    Subdirs whose name starts with ``_`` or ``.`` are skipped (so
    ``_base.py``-style helpers under user skill dirs are ignored).

    Phase 4.2: ``tool_registry`` (if given) is propagated into each
    ``SkillRegistry.register`` call so external skills' declared
    ``required_tools`` are validated against the same catalog the
    built-ins are.
    """
    from app.skills._base import Skill, SkillError  # local import to avoid cycles

    findings: list[LoadFinding] = []

    if _external_skills_disabled():
        # Kill switch is set — record one finding per searched dir so the
        # audit table is still informative, then return without importing.
        for skills_dir in dirs:
            findings.append(
                LoadFinding(
                    source_dir=str(skills_dir),
                    status="skipped",
                    error=f"disabled by {DISABLE_ENV}=1",
                )
            )
        return findings

    # Buffered warning — emit ONCE per process at the end, only if any
    # external skill actually registered. Avoids spamming stderr for
    # users who never installed any external skills.
    registered_count = 0

    for skills_dir in dirs:
        if not skills_dir.exists():
            findings.append(
                LoadFinding(
                    source_dir=str(skills_dir),
                    status="skipped",
                    error="path does not exist",
                )
            )
            continue
        if not skills_dir.is_dir():
            findings.append(
                LoadFinding(
                    source_dir=str(skills_dir),
                    status="skipped",
                    error="path is not a directory",
                )
            )
            continue

        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(("_", ".")):
                continue

            skill_py = entry / "skill.py"
            if not skill_py.exists():
                findings.append(
                    LoadFinding(
                        source_dir=str(entry),
                        status="skipped",
                        error="no skill.py",
                    )
                )
                continue

            module = None
            try:
                module = _load_module(skill_py, entry.name)
            except Exception as exc:
                findings.append(
                    LoadFinding(
                        source_dir=str(entry),
                        status="error",
                        error=f"import failed: {type(exc).__name__}: {exc}",
                    )
                )
                logger.warning("failed to import external skill %s: %s", entry, exc)
                continue

            registered_any = False
            for cls_name, cls in inspect.getmembers(module, inspect.isclass):
                if cls is Skill:
                    continue
                if not issubclass(cls, Skill):
                    continue
                if cls.__module__ != module.__name__:
                    # Skip Skill subclasses that were merely imported
                    # (e.g., re-exporting FolderOrganizerSkill from a
                    # user skill that wants to reuse parts).
                    continue
                try:
                    instance = cls()
                except Exception as exc:
                    findings.append(
                        LoadFinding(
                            source_dir=str(entry),
                            status="error",
                            class_name=cls_name,
                            error=f"instantiate failed: {type(exc).__name__}: {exc}",
                        )
                    )
                    logger.warning("failed to instantiate %s in %s: %s", cls_name, entry, exc)
                    continue
                try:
                    registry.register(instance, tool_registry=tool_registry)
                except SkillError as exc:
                    findings.append(
                        LoadFinding(
                            source_dir=str(entry),
                            status="error",
                            class_name=cls_name,
                            skill_name=instance.manifest.name,
                            error=f"register failed: {exc}",
                        )
                    )
                    continue
                findings.append(
                    LoadFinding(
                        source_dir=str(entry),
                        status="registered",
                        class_name=cls_name,
                        skill_name=instance.manifest.name,
                    )
                )
                registered_any = True
                registered_count += 1

            if not registered_any and not any(
                f.source_dir == str(entry) and f.status == "error" for f in findings
            ):
                findings.append(
                    LoadFinding(
                        source_dir=str(entry),
                        status="skipped",
                        error="no Skill subclass found in skill.py",
                    )
                )

    if registered_count > 0:
        # One-time, end-of-load warning. Goes to stderr so it doesn't
        # corrupt MCP stdio framing or pollute regular CLI output.
        names = sorted(
            {f.skill_name for f in findings if f.status == "registered" and f.skill_name}
        )
        sys.stderr.write(
            f"⚠ LocalFlow loaded {registered_count} external skill(s): "
            f"{', '.join(names)}. External skills are TRUSTED Python code and "
            f"are NOT sandboxed — they can bypass the harness via direct "
            f"imports. To disable, set {DISABLE_ENV}=1 in your environment "
            f"(see docs/SECURITY.md).\n"
        )

    return findings


def _load_module(skill_py: Path, suggested_name: str):
    """Import ``skill_py`` as a unique module so multiple external skills
    with the same internal class name don't trample each other.

    The module name is namespaced to avoid colliding with built-in skill
    modules under ``app.skills.*``.
    """
    # Hash the absolute path so re-imports of the same file give the
    # same module name (idempotent across calls), but two files with
    # the same parent-dir name still get distinct names.
    safe = "".join(c if c.isalnum() else "_" for c in suggested_name)
    digest = abs(hash(str(skill_py.resolve()))) % (10**8)
    module_name = f"_localflow_ext_skill_{safe}_{digest}"

    spec = importlib.util.spec_from_file_location(module_name, skill_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build module spec for {skill_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # required for relative imports inside the skill
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module
