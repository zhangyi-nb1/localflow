"""Skill ABC + SkillRegistry.

Activepieces-inspired type-safe skill framework. Each Skill is a
pluggable task capability that declares its manifest and implements the
lifecycle hooks the Harness Kernel calls. The Kernel itself owns the
universal stages (inspect / dry-run / execute / verify); skills provide
the parts that are task-specific (planner / validator / reporter).

Outline §13.7 maps skills to reference projects:
    FileOps      → MCP Filesystem + Open Interpreter
    DocumentOps  → Open Deep Research + DeepAgents
    DataOps      → TaskWeaver
    ...

Adding a new skill is the third implementation of outline §10.7's
extensibility rule: it must work without touching ``app/harness/``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)

if TYPE_CHECKING:
    from app.harness.executor import ExecutionOutcome
    from app.tools._registry import ToolRegistry


class SkillError(RuntimeError):
    """Base class for skill-level errors raised by validate/plan."""


class Skill(ABC):
    """Abstract base class every LocalFlow skill inherits.

    Canonical 8-stage lifecycle (Phase 4.3, asserted by
    :func:`app.skills._contract.run_skill_contract`):

      1. ``manifest_valid``           — manifest well-formed, all
                                        ``required_tools`` resolve in the
                                        Phase 4.2 Tool Registry.
      2. ``plan_empty_workspace``     — ``skill.plan()`` on an empty
                                        :class:`WorkspaceSnapshot` must not crash.
      3. ``plan_happy_path``          — ``skill.plan()`` on a seeded snapshot
                                        produces a valid :class:`ActionPlan`
                                        staying inside the workspace and
                                        within ``manifest.allowed_actions``.
      4. ``validate_accepts_own_plan``— ``skill.validate()`` accepts the
                                        plan its own ``plan()`` just returned.
      5. ``validate_rejects_garbage`` — A plan whose target_path escapes the
                                        workspace is rejected by either
                                        ``skill.validate()`` or the harness
                                        ``policy_guard.resolve_inside`` (always
                                        true in a stock harness — included so
                                        forks that swap the kernel don't lose
                                        the safety net silently).
      6. ``execute_and_verify``       — :class:`Executor` runs the plan;
                                        :class:`Verifier` independently passes.
      7. ``rollback_restores``        — :class:`Rollback` returns the
                                        workspace's file count to its
                                        pre-execute value.
      8. ``report_non_empty``         — ``skill.report()`` returns non-empty
                                        markdown.

    Skills MUST NOT touch ``app/harness/`` or ``app/storage/`` directly.
    The contract is: receive :class:`TaskSpec` + :class:`WorkspaceSnapshot`,
    return :class:`ActionPlan` (or a string for report). All side effects
    happen through the harness via the typed actions in the returned plan.

    See also: :func:`app.skills._contract.run_skill_contract` — the
    canonical "is this Skill compatible with LocalFlow's lifecycle?" test.
    """

    @property
    @abstractmethod
    def manifest(self) -> SkillManifest:
        """Static, declarative metadata. Used by the CLI to list skills
        and by introspection / documentation tooling."""

    @abstractmethod
    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        """Deterministic (rule-based) planner. Every skill MUST implement
        this — even if an LLM-based planner is also provided, the rule
        version serves as the deterministic fallback and the test baseline."""

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        """LLM-based planner. Optional override. Default raises
        NotImplementedError so the CLI can surface a clear message when
        ``--planner llm`` is used on a skill that doesn't support it."""
        raise NotImplementedError(
            f"skill {self.manifest.name!r} does not support --planner llm; "
            f"use --planner rule (or another skill)"
        )

    def supports_llm(self) -> bool:
        """Check if this skill overrides ``plan_with_llm``. Used by CLI
        for clearer error messages without forcing a call."""
        return type(self).plan_with_llm is not Skill.plan_with_llm

    @abstractmethod
    def validate(self, plan: ActionPlan) -> None:
        """Skill-specific structural validation beyond Pydantic + Policy
        Guard. Raise SkillError (or a subclass) on violation."""

    @abstractmethod
    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome: "ExecutionOutcome",
        verification: VerificationResult,
    ) -> str:
        """Render the final_report.md markdown for this run."""


class SkillRegistry:
    """Process-wide registry of skill instances. Skills register
    themselves at package import time."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(
        self,
        skill: Skill,
        *,
        tool_registry: "ToolRegistry | None" = None,
    ) -> None:
        """Register ``skill`` under its manifest name.

        When ``tool_registry`` is supplied, every name in
        ``skill.manifest.required_tools`` must resolve there — Phase 4.2's
        "declared dependencies fail loudly" contract. Missing tools raise
        ``SkillError`` and the skill is **not** registered.
        """
        name = skill.manifest.name
        if name in self._skills:
            raise SkillError(f"skill {name!r} already registered")
        if tool_registry is not None:
            for tool_name in skill.manifest.required_tools:
                if not tool_registry.has(tool_name):
                    raise SkillError(
                        f"skill {name!r} requires unknown tool {tool_name!r}"
                    )
        self._skills[name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def require(self, name: str) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise SkillError(
                f"unknown skill: {name!r}; available: {', '.join(self.list_names())}"
            )
        return skill

    def list_names(self) -> list[str]:
        return sorted(self._skills.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)
