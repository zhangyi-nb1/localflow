"""Phase 4.1 — filesystem skill discovery tests.

Verifies the contracts that make Phase 4 a real plug-in system:
  * happy path: well-formed external skill is discovered + registered
  * multi-class: skill.py defining 2 Skill subclasses → both registered
  * no skill.py: subdir is skipped with a clear finding
  * no Skill subclass: skill.py exists but defines no Skill → skipped
  * import error: skill.py raises on import → recorded as error, not fatal
  * collision: external skill with same name as built-in → registration error
  * private dirs: subdirs starting with ``_`` / ``.`` are ignored
  * env var: LOCALFLOW_SKILLS_DIR is honored
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.skills._base import SkillRegistry
from app.skills._loader import (
    default_external_skill_dirs,
    discover_and_register_external,
)

SKILL_PY_TEMPLATE = '''
from app.schemas import ActionPlan, SkillManifest, TaskSpec, WorkspaceSnapshot, VerificationResult
from app.skills._base import Skill


class {class_name}(Skill):
    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(name="{skill_name}", version="0.1.0", description="test")

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        import uuid
        return ActionPlan(plan_id=f"plan-{{uuid.uuid4().hex[:8]}}", task_id=task.task_id, summary="x")

    def validate(self, plan: ActionPlan) -> None:
        pass

    def report(self, *, task, plan, outcome, verification) -> str:
        return "ok"
'''


def _write_skill(skills_dir: Path, dirname: str, class_name: str, skill_name: str) -> Path:
    sub = skills_dir / dirname
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "skill.py").write_text(
        SKILL_PY_TEMPLATE.format(class_name=class_name, skill_name=skill_name),
        encoding="utf-8",
    )
    return sub


# --------------------------------------------------------------------- happy path


def test_discovers_and_registers_well_formed_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "my_skill", "MyExtSkill", "my_ext_skill")

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert "my_ext_skill" in registry.list_names()
    registered = [f for f in findings if f.status == "registered"]
    assert len(registered) == 1
    assert registered[0].skill_name == "my_ext_skill"
    assert registered[0].class_name == "MyExtSkill"


def test_multiple_skills_in_one_file_all_register(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    sub = skills_dir / "duo"
    sub.mkdir(parents=True)
    # Two Skill subclasses in one skill.py
    body = "\n\n".join([
        SKILL_PY_TEMPLATE.format(class_name="OneSkill", skill_name="one"),
        SKILL_PY_TEMPLATE.format(class_name="TwoSkill", skill_name="two"),
    ])
    (sub / "skill.py").write_text(body, encoding="utf-8")

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert {"one", "two"}.issubset(set(registry.list_names()))
    assert sum(1 for f in findings if f.status == "registered") == 2


# --------------------------------------------------------------------- skip cases


def test_no_skill_py_is_skipped_with_finding(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "empty_dir").mkdir(parents=True)

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert registry.list_names() == []
    assert any(f.status == "skipped" and "no skill.py" in (f.error or "") for f in findings)


def test_no_skill_subclass_is_skipped_with_finding(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    sub = skills_dir / "decoy"
    sub.mkdir(parents=True)
    (sub / "skill.py").write_text("x = 1\n", encoding="utf-8")  # no Skill subclass

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert registry.list_names() == []
    assert any(f.status == "skipped" and "no Skill subclass" in (f.error or "") for f in findings)


def test_private_dirs_are_ignored(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    for hidden in ("_helpers", ".hidden", "_base"):
        _write_skill(skills_dir, hidden, "ShouldNotRegister", "should_not_register")

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert registry.list_names() == []
    # No findings either — private dirs are ignored entirely.
    assert all(f.source_dir.endswith("skills") or "_" not in Path(f.source_dir).name[:1] for f in findings)


def test_nonexistent_path_recorded_not_fatal(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does" / "not" / "exist"
    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [nonexistent])
    assert registry.list_names() == []
    assert any(f.status == "skipped" and "does not exist" in (f.error or "") for f in findings)


# --------------------------------------------------------------------- error cases


def test_skill_py_with_import_error_does_not_crash(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    sub = skills_dir / "broken"
    sub.mkdir(parents=True)
    (sub / "skill.py").write_text(
        "from this_module_does_not_exist import nothing\n", encoding="utf-8"
    )
    # Also a good skill alongside — it should still load.
    _write_skill(skills_dir, "good", "GoodSkill", "good_skill")

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    # Good skill registered.
    assert "good_skill" in registry.list_names()
    # Bad skill recorded as error.
    assert any(f.status == "error" and "import failed" in (f.error or "") for f in findings)


def test_skill_py_with_instantiation_error_does_not_crash(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    sub = skills_dir / "broken_init"
    sub.mkdir(parents=True)
    (sub / "skill.py").write_text(
        '''
from app.skills._base import Skill

class BadInit(Skill):
    def __init__(self):
        raise RuntimeError("intentional explosion")

    @property
    def manifest(self):
        from app.schemas import SkillManifest
        return SkillManifest(name="never", version="0.0.0", description="")

    def plan(self, task, snapshot): ...
    def validate(self, plan): ...
    def report(self, *, task, plan, outcome, verification): return ""
''',
        encoding="utf-8",
    )

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])

    assert "never" not in registry.list_names()
    assert any(f.status == "error" and "instantiate failed" in (f.error or "") for f in findings)


def test_name_collision_with_builtin_records_error(tmp_path: Path) -> None:
    """An external skill claiming the name 'folder_organizer' should
    fail registration (built-in wins, error logged in findings)."""
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "impostor", "Impostor", "folder_organizer")

    # Use a fresh registry pre-populated with the built-in name so we
    # can check collision behavior in isolation.
    from app.skills.folder_organizer import FolderOrganizerSkill

    registry = SkillRegistry()
    registry.register(FolderOrganizerSkill())

    findings = discover_and_register_external(registry, [skills_dir])

    # Only the built-in is registered.
    assert registry.list_names() == ["folder_organizer"]
    assert any(f.status == "error" and "register failed" in (f.error or "") for f in findings)


# --------------------------------------------------------------------- search paths


def test_env_var_skill_dir_is_searched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LOCALFLOW_SKILLS_DIR should be the first path searched."""
    monkeypatch.setenv("LOCALFLOW_SKILLS_DIR", str(tmp_path))
    dirs = default_external_skill_dirs()
    # tmp_path resolves to .../tmp_pathN — should be first
    assert dirs[0].resolve() == tmp_path.resolve()


