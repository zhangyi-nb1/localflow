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

# Phase 11 — keywords that indicate the user actually wants the data in
# their spreadsheets read, not just their files organised. When detected
# alongside an .xlsx / .csv in the workspace, we route to data_analyzer
# instead of the agent meta-skill. Tested via tests/test_autodetect_data_routing.py.
#
# Deliberately scoped to **analysis verbs** — generic chart-kind words
# ("柱状图" / "bar chart") aren't enough on their own because the agent
# meta-skill legitimately handles "chart of file-type counts" goals.
# The discriminator is "are we interpreting the data INSIDE the file",
# which is what verbs like 分析 / 解读 / analyze / interpret capture.
DATA_VERB_HINTS_ZH: tuple[str, ...] = (
    "分析",
    "解读",
    "统计",
    "分布",
    "趋势",
    "汇总",
    "聚合",
    "可视化数据",
)
DATA_VERB_HINTS_EN: tuple[str, ...] = (
    "analyze",
    "analyse",
    "interpret the data",
    "summarize the data",
    "summarise the data",
    "aggregate",
    "statistics",
    "distribution",
    "trend",
    "correlate",
)
DATA_FILE_TYPES = frozenset({"tabular", "excel"})


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


def _looks_like_data_analysis_goal(goal: str, snapshot: WorkspaceSnapshot | None) -> bool:
    """Return True when the goal mentions data analysis AND the workspace
    actually contains a tabular file. Both halves are required — analysis
    verbs alone (no .csv/.xlsx) should still route to the agent
    meta-skill so empty workspaces and PDF-only workspaces don't get
    pointed at a skill that can't help them.
    """
    if snapshot is None:
        return False
    if not (goal or "").strip():
        return False
    has_data_file = any(getattr(f, "file_type", "") in DATA_FILE_TYPES for f in snapshot.files)
    if not has_data_file:
        return False
    goal_lower = goal.lower()
    if any(h in goal_lower for h in DATA_VERB_HINTS_EN):
        return True
    if any(h in goal for h in DATA_VERB_HINTS_ZH):
        return True
    return False


def autodetect_skill(
    goal: str,
    snapshot: WorkspaceSnapshot | None,
    registry: SkillRegistry,
) -> SkillChoice:
    """Pick a skill given the goal + workspace snapshot.

    Phase 11 — when the workspace contains a data file AND the goal
    contains an analysis verb (in Chinese or English), route to
    ``data_analyzer`` so its pandas-based planner reads real cell
    values instead of the agent meta-skill describing the file
    metadata. Falls back to ``agent`` for everything else.
    """
    available = set(registry.list_names())

    if _looks_like_data_analysis_goal(goal, snapshot) and "data_analyzer" in available:
        return SkillChoice(
            name="data_analyzer",
            reason=(
                "goal mentions data analysis + workspace contains .csv/.xlsx — "
                "routing to data_analyzer so the LLM picks an AnalysisSpec "
                "(groupby/aggregation/chart) against real cell values"
            ),
        )

    name = DEFAULT_SKILL
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
