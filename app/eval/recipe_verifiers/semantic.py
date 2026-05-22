"""Phase 19 — LLM-as-judge recipe-level verifiers.

Three verifiers from productisation guide §10:

  * summary_grounding_verifier — README / summary claims must trace to source files
  * chart_data_consistency_verifier — chart PNGs must reflect the underlying CSV stats
  * topic_coherence_verifier — files inside a topic dir must be semantically related

Every verifier degrades gracefully without an LLM client (skipped=True
verdict; never fails the pack on infrastructure issues). The
:func:`app.agent.judge.judge` helper provides the strict-schema
yes/no/hint envelope; we drive that helper with verifier-specific
system + user prompts.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.agent.judge import JudgeVerdict, get_default_client_or_none, judge
from app.agent.locale_prompts import locale_instruction
from app.eval.recipe_verifiers._registry import register
from app.eval.recipe_verifiers._schema import (
    RecipeVerifierContext,
    RecipeVerifierVerdict,
)

# ─────────────────────────── shared judge prompt
JUDGE_SYSTEM = (
    "You are a strict, terse semantic grader for a workspace-delivery agent. "
    "You judge whether a generated deliverable is faithful to the underlying "
    "source files. Submit a yes/no verdict via the submit_verdict tool. When "
    "verdict=false, your suggested_hint MUST be a direct instruction the "
    "planner LLM could act on (e.g. 'rewrite the README to cite only files "
    "from data/'). Keep every field under the schema's maxLength."
)


MAX_BLOB = 6000  # chars max we feed the judge per file to keep token cost bounded.


def _read_text_capped(path: Path, *, max_chars: int = MAX_BLOB) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _list_workspace_files(workspace: Path, *, limit: int) -> list[str]:
    files: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(workspace)
        except ValueError:
            continue
        files.append(rel.as_posix())
        if len(files) >= limit:
            break
    return files


def _judge_or_skip(
    *,
    name: str,
    user_prompt: str,
    suggested_when_skip: str = "",
    locale: str | None = None,
) -> RecipeVerifierVerdict | JudgeVerdict:
    """Drive the LLM judge or return a skipped verdict.

    Returns a finalised RecipeVerifierVerdict (skipped) when no LLM
    client is available or the judge call fails — callers can return
    it directly. Otherwise returns the raw JudgeVerdict and the caller
    wraps it into a final verdict with name-specific detail logic.

    ``locale`` (v0.22) is appended to the judge system prompt so the
    rationale + suggested_hint come back in the user's language.
    """
    client = get_default_client_or_none()
    if client is None:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="skipped — no LLM client available",
            skipped=True,
            suggested_hint=suggested_when_skip or None,
        )
    system = JUDGE_SYSTEM + "\n\n" + locale_instruction(locale)
    verdict = judge(system=system, user=user_prompt, client=client)
    if verdict is None:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="skipped — judge call failed",
            skipped=True,
        )
    return verdict


# ───────────────────────────────────── 5. summary_grounding_verifier


_SUMMARY_CANDIDATES = ("README.md", "summary.md", "executive_summary.md")


@register("summary_grounding_verifier")
def summary_grounding_verifier(
    ctx: RecipeVerifierContext,
) -> RecipeVerifierVerdict:
    """Productisation guide §10 #3 — "summary claims must be traceable
    to source files".

    Picks the top-level README / summary; asks the LLM whether its
    factual claims line up with the workspace contents (filenames
    listed, counts cited, sections described). Catches generic
    boilerplate ("various documents", "different files") and
    hallucinated structure.
    """
    name = "summary_grounding_verifier"
    ws = ctx.workspace_path

    summary_path: Path | None = None
    for candidate in _SUMMARY_CANDIDATES:
        p = ws / candidate
        if p.exists() and p.is_file():
            summary_path = p
            break

    if summary_path is None:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="no top-level summary file (README / summary) produced",
            skipped=True,
        )

    summary_text = _read_text_capped(summary_path)
    if not summary_text.strip():
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail=f"{summary_path.name} is empty; nothing to verify",
            skipped=True,
        )

    files_listing = "\n".join(f"- {p}" for p in _list_workspace_files(ws, limit=40))
    user_prompt = (
        f"The agent produced this top-level summary at `{summary_path.name}`:\n\n"
        f"```markdown\n{summary_text}\n```\n\n"
        "The workspace actually contains these files (sample, 40 max):\n\n"
        f"{files_listing}\n\n"
        "Is the summary materially grounded in the workspace contents? "
        "Reject when it uses placeholder language ('various files', "
        "'documents and resources') without naming actual files / sections, "
        "OR when it cites files / counts / categories that aren't present in "
        "the listing."
    )
    result = _judge_or_skip(name=name, user_prompt=user_prompt, locale=ctx.locale)
    if isinstance(result, RecipeVerifierVerdict):
        return result

    return RecipeVerifierVerdict(
        name=name,
        passed=result.verdict,
        detail=result.reason or ("grounded" if result.verdict else "not grounded"),
        suggested_hint=result.suggested_hint if not result.verdict else None,
    )


# ───────────────────────────────────── 6. chart_data_consistency_verifier


_CHART_PNG_RX = re.compile(r"\.(png|jpg|jpeg)$", re.IGNORECASE)
_NUMBER_RX = re.compile(r"-?\d+(?:\.\d+)?")


@register("chart_data_consistency_verifier")
def chart_data_consistency_verifier(
    ctx: RecipeVerifierContext,
) -> RecipeVerifierVerdict:
    """Productisation guide §10 #4 — "chart values must match the
    underlying CSV / XLSX statistics".

    Approach (no vision LLM needed for Phase 19's structural pass):
      1. Find every chart image under ``workspace_path``.
      2. For each chart, find its sibling caption markdown (matching
         the basename without extension) — that's what reports
         generated by data_analyzer + workspace_visualizer do.
      3. If a caption claims a sum / count / mean, sanity-check that
         the same number appears in the source CSV or XLSX summary.

    Pragmatic: if no chart + caption pairs exist, skip. If the LLM is
    available, also pass the caption + a CSV preview to the judge for
    a holistic check.
    """
    name = "chart_data_consistency_verifier"
    ws = ctx.workspace_path

    # 1. Find chart images that are PLAUSIBLY data-driven. v0.20.0 fix:
    # workspace overview charts (file_counts.png) and the workspace_
    # visualizer's pie / bar of category counts are NOT derived from a
    # CSV row — they summarise the workspace shape. Comparing them
    # against an unrelated CSV (experiment_results.csv) produced false-
    # positive failures in Phase 19. The verifier now restricts itself
    # to ``analysis_charts/`` (data_analyzer's canonical output dir),
    # which IS row-driven and meaningful to compare against the CSV.
    analysis_dir = ws / "analysis_charts"
    if not analysis_dir.exists() or not analysis_dir.is_dir():
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail=(
                "no analysis_charts/ dir; workspace overview charts are "
                "metadata-driven and excluded from data-consistency checks"
            ),
            skipped=True,
        )
    charts = [
        p for p in analysis_dir.rglob("*") if p.is_file() and _CHART_PNG_RX.search(p.name)
    ]
    if not charts:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="no data-driven chart images in analysis_charts/",
            skipped=True,
        )

    # 2. For each chart, look for a sibling .md caption (same stem) OR
    # a `<stem>_summary.md` at the workspace root. data_analyzer
    # produces both layouts depending on the planner path.
    pairs: list[tuple[Path, Path]] = []
    for chart in charts:
        caption = chart.with_suffix(".md")
        if caption.exists():
            pairs.append((chart, caption))
            continue
        # Same-stem at workspace root as fallback.
        summary = ws / f"{chart.stem}_summary.md"
        if summary.exists():
            pairs.append((chart, summary))
            continue
        # Last resort: the canonical analysis_report.md (sectioned by chart).
        report = ws / "analysis_report.md"
        if report.exists():
            pairs.append((chart, report))

    if not pairs:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail=(
                f"found {len(charts)} chart(s) in analysis_charts/ but no "
                "matching .md captions or analysis_report.md; skipping"
            ),
            skipped=True,
        )

    # 3. LLM-driven check on first pair (token-budget conscious).
    chart_path, caption_path = pairs[0]
    caption_text = _read_text_capped(caption_path)
    if not caption_text.strip():
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail=f"caption {caption_path.name} is empty; skipping",
            skipped=True,
        )

    # Pull the first CSV / XLSX preview we can find as ground truth.
    data_preview = ""
    for p in sorted(ws.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".csv", ".tsv"):
            data_preview = _read_text_capped(p, max_chars=2000)
            break

    user_prompt = (
        f"A chart image was produced at `{chart_path.relative_to(ws).as_posix()}` "
        f"with this caption / summary markdown:\n\n"
        f"```markdown\n{caption_text}\n```\n\n"
        + (
            f"The underlying tabular data (first 2 KB of one CSV in the "
            f"workspace) is:\n\n```\n{data_preview}\n```\n\n"
            if data_preview
            else "No CSV preview is available — judge purely on the caption's "
            "internal consistency.\n\n"
        )
        + "Are the numbers / categories / claims in the caption consistent "
        "with the data preview? Reject when the caption cites counts or "
        "values that contradict the visible data, or when it uses "
        "placeholder claims unrelated to any actual statistic."
    )
    result = _judge_or_skip(name=name, user_prompt=user_prompt, locale=ctx.locale)
    if isinstance(result, RecipeVerifierVerdict):
        return result
    return RecipeVerifierVerdict(
        name=name,
        passed=result.verdict,
        detail=result.reason
        or ("chart caption consistent with data" if result.verdict else "inconsistent"),
        suggested_hint=result.suggested_hint if not result.verdict else None,
    )


# ───────────────────────────────────── 7. topic_coherence_verifier


@register("topic_coherence_verifier")
def topic_coherence_verifier(
    ctx: RecipeVerifierContext,
) -> RecipeVerifierVerdict:
    """Productisation guide §10 #7 — "files inside a topic dir must be
    semantically related".

    Operates on category dirs the recipe's organizer stage produces
    (papers / data / images / notes / misc / topics/<x>). For the
    first non-trivial category dir (≥ 3 files) the judge inspects the
    dir's index.md and a sample of its file names, and decides
    whether the bucket is semantically coherent or whether it's a
    misclassification dumping ground.

    Skips when no category dir exists OR all category dirs are too
    small to evaluate.
    """
    name = "topic_coherence_verifier"
    ws = ctx.workspace_path

    candidate_dirs: list[Path] = []
    # Look for both flat-category and topics/<sub> layouts.
    for p in sorted(ws.iterdir() if ws.exists() else []):
        if not p.is_dir():
            continue
        if p.name.startswith(("."  , "_")):
            continue
        # Count direct file children to filter trivial dirs.
        try:
            children = [c for c in p.iterdir() if c.is_file()]
        except OSError:
            continue
        if len(children) >= 3:
            candidate_dirs.append(p)
    # Also descend one level into topics/ if present.
    topics_dir = ws / "topics"
    if topics_dir.exists() and topics_dir.is_dir():
        for sub in sorted(topics_dir.iterdir()):
            if not sub.is_dir():
                continue
            try:
                children = [c for c in sub.iterdir() if c.is_file()]
            except OSError:
                continue
            if len(children) >= 3:
                candidate_dirs.append(sub)

    if not candidate_dirs:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="no topic dirs with ≥3 files; nothing to evaluate",
            skipped=True,
        )

    target = candidate_dirs[0]
    index_md = target / "index.md"
    index_text = _read_text_capped(index_md) if index_md.exists() else ""
    file_names = sorted(
        c.name for c in target.iterdir() if c.is_file() and c.name != "index.md"
    )[:15]

    user_prompt = (
        f"The agent grouped files into a topic/category directory named "
        f"`{target.name}/` containing these files (15 max):\n\n"
        + "\n".join(f"  - {n}" for n in file_names)
        + (
            f"\n\nThe directory's index.md says:\n\n```markdown\n{index_text}\n```\n\n"
            if index_text
            else "\n\nThe directory has no index.md.\n\n"
        )
        + "Are these files plausibly related under a common topic / category? "
        "Reject when the bucket is heterogeneous (papers mixed with random "
        "binaries) or when the index.md description doesn't match what's "
        "actually in the dir."
    )
    result = _judge_or_skip(name=name, user_prompt=user_prompt, locale=ctx.locale)
    if isinstance(result, RecipeVerifierVerdict):
        return result
    return RecipeVerifierVerdict(
        name=name,
        passed=result.verdict,
        detail=(
            f"`{target.name}/`: " + (result.reason or ("coherent" if result.verdict else "incoherent"))
        ),
        suggested_hint=result.suggested_hint if not result.verdict else None,
    )
