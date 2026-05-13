"""Skill registry — wires every concrete Skill into a process-wide
``SkillRegistry`` at import time so the CLI can dispatch by name.

To add a new skill:
  1. Implement ``Skill`` ABC in ``app/skills/<your_skill>/skill.py``.
  2. Import + register it here.
That's the whole contract — ``app/harness/`` and ``app/cli.py`` stay
untouched. This is the third proof of outline §10.7's extensibility rule.
"""

from app.skills._base import Skill, SkillError, SkillRegistry
from app.skills._contract import (
    ContractReport,
    StageResult,
    WorkspaceSeeder,
    run_skill_contract,
)
from app.skills._loader import (
    LoadFinding,
    default_external_skill_dirs,
    discover_and_register_external,
)
from app.skills.data_analyzer import DataAnalyzerSkill
from app.skills.data_reporter import DataReporterSkill
from app.skills.folder_organizer import FolderOrganizerSkill
from app.skills.pdf_indexer import PdfIndexerSkill
from app.tools import get_default_tool_registry

# Phase 4.2: the same Tool Registry validates both built-in and external
# skill manifests' required_tools. Typos / drift fail at register time.
_tool_registry = get_default_tool_registry()

_default_registry: SkillRegistry = SkillRegistry()
_default_registry.register(FolderOrganizerSkill(), tool_registry=_tool_registry)
_default_registry.register(PdfIndexerSkill(), tool_registry=_tool_registry)
_default_registry.register(DataReporterSkill(), tool_registry=_tool_registry)
_default_registry.register(DataAnalyzerSkill(), tool_registry=_tool_registry)

# Phase 4.1: filesystem skill discovery. Built-ins register FIRST so any
# external skill with a colliding name fails registration (logged in the
# findings, surfaced by the ``localflow skills`` command). Built-ins
# always win on collision.
_load_findings: list[LoadFinding] = discover_and_register_external(
    _default_registry,
    default_external_skill_dirs(),
    tool_registry=_tool_registry,
)


def get_default_registry() -> SkillRegistry:
    return _default_registry


def get_load_findings() -> list[LoadFinding]:
    """Audit trail of every external skill load attempt.

    Returned in scan order. Exposed by the ``localflow skills`` CLI
    command for debugging missing / broken external skills.
    """
    return list(_load_findings)


__all__ = [
    "ContractReport",
    "DataAnalyzerSkill",
    "DataReporterSkill",
    "FolderOrganizerSkill",
    "LoadFinding",
    "PdfIndexerSkill",
    "Skill",
    "SkillError",
    "SkillRegistry",
    "StageResult",
    "WorkspaceSeeder",
    "default_external_skill_dirs",
    "discover_and_register_external",
    "get_default_registry",
    "get_load_findings",
    "run_skill_contract",
]
