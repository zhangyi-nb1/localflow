"""Auto-detect the right skill + planner from a goal + workspace.

Phase 8.1 / v0.8.0. Replaces the previous Plan-page UX where users
had to pick a Skill and a Planner manually. Now the user writes a
goal in plain English or Chinese; this module picks both based on a
mixture of:

  * Goal keywords (bilingual) — intent signals
  * Workspace file-type distribution — capability signals (you can't
    run ``data_reporter`` without tabular files)
  * Skill's declared ``supports_llm()`` — gates the planner choice

The module imports neither Streamlit nor app.harness so it can be
unit-tested in isolation. The caller (Plan page) wires the workspace
snapshot + skill registry in.

The heuristic is intentionally simple and explainable — every decision
returns a short string the UI surfaces to the user so they can
override if it picked wrong. The Plan page keeps a collapsed Override
expander for that 5% case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.schemas import WorkspaceSnapshot
from app.skills import SkillRegistry

# Keyword lists (bilingual). Each tuple is (keyword_pattern, kind).
# Patterns are case-insensitive substrings — exact regex would be
# overkill and harder for non-native-English speakers to predict.

_DATA_ANALYZE_KEYWORDS = (
    "analy",
    "groupby",
    "group by",
    "filter",
    "chart",
    "plot",
    "visuali",
    "aggregate",
    "pivot",
    "regression",
    "correlation",
    "trend",
    "分析",
    "图表",
    "可视化",
    "聚合",
    "趋势",
    "相关",
)

_DATA_REPORT_KEYWORDS = (
    "report",
    "stats",
    "statistic",
    "summary",
    "summarize",
    "describe",
    "dashboard",
    "overview",
    "metrics",
    "报告",
    "统计",
    "汇总",
    "概况",
    "总结",
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

# How many tabular-class files (csv / tsv / parquet / xlsx) we want to
# see before considering data_* skills as a strong candidate.
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


def _contains_any(text: str, keywords: Iterable[str]) -> str | None:
    """Return the first keyword in ``keywords`` found inside ``text``
    (case-insensitive substring), or None."""
    low = text.lower()
    for kw in keywords:
        if kw in low:
            return kw
    return None


def _file_type_counts(snapshot: WorkspaceSnapshot | None) -> dict[str, int]:
    """Map file_type → count from a snapshot. Empty dict if no snapshot."""
    counts: dict[str, int] = {}
    if snapshot is None:
        return counts
    for f in snapshot.files:
        counts[f.file_type] = counts.get(f.file_type, 0) + 1
    return counts


def _has_tabular(counts: dict[str, int]) -> bool:
    """tabular = csv/tsv/parquet. xlsx falls under "excel"; include it
    too so a workspace of spreadsheets still routes to data_*."""
    n = counts.get("tabular", 0) + counts.get("excel", 0)
    return n >= _MIN_TABULAR_FOR_DATA_SKILLS


def _has_pdfs(counts: dict[str, int]) -> bool:
    return counts.get("pdf", 0) >= _MIN_PDF_FOR_PDF_SKILL


def autodetect_skill(
    goal: str,
    snapshot: WorkspaceSnapshot | None,
    registry: SkillRegistry,
) -> SkillChoice:
    """Pick the best skill for the goal + workspace.

    Priority order (strongest signal first):

      1. ``data_analyzer`` — tabular files + analysis intent
      2. ``data_reporter`` — tabular files + report intent
      3. ``pdf_indexer``   — pdf files + pdf/index intent
      4. ``folder_organizer`` — organize intent (any workspace)
      5. ``data_analyzer`` (fallback when tabular without intent — they
         clearly want to do something with data)
      6. ``pdf_indexer`` (fallback when pdfs without intent)
      7. ``folder_organizer`` (universal fallback)

    Each rung includes the data + intent signal; the lower rungs catch
    "I have these files but no clear keyword" cases.

    Returns a :class:`SkillChoice` where ``reason`` is a short
    user-facing string — the UI surfaces it next to the auto-detected
    badge so the user can sanity-check.
    """
    goal_clean = (goal or "").strip()
    counts = _file_type_counts(snapshot)
    available = set(registry.list_names())

    tabular_n = counts.get("tabular", 0) + counts.get("excel", 0)
    pdf_n = counts.get("pdf", 0)

    def pick(name: str, reason: str, fallback: str = "folder_organizer") -> SkillChoice:
        if name in available:
            return SkillChoice(name=name, reason=reason)
        # Fall back if the chosen skill isn't registered (shouldn't happen
        # for built-ins, but external skill discovery may differ across
        # installs).
        return SkillChoice(name=fallback, reason=f"{reason} (fallback — {name} not registered)")

    if not goal_clean:
        return pick(
            "folder_organizer",
            "default skill; tell me your goal to refine the choice",
        )

    if _has_tabular(counts):
        if _contains_any(goal_clean, _DATA_ANALYZE_KEYWORDS):
            return pick(
                "data_analyzer",
                f"{tabular_n} tabular file(s) + analysis keyword in goal",
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

    if _contains_any(goal_clean, _ORGANIZE_KEYWORDS):
        return pick(
            "folder_organizer",
            "goal mentions organize/sort/categorize",
        )

    # No clean keyword but the workspace content is itself a strong hint.
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
) -> PlannerChoice:
    """Pick rule vs llm for a (goal, skill) pair.

    Rules:
      * If the skill doesn't override ``plan_with_llm`` → ``rule``.
      * If the goal contains an LLM-intent keyword (semantic / content /
        topic / 智能 / 语义 / etc.) AND the skill supports LLM → ``llm``.
      * Otherwise → ``rule`` (faster, deterministic, free).
    """
    goal_clean = (goal or "").strip()
    skill = registry.get(skill_name)
    if skill is None:
        return PlannerChoice("rule", f"unknown skill `{skill_name}` — defaulting to rule")

    if not skill.supports_llm():
        return PlannerChoice("rule", f"skill `{skill_name}` doesn't support LLM planning")

    matched = _contains_any(goal_clean, _LLM_INTENT_KEYWORDS)
    if matched:
        return PlannerChoice("llm", f"goal has semantic intent ('{matched}')")

    return PlannerChoice(
        "rule",
        "rule planner is enough — goal has no semantic-intent keyword",
    )
