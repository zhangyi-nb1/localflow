"""Tests for the Skill ABC + SkillRegistry (Phase 2.3 / outline §10.7
extensibility rule + §13.5 'Skill 不应侵入 Harness Kernel')."""

from __future__ import annotations

import pytest

from app.schemas import ActionPlan, SkillManifest, TaskSpec, WorkspaceSnapshot
from app.skills import (
    DataReporterSkill,
    FolderOrganizerSkill,
    PdfIndexerSkill,
    Skill,
    SkillError,
    SkillRegistry,
    get_default_registry,
)


def test_default_registry_has_all_skills() -> None:
    """The 4 built-ins MUST be registered. Phase 4.1 also loads any
    external skills the user has dropped into ~/.localflow/skills/, so
    we test subset rather than equality — otherwise the test breaks for
    anyone who has installed an external skill."""
    registry = get_default_registry()
    names = set(registry.list_names())
    builtins = {"folder_organizer", "pdf_indexer", "data_reporter", "data_analyzer"}
    assert builtins.issubset(names)


def test_default_registry_returns_correct_instance() -> None:
    from app.skills import DataAnalyzerSkill

    registry = get_default_registry()
    assert isinstance(registry.require("folder_organizer"), FolderOrganizerSkill)
    assert isinstance(registry.require("pdf_indexer"), PdfIndexerSkill)
    assert isinstance(registry.require("data_reporter"), DataReporterSkill)
    assert isinstance(registry.require("data_analyzer"), DataAnalyzerSkill)


def test_registry_require_unknown_raises() -> None:
    registry = SkillRegistry()
    with pytest.raises(SkillError, match="unknown skill"):
        registry.require("nonexistent")


def test_registry_register_duplicate_raises() -> None:
    registry = SkillRegistry()
    registry.register(FolderOrganizerSkill())
    with pytest.raises(SkillError, match="already registered"):
        registry.register(FolderOrganizerSkill())


def test_registry_contains() -> None:
    registry = get_default_registry()
    assert "folder_organizer" in registry
    assert "pdf_indexer" in registry
    assert "nonexistent" not in registry


def test_skills_have_valid_manifests() -> None:
    """Manifest must be a real SkillManifest with required fields."""
    for skill in (FolderOrganizerSkill(), PdfIndexerSkill(), DataReporterSkill()):
        m = skill.manifest
        assert isinstance(m, SkillManifest)
        assert m.name
        assert m.version
        assert m.allowed_actions
        assert m.supports_dry_run is True
        assert m.supports_rollback is True
        assert m.supports_verify is True


def test_folder_organizer_supports_llm() -> None:
    assert FolderOrganizerSkill().supports_llm() is True


def test_pdf_indexer_does_not_support_llm_yet() -> None:
    assert PdfIndexerSkill().supports_llm() is False


def test_pdf_indexer_plan_with_llm_raises_clear_error() -> None:
    skill = PdfIndexerSkill()
    task = TaskSpec(task_id="t", user_goal="g", workspace_root="/tmp")
    snap = WorkspaceSnapshot(snapshot_id="s", task_id="t", root="/tmp")
    with pytest.raises(NotImplementedError, match="pdf_indexer"):
        skill.plan_with_llm(task, snap)


def test_skill_abc_cannot_be_instantiated_directly() -> None:
    """The ABC must enforce that subclasses provide all abstract methods."""
    with pytest.raises(TypeError):
        Skill()  # type: ignore[abstract]


class _IncompleteSkill(Skill):
    """Missing several abstract methods on purpose."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(name="incomplete")


def test_incomplete_skill_subclass_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        _IncompleteSkill()  # type: ignore[abstract]


# --------------------------------------------------------------- Phase 4.2 ---


class _SkillWithRequiredTools(Skill):
    """Fixture skill that declares required_tools — used to exercise the
    Phase 4.2 SkillRegistry.register validation hook."""

    def __init__(self, name: str, required_tools: list[str]) -> None:
        self._name = name
        self._tools = required_tools

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name=self._name,
            required_tools=list(self._tools),
            allowed_actions=["index"],
        )

    def plan(self, task, snapshot):
        return ActionPlan(plan_id="p", task_id=task.task_id, summary="x")

    def validate(self, plan) -> None:
        pass

    def report(self, *, task, plan, outcome, verification) -> str:
        return ""


def test_register_with_tool_registry_accepts_known_tool() -> None:
    from app.tools import get_default_tool_registry

    registry = SkillRegistry()
    skill = _SkillWithRequiredTools("ok_skill", ["data_ops.read_tabular"])
    registry.register(skill, tool_registry=get_default_tool_registry())
    assert "ok_skill" in registry


def test_register_with_tool_registry_rejects_unknown_tool() -> None:
    from app.tools import get_default_tool_registry

    registry = SkillRegistry()
    skill = _SkillWithRequiredTools("bad_skill", ["data_ops.read_tabulat"])  # typo
    with pytest.raises(SkillError, match="requires unknown tool"):
        registry.register(skill, tool_registry=get_default_tool_registry())
    # Skill must not be partially registered.
    assert "bad_skill" not in registry


def test_register_without_tool_registry_skips_validation() -> None:
    """Back-compat: pre-Phase-4.2 callers (or tests) that omit
    tool_registry should still register skills without validation."""
    registry = SkillRegistry()
    skill = _SkillWithRequiredTools("anything", ["nonexistent.tool"])
    registry.register(skill)  # no tool_registry → no check
    assert "anything" in registry


@pytest.mark.parametrize(
    "skill_name",
    [
        "folder_organizer",
        "pdf_indexer",
        "data_reporter",
        "data_analyzer",
    ],
)
def test_builtin_required_tools_resolve_in_default_tool_registry(skill_name: str) -> None:
    """The whole point of Phase 4.2's validation: typos / drift fail at
    registration. The 4 built-ins' declared tools MUST resolve."""
    from app.tools import get_default_tool_registry

    skill = get_default_registry().require(skill_name)
    tool_reg = get_default_tool_registry()
    for tool in skill.manifest.required_tools:
        assert tool_reg.has(tool), f"built-in skill {skill_name!r} declares unknown tool {tool!r}"


# --------------------------------------------------------------- Phase 4.3 ---


def test_every_builtin_skill_has_a_contract_case() -> None:
    """Future-proofing: when someone adds a 5th built-in Skill, this
    guard fails until they also add a row to BUILTIN_CONTRACT_CASES in
    tests/test_skill_contracts.py. Catches "added a Skill but forgot the
    lifecycle test".
    """
    from tests.test_skill_contracts import BUILTIN_CONTRACT_CASES

    builtins = {"folder_organizer", "pdf_indexer", "data_reporter", "data_analyzer"}
    # Each entry is a pytest.param(skill_cls, seeder, id="<name>")
    contract_ids = {p.id for p in BUILTIN_CONTRACT_CASES}
    missing = builtins - contract_ids
    assert not missing, (
        f"built-in skills missing from BUILTIN_CONTRACT_CASES: {missing}. "
        f"Add a row to tests/test_skill_contracts.py."
    )