def test_env_var_supports_multiple_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("LOCALFLOW_SKILLS_DIR", f"{a}{os.pathsep}{b}")
    dirs = default_external_skill_dirs()
    paths = [str(d) for d in dirs]
    assert str(a) in paths
    assert str(b) in paths


# --------------------------------------------------------------- Phase 4.2


SKILL_PY_WITH_BAD_TOOL = '''
from app.schemas import ActionPlan, SkillManifest, TaskSpec, WorkspaceSnapshot, VerificationResult
from app.skills._base import Skill


class BadToolDecl(Skill):
    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="bad_tool_decl",
            required_tools=["totally_nonexistent.tool"],
        )

    def plan(self, task, snapshot):
        return ActionPlan(plan_id="p", task_id=task.task_id, summary="x")

    def validate(self, plan) -> None:
        pass

    def report(self, *, task, plan, outcome, verification) -> str:
        return ""
'''


def test_external_skill_with_bogus_required_tools_is_recorded_as_error(tmp_path: Path) -> None:
    """An external skill declaring a tool not in the Tool Registry must
    fail registration with a clear error finding — and must not block
    other external skills from loading."""
    from app.tools import get_default_tool_registry

    skills_dir = tmp_path / "skills"
    sub = skills_dir / "bad_decl"
    sub.mkdir(parents=True)
    (sub / "skill.py").write_text(SKILL_PY_WITH_BAD_TOOL, encoding="utf-8")
    # A good skill alongside — it should still load.
    _write_skill(skills_dir, "good_decl", "GoodDecl", "good_decl")

    registry = SkillRegistry()
    findings = discover_and_register_external(
        registry, [skills_dir], tool_registry=get_default_tool_registry()
    )

    assert "bad_tool_decl" not in registry.list_names()
    assert "good_decl" in registry.list_names()
    assert any(
        f.status == "error" and "requires unknown tool" in (f.error or "")
        for f in findings
    )


def test_external_skill_without_tool_registry_param_skips_validation(tmp_path: Path) -> None:
    """If discover_and_register_external is called without tool_registry
    (legacy callers / partial wiring), it must still register skills —
    just without the Phase 4.2 validation."""
    skills_dir = tmp_path / "skills"
    sub = skills_dir / "bad_decl"
    sub.mkdir(parents=True)
    (sub / "skill.py").write_text(SKILL_PY_WITH_BAD_TOOL, encoding="utf-8")

    registry = SkillRegistry()
    findings = discover_and_register_external(registry, [skills_dir])
    # Skill registered because no tool_registry was passed.
    assert "bad_tool_decl" in registry.list_names()
    assert any(f.status == "registered" for f in findings)
