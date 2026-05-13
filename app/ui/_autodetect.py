"""Auto-detect the right skill + planner from a goal + workspace.

Phase 8.1 / v0.8.0 introduced this module so the Plan page wouldn't
expose Skill + Planner dropdowns to users who just want to describe a
task. Phase 8.2 / v0.8.2 extends it with three things real-user
testing exposed:

  1. ``workspace_visualizer`` — a new chart-drawing skill. Goals with
     chart/绘制/可视化 keywords route here instead of data_reporter
     (which only writes markdown).
  2. **Compound-goal detection**. A goal with multiple steps
     ("然后/再/最后/then/finally") needs an LLM planner — rule
     planners produce a single category of action and cannot
     synthesize multi-step plans. The detector finds these and
     upgrades the planner.
  3. **User preference override**. ``MemoryStore`` now persists a
     ``prefer_llm_planner`` flag (see ``app/memory/_schema.py``).
     When set, any LLM-capable skill defaults to LLM regardless of
     goal text.
  4. **Capability-gap detection**. If the user asks for chart output
     in a workspace that no chart-capable skill can handle (e.g.
     "make a bar chart" with no tabular data and the auto-detect
     lands on data_reporter), surface that gap so the user knows the
     output won't include real visualizations.

Streamlit-free at import — the module is unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.schemas import WorkspaceSnapshot
from app.skills import SkillRegistry

# ───────────────────────────────────────── keyword lists (bilingual)
# Patterns are case-insensitive substrings — exact regex would be
# overkill and harder for non-native-English speakers to predict.

_DATA_ANALYZE_KEYWORDS = (
    "analy",
    "groupby",
    "group by",
    "filter",
    "aggregate",
    "pivot",
    "regression",
    "correlation",
    "trend",
    "分析",
    "聚合",
    "趋势",
    "相关",
)

_DATA_REPORT_KEYWORDS = (
    "report",
    "stats",
    "statistic",
    "describe",
    "dashboard",
    "overview",
    "metrics",
    "报告",
    "统计",
    "汇总",
    "概况",
)

_PDF_KEYWORDS = (
    "pdf",
    "paper",
    "papers",
    "thesis",
    "article",
    "citation",
    "bibliography",
    "abstract",
    "literature",
    "scientific",
    "论文",
    "文献",
    "索引",
    "摘要",
    "目录",
)

_ORGANIZE_KEYWORDS = (
    "organi",  # organize / organise / organized
    "sort",
    "categori",  # categorize / categorise / category
    "classify",
    "classification",
    "tidy",
    "clean up",
    "cleanup",
    "by file type",
    "by type",
    "by extension",
    "rename",
    "整理",
    "分类",
    "归类",
    "整齐",
    "按类型",
    "按格式",
    "重命名",
)

# Chart / visualization keywords — route to workspace_visualizer
# (rule-based PNG bar chart of file counts) when there's no tabular
# data; route to data_analyzer when there IS tabular data (it draws
# real per-column charts).
_CHART_KEYWORDS = (
    "chart",
    "plot",
    "bar chart",
    "bar graph",
    "visuali",  # visualization / visualisation / visualize
    "graph",
    "diagram",
    "histogram",
    "图表",
    "柱状",
    "条形",
    "可视化",
    "绘制",
    "绘图",
    "画图",
    "画一张",
    "图象",  # user's typo for 图像; we accept both
    "图像",
)

_LLM_INTENT_KEYWORDS = (
    "by content",
    "by topic",
    "by meaning",
    "by subject",
    "semantic",
    "semantically",
    "intelligent",
    "intelligently",
    "smart",
    "understand",
    "based on content",
    "based on topic",
    "based on meaning",
    "summarize",
    "summarise",
    "infer",
    "categorize semantically",
    "按内容",
    "按主题",
    "按语义",
    "按含义",
    "智能",
    "理解",
    "语义",
)

# Compound-goal markers — these signal the user wants multiple steps
# that need an LLM to synthesize into a coherent ActionPlan. Rule
# planners produce only one action category (move-only, or index-only,
# etc.) and silently skip the rest of the request.
_COMPOUND_MARKERS = (
    " then ",
    " and then ",
    " after ",
    " finally ",
    " next, ",
    "first,",
    "second,",
    "third,",
    "step 1",
    "step 2",
    "step 3",
    "然后",
    "再",
    "接着",
    "最后",
    "之后",
    "并且",
    "并将",
    "接下来",
)

# Verbs (bilingual) used to count distinct actions in a goal — three
# different verbs is a stronger compound signal than two.
_ACTION_VERBS = (
    ("organize", "整理"),
    ("sort", "分类"),
    ("classify", "归类"),
    ("rename", "重命名"),
    ("summarize", "总结"),
    ("summarise", "概况"),
    ("report", "报告"),
    ("count", "统计"),
    ("analy", "分析"),
    ("chart", "图表"),
    ("plot", "绘制"),
    ("visuali", "可视化"),
    ("index", "索引"),
    ("move", "移动"),
    ("copy", "复制"),
    ("delete", "删除"),
    ("group", "分组"),
)

# ───────────────────────────────────────── workspace-sufficiency thresholds

_MIN_TABULAR_FOR_DATA_SKILLS = 1
_MIN_PDF_FOR_PDF_SKILL = 1


@dataclass(frozen=True)
class SkillChoice:
    """Result of :func:`autodetect_skill`. Both fields are always set."""

    name: str
    reason: str


@dataclass(frozen=True)
class PlannerChoice:
    """Result of :func:`autodetect_planner`."""

    name: str  # "rule" or "llm"
    reason: str


@dataclass(frozen=True)
class CapabilityGap:
    """Reported when the auto-detected skill can't fully satisfy what
    the goal asks for (e.g. user wants a PNG chart but the chosen skill
    only writes markdown). The Plan page surfaces this as a warning so
    the user can override before running."""

    message: str  # short user-facing explanation
    suggested_skill: str | None  # nudge toward a better fit, if any


def _contains_any(text: str, keywords: Iterable[str]) -> str | None:
    """Return the first keyword in ``keywords`` found inside ``text``
    (case-insensitive substring), or None."""
    low = text.lower()
    for kw in keywords:
        if kw in low:
            return kw
    return None


def _file_type_counts(snapshot: WorkspaceSnapshot | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    if snapshot is None:
        return counts
    for f in snapshot.files:
        counts[f.file_type] = counts.get(f.file_type, 0) + 1
    return counts


def _has_tabular(counts: dict[str, int]) -> bool:
    n = counts.get("tabular", 0) + counts.get("excel", 0)
    return n >= _MIN_TABULAR_FOR_DATA_SKILLS


def _has_pdfs(counts: dict[str, int]) -> bool:
    return counts.get("pdf", 0) >= _MIN_PDF_FOR_PDF_SKILL


def is_compound_goal(goal: str) -> tuple[bool, str]:
    """Detect whether a goal asks for multiple distinct steps.

    Returns ``(is_compound, reason)``. Two signals, either triggers:
      * An explicit compound marker (然后/再/最后/then/finally/etc.).
      * Three or more distinct action verbs.

    Returning ``True`` is the planner's cue to upgrade to LLM —
    rule planners can't synthesize multi-step plans across action
    categories.
    """
    if not goal:
        return False, ""
    low = goal.lower()
    marker = _contains_any(low, _COMPOUND_MARKERS)
    if marker is not None:
        return True, f"goal has a multi-step marker ('{marker.strip()}')"

    verbs_hit: list[str] = []
    for en, zh in _ACTION_VERBS:
        if en in low or zh in goal:
            verbs_hit.append(en)
    if len(verbs_hit) >= 3:
        return True, f"goal mixes {len(verbs_hit)} distinct action verbs"
    return False, ""


def autodetect_skill(
    goal: str,
    snapshot: WorkspaceSnapshot | None,
    registry: SkillRegistry,
) -> SkillChoice:
    """Pick the best skill for the goal + workspace.

    Priority order (strongest signal first):

      1. ``data_analyzer`` — tabular files + analysis intent
      2. ``data_reporter`` — tabular files + report intent (no chart)
      3. ``workspace_visualizer`` — chart intent without tabular data
      4. ``pdf_indexer``   — pdf files + pdf/index intent
      5. ``folder_organizer`` — organize intent (any workspace)
      6. fallback by content: tabular → data_reporter, pdf → pdf_indexer
      7. universal fallback: ``folder_organizer``

    Compound goals (multiple steps) still get a single skill choice
    here — the Plan page surfaces a capability-gap warning so the user
    knows which parts of their goal may be skipped.
    """
    goal_clean = (goal or "").strip()
    counts = _file_type_counts(snapshot)
    available = set(registry.list_names())

    tabular_n = counts.get("tabular", 0) + counts.get("excel", 0)
    pdf_n = counts.get("pdf", 0)

    def pick(name: str, reason: str, fallback: str = "folder_organizer") -> SkillChoice:
        if name in available:
            return SkillChoice(name=name, reason=reason)
        return SkillChoice(name=fallback, reason=f"{reason} (fallback — {name} not registered)")

    if not goal_clean:
        return pick(
            "folder_organizer",
            "default skill; tell me your goal to refine the choice",
        )

    has_chart_kw = _contains_any(goal_clean, _CHART_KEYWORDS) is not None
    has_organize_kw = _contains_any(goal_clean, _ORGANIZE_KEYWORDS) is not None
    # Workspace is "mixed" when most files are NOT tabular — even with
    # one stray xlsx, the user's primary intent (organize / chart) shouldn't
    # be hijacked into data_analyzer.
    non_tabular_n = sum(n for k, n in counts.items() if k not in ("tabular", "excel"))
    workspace_mixed = non_tabular_n > tabular_n

    # Rule: explicit organize verb in a mixed workspace wins over a
    # stray tabular file. The user is organizing their workspace, not
    # analyzing the xlsx.
    if has_organize_kw and workspace_mixed:
        return pick(
            "folder_organizer",
            "goal mentions organize/sort/categorize on a mixed workspace",
        )

    if _has_tabular(counts) and not workspace_mixed:
        if _contains_any(goal_clean, _DATA_ANALYZE_KEYWORDS) or has_chart_kw:
            return pick(
                "data_analyzer",
                f"{tabular_n} tabular file(s) + analysis/chart keyword in goal",
            )
        if _contains_any(goal_clean, _DATA_REPORT_KEYWORDS):
            return pick(
                "data_reporter",
                f"{tabular_n} tabular file(s) + report/stats keyword in goal",
            )

    if _has_pdfs(counts) and _contains_any(goal_clean, _PDF_KEYWORDS):
        return pick(
            "pdf_indexer",
            f"{pdf_n} PDF file(s) + pdf/index keyword in goal",
        )

    # Chart intent without organize → workspace_visualizer (real PNG of
    # file counts). Organize-then-chart compound goals are caught above;
    # the capability-gap helper warns about the chart part.
    if has_chart_kw and not has_organize_kw:
        return pick(
            "workspace_visualizer",
            "goal mentions a chart/visualization without tabular data",
        )

    if has_organize_kw:
        return pick(
            "folder_organizer",
            "goal mentions organize/sort/categorize",
        )

    if has_chart_kw:
        return pick(
            "workspace_visualizer",
            "goal mentions chart/visualization",
        )

    if _has_tabular(counts):
        return pick(
            "data_reporter",
            f"{tabular_n} tabular file(s) — defaulting to a data report",
        )
    if _has_pdfs(counts):
        return pick(
            "pdf_indexer",
            f"{pdf_n} PDF file(s) — defaulting to an index",
        )
    return pick(
        "folder_organizer",
        "no specific signal — defaulting to file-type organization",
    )


def autodetect_planner(
    goal: str,
    skill_name: str,
    registry: SkillRegistry,
    *,
    prefer_llm: bool = False,
) -> PlannerChoice:
    """Pick rule vs llm for a (goal, skill) pair.

    Rules (in priority order):

      1. If the skill doesn't override ``plan_with_llm`` → ``rule``.
         (No upgrade is possible, regardless of preferences.)
      2. If ``prefer_llm`` (from user memory) is True AND skill
         supports LLM → ``llm``.
      3. If the goal contains an LLM-intent keyword (semantic /
         content / topic / 智能 / 语义 / etc.) → ``llm``.
      4. If the goal is a compound multi-step goal AND skill supports
         LLM → ``llm``. Rule planners can't synthesize across action
         categories.
      5. Otherwise → ``rule`` (faster, deterministic, free).
    """
    goal_clean = (goal or "").strip()
    skill = registry.get(skill_name)
    if skill is None:
        return PlannerChoice("rule", f"unknown skill `{skill_name}` — defaulting to rule")

    if not skill.supports_llm():
        return PlannerChoice("rule", f"skill `{skill_name}` doesn't support LLM planning")

    if prefer_llm:
        return PlannerChoice("llm", "user preference: prefer LLM by default")

    matched = _contains_any(goal_clean, _LLM_INTENT_KEYWORDS)
    if matched:
        return PlannerChoice("llm", f"goal has semantic intent ('{matched}')")

    compound, compound_reason = is_compound_goal(goal_clean)
    if compound:
        return PlannerChoice("llm", f"compound goal needs LLM — {compound_reason}")

    return PlannerChoice(
        "rule",
        "rule planner is enough — goal has no semantic-intent or multi-step marker",
    )


def detect_capability_gap(
    goal: str,
    skill_name: str,
    snapshot: WorkspaceSnapshot | None,
) -> CapabilityGap | None:
    """Return a non-None warning when the chosen skill can't deliver
    every part of what the goal asks for.

    The cases this helper catches today (extend as more skills land):

      * Goal asks for organize-AND-chart but only one skill was picked
        — neither folder_organizer (organize-only) nor
        workspace_visualizer (chart-only) covers both. Recommend
        running them in sequence.
      * Goal asks for chart on tabular data but the chosen skill is
        data_reporter (markdown-only, no real PNG).
      * Goal asks for chart on workspace metadata but routed to
        anything other than workspace_visualizer.

    Returns ``None`` when the routing is a clean fit.
    """
    goal_clean = (goal or "").strip()
    if not goal_clean:
        return None

    has_chart = _contains_any(goal_clean, _CHART_KEYWORDS) is not None
    has_organize = _contains_any(goal_clean, _ORGANIZE_KEYWORDS) is not None
    counts = _file_type_counts(snapshot)
    has_tabular = _has_tabular(counts)

    # Case 1 — compound organize+chart but only one skill picked.
    if has_chart and has_organize:
        if skill_name == "folder_organizer":
            return CapabilityGap(
                message=(
                    "Goal asks for both organize AND chart. folder_organizer "
                    "will only do the organize part. To get the chart, run "
                    "workspace_visualizer as a second task after this one "
                    "completes."
                ),
                suggested_skill="workspace_visualizer",
            )
        if skill_name == "workspace_visualizer":
            return CapabilityGap(
                message=(
                    "Goal asks for both organize AND chart. "
                    "workspace_visualizer will only draw the chart. To get the "
                    "organize part, run folder_organizer first."
                ),
                suggested_skill="folder_organizer",
            )

    # Case 2 — chart requested but skill writes only markdown.
    if has_chart and skill_name == "data_reporter":
        return CapabilityGap(
            message=(
                "data_reporter writes a markdown summary, not real PNG charts. "
                "For an actual chart image, switch to data_analyzer (tabular "
                "data) or workspace_visualizer (file counts)."
            ),
            suggested_skill=("data_analyzer" if has_tabular else "workspace_visualizer"),
        )

    # Case 3 — chart on metadata but not routed to workspace_visualizer
    # (and no tabular data to justify data_analyzer).
    if (
        has_chart
        and not has_tabular
        and skill_name
        not in (
            "workspace_visualizer",
            "data_analyzer",
        )
    ):
        return CapabilityGap(
            message=(
                f"Goal mentions a chart but `{skill_name}` can't draw one. "
                "Switch to workspace_visualizer for a real PNG of file counts."
            ),
            suggested_skill="workspace_visualizer",
        )

    return None
