"""Auto-detect for the v0.9.0 single-skill UI.

In v0.8.x this module multi-way-classified goals into one of five
specialist skills (folder_organizer / pdf_indexer / data_reporter /
data_analyzer / workspace_visualizer) and surfaced an Override panel
for the cases where the heuristic guessed wrong. Real-user testing in
v0.8.2 made the problem crystal clear: a compound goal ("整理...然后
绘图") needs more than one skill, the Override panel was rated "蠢",
and capability-gap warnings only papered over the design issue.

**v0.9.0 simplification**: there is now exactly ONE user-facing skill
on the Plan page — the new ``agent`` meta-skill, which produces a
single ActionPlan covering organize + index + chart in one LLM call.
The auto-detect module collapses to two trivial decisions:

  * **Skill**: always ``agent``. The specialist skills remain in the
    registry for CLI / MCP power users — they just don't show up in
    the UI anymore.
  * **Planner**: ``llm`` if the goal is non-empty AND the user hasn't
    set ``prefer_llm_planner = False`` explicitly... in fact, the new
    rule is even simpler: **non-empty goal → llm**. Empty goal → rule
    fallback (organize-only). The user's preference toggle remains
    meaningful only for skills the UI doesn't expose by default.

The module's public API stays the same so callers (1_Plan.py,
existing tests) compile without changes — they just see simpler
outputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import WorkspaceSnapshot
from app.skills import SkillRegistry

# v0.9.0 default — exposed for tests and the Plan page reason line.
DEFAULT_SKILL = "agent"


@dataclass(frozen=True)
class SkillChoice:
    """Result of :func:`autodetect_skill`."""

    name: str
    reason: str


@dataclass(frozen=True)
class PlannerChoice:
    """Result of :func:`autodetect_planner`."""

    name: str  # "rule" or "llm"
    reason: str


@dataclass(frozen=True)
class CapabilityGap:
    """Kept for backwards compatibility — always returns None in
    v0.9.0 because the agent skill handles every recognized capability.
    The old organize+chart gap is now satisfied in a single agent plan.
    """

    message: str
    suggested_skill: str | None


def autodetect_skill(
    goal: str,
    snapshot: WorkspaceSnapshot | None,
    registry: SkillRegistry,
) -> SkillChoice:
    """Always return the agent skill.

    The function still takes the legacy parameters so existing call
    sites and tests don't break. Routing decisions now live inside the
    agent's LLM planner instead of this module.
    """
    name = DEFAULT_SKILL
    available = set(registry.list_names())
    if name not in available:
        # Defensive fallback — should never fire in a clean install
        # because AgentSkill is registered eagerly in app/skills/__init__.py.
        return SkillChoice(
            name="folder_organizer",
            reason="agent skill missing from registry — falling back to folder_organizer",
        )
    if not (goal or "").strip():
        return SkillChoice(
            name=name,
            reason="describe what you want — agent will decompose and plan in one shot",
        )
    return SkillChoice(
        name=name,
        reason="agent handles the whole goal end-to-end (organize + index + chart)",
    )


def autodetect_planner(
    goal: str,
    skill_name: str,
    registry: SkillRegistry,
    *,
    prefer_llm: bool = False,
) -> PlannerChoice:
    """Pick rule vs llm.

    v0.9.0 logic:
      1. If the skill can't do LLM at all → ``rule``.
      2. Empty goal → ``rule`` (we have nothing for the LLM to plan
         beyond what folder_organizer's fallback already does).
      3. Otherwise → ``llm``. The whole point of v0.9.0 is that the
         agent is LLM-driven; trying to mimic compound-goal detection
         heuristics in v0.8.2 was the wrong abstraction.

    ``prefer_llm`` is still threaded through for parity with v0.8.2,
    but the new default already favours llm, so it only matters when
    the user has flipped the toggle OFF and is on a non-LLM skill —
    in which case the function returns ``rule`` and the toggle is
    silently respected.
    """
    skill = registry.get(skill_name)
    if skill is None:
        return PlannerChoice("rule", f"unknown skill `{skill_name}` — defaulting to rule")
    if not skill.supports_llm():
        return PlannerChoice("rule", f"skill `{skill_name}` doesn't support LLM planning")
    if not (goal or "").strip():
        return PlannerChoice("rule", "empty goal — using rule fallback")
    return PlannerChoice("llm", "agent skill plans every compound goal via LLM")


def detect_capability_gap(
    goal: str,
    skill_name: str,
    snapshot: WorkspaceSnapshot | None,
) -> CapabilityGap | None:
    """In v0.9.0 there are no capability gaps — the agent skill covers
    every capability the UI used to gate-keep on. Function retained so
    callers don't need a refactor; always returns None."""
    return None


def is_compound_goal(goal: str) -> tuple[bool, str]:
    """Legacy helper. Kept for backwards-compatible imports in tests.
    The agent skill handles compound goals natively, so we no longer
    use the result for planner gating — but external tooling may still
    consult it for diagnostics."""
    if not goal:
        return False, ""
    low = goal.lower()
    markers = (
        " then ",
        " and then ",
        " finally ",
        "然后",
        "再",
        "接着",
        "最后",
        "之后",
    )
    for m in markers:
        if m in low:
            return True, f"goal has a multi-step marker ('{m.strip()}')"
    return False, ""
