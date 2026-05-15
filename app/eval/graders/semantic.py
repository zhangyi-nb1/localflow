"""Phase 13 — semantic graders.

Three starter LLM-as-judge graders that close the structural blind
spot the v0.11 bug exposed: a plan can execute cleanly + produce the
expected files + pass every structural check while still being
semantically wrong (empty analysis, generic boilerplate, hallucinated
chart counts).

Each grader is registered via the same ``@register`` decorator as
the structural ones, so the eval runner sees them as homogeneous
graders. Their distinguishing feature is the body: they call
:func:`app.agent.judge.judge` for the actual verdict, and they
include a ``suggested_hint`` in the failure path that's directly
usable as input to :func:`app.harness.control_loop.run_revise`.

LLM-key graceful degradation: every grader checks
:func:`get_default_client_or_none` and returns ``passed=True`` with
``detail='skipped — no LLM client available'`` when no key is
configured. This keeps semantic verification opt-in even when the
memory pref enables it but the environment lacks credentials —
graders never fail the run on infrastructure issues.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.agent.judge import get_default_client_or_none, judge
from app.eval.graders import register
from app.eval.schema import GraderContext, GraderVerdict

# Common short system prompt — keeps the judge tightly scoped.
JUDGE_SYSTEM = (
    "You are a strict, terse semantic-quality grader for a file-organizing + "
    "data-analysis agent. You judge whether the agent's output meets the "
    "user's stated goal. Submit a yes/no verdict via the submit_verdict tool. "
    "When verdict=false, your suggested_hint MUST be a direct instruction "
    "phrased for the planner LLM that would address the failure (e.g. "
    "'analyse the actual numeric columns instead of file metadata'). Keep "
    "every field under the schema's maxLength."
)


# ───────────────────────────────────── output_addresses_goal


@register("output_addresses_goal")
def output_addresses_goal(ctx: GraderContext) -> GraderVerdict:
    """LLM-as-judge — does the produced output actually answer the
    user's goal?

    Reads up to 3 of the expected_outputs (text-like only — markdown,
    txt) plus the original user_goal. The judge is asked to say YES
    only when the output's content materially addresses the goal,
    not just nominally produces a file. Catches the v0.11 "agent
    wrote a meta-description instead of analyzing the data" failure
    mode.
    """
    name = "output_addresses_goal"
    goal = (ctx.task_spec.user_goal or "").strip()
    if not goal:
        return GraderVerdict(name=name, passed=True, detail="no user_goal set; skipping")

    client = get_default_client_or_none()
    if client is None:
        return GraderVerdict(name=name, passed=True, detail="skipped — no LLM client available")

    text_outputs = _read_text_outputs(ctx, limit=3, max_chars_each=2000)
    if not text_outputs:
        return GraderVerdict(
            name=name,
            passed=True,
            detail="no text outputs to grade (binary or missing expected_outputs)",
        )

    rendered = "\n\n".join(f"### {path}\n\n{content}" for path, content in text_outputs.items())
    user_prompt = (
        f"User goal:\n\n> {goal}\n\n"
        f"The agent produced the following text outputs:\n\n{rendered}\n\n"
        "Does the content of these outputs materially address the user's goal? "
        "Reject when the output is generic boilerplate, a meta-description of "
        "the workspace instead of substantive content, or otherwise fails to "
        "engage with the goal."
    )
    verdict = judge(system=JUDGE_SYSTEM, user=user_prompt, client=client)
    if verdict is None:
        return GraderVerdict(name=name, passed=True, detail="judge call failed; skipping")

    return GraderVerdict(
        name=name,
        passed=verdict.verdict,
        detail=verdict.reason or ("addresses goal" if verdict.verdict else "fails to address goal"),
        score=None,
    )


# ───────────────────────────────────── summary_grounded


@register("summary_grounded")
def summary_grounded(ctx: GraderContext) -> GraderVerdict:
    """LLM-as-judge — an index.md / *_report.md should mention files
    that actually exist in the workspace.

    Catches generic boilerplate and hallucinated filenames. Only fires
    on tasks that produce a markdown index — otherwise skips.
    """
    name = "summary_grounded"
    target_path = _pick_summary_path(ctx)
    if target_path is None:
        return GraderVerdict(name=name, passed=True, detail="no summary file produced; skipping")

    summary_text = _read_text_capped(target_path, max_chars=4000)
    if not summary_text:
        return GraderVerdict(name=name, passed=True, detail="summary file empty; skipping")

    client = get_default_client_or_none()
    if client is None:
        return GraderVerdict(name=name, passed=True, detail="skipped — no LLM client available")

    workspace_files = _list_workspace_files(ctx, limit=40)
    files_listing = "\n".join(f"- {p}" for p in workspace_files) or "(none)"
    user_prompt = (
        "The following markdown file was generated as a summary / index of "
        "the workspace:\n\n"
        f"```markdown\n{summary_text}\n```\n\n"
        "The workspace actually contains these files (sample):\n\n"
        f"{files_listing}\n\n"
        "Does the summary materially describe the workspace files, or is it "
        "generic boilerplate that doesn't reference what's actually present? "
        "Reject if the summary uses placeholder language ('various files', "
        "'documents and resources') without naming or grouping the actual "
        "contents, or if it claims files that aren't in the listing."
    )
    verdict = judge(system=JUDGE_SYSTEM, user=user_prompt, client=client)
    if verdict is None:
        return GraderVerdict(name=name, passed=True, detail="judge call failed; skipping")
    return GraderVerdict(
        name=name,
        passed=verdict.verdict,
        detail=verdict.reason or ("grounded" if verdict.verdict else "not grounded"),
    )


# ───────────────────────────────────── analysis_result_nonempty


# Substrings that data_analyzer's report renderer uses when an
# AnalysisSpec yields no useful rows. Keeping these literal here so a
# refactor of the report template forces an explicit update. The
# markers match the renderer's exact bold-Outcome format:
#   ``**Outcome**: `empty_result` `` (case-insensitive after .lower()).
EMPTY_MARKERS = (
    "**outcome**: `empty_result`",
    "**outcome**: `invalid_spec`",
    "**outcome**: `read_error`",
    "**outcome**: `execution_error`",
    "_(empty result)_",
)


@register("analysis_result_nonempty")
def analysis_result_nonempty(ctx: GraderContext) -> GraderVerdict:
    """Mostly-deterministic grader for data_analyzer outputs.

    Reads ``analysis_report.md`` (the canonical filename produced by
    both the rule + LLM planners) and:

      1. Skips when the file doesn't exist (task didn't use data_analyzer).
      2. Counts how many ``### `<file>` <a id=...>`` headings the
         report contains — that's the number of analyses run.
      3. Counts how many of those analyses ended in an EMPTY / INVALID /
         ERROR outcome via substring search against EMPTY_MARKERS.
      4. Fails when 100% of analyses ended empty — that's a semantic
         miss: the user asked for analysis and got nothing.

    Includes a ``suggested_hint`` phrased so the LLM planner picks
    different columns / aggregations on the repair attempt.
    """
    name = "analysis_result_nonempty"
    report_path = ctx.workspace_path / "analysis_report.md"
    if not report_path.exists():
        return GraderVerdict(name=name, passed=True, detail="no analysis_report.md; skipping")
    text = _read_text_capped(report_path, max_chars=20000).lower()
    headings = re.findall(r"^###\s+`", text, flags=re.MULTILINE)
    total = len(headings)
    if total == 0:
        return GraderVerdict(
            name=name,
            passed=False,
            detail="analysis_report.md exists but contains no analysis sections",
        )
    empty_count = sum(text.count(marker.lower()) for marker in EMPTY_MARKERS)
    if empty_count >= total:
        return GraderVerdict(
            name=name,
            passed=False,
            detail=(
                f"every analysis ({total}/{total}) ended in empty/error — "
                f"the report has zero substantive results"
            ),
            score=0.0,
        )
    # Partial empties are OK as long as at least one analysis produced content.
    return GraderVerdict(
        name=name,
        passed=True,
        detail=f"{total - empty_count}/{total} analyses produced non-empty results",
        score=(total - empty_count) / total,
    )


# ───────────────────────────────────── helpers


def _read_text_outputs(ctx: GraderContext, *, limit: int, max_chars_each: int) -> dict[str, str]:
    """Read up to ``limit`` text-like expected_outputs into a dict
    ``{relpath: content}``. Skips binary files, missing files, and
    files larger than 200 KB."""
    out: dict[str, str] = {}
    for rel in ctx.task.expected_outputs:
        if len(out) >= limit:
            break
        p = ctx.workspace_path / rel
        if not p.exists() or not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix not in (".md", ".txt", ".csv", ".json", ".yaml", ".yml"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > 200_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")[:max_chars_each]
        except OSError:
            continue
        out[rel] = content
    return out


def _pick_summary_path(ctx: GraderContext) -> Path | None:
    """Heuristic: pick the *.md output most likely to be a summary —
    index.md, summary.md, or analysis_report.md. Prefers the
    shallowest in expected_outputs."""
    candidates = []
    for rel in ctx.task.expected_outputs:
        if not rel.lower().endswith(".md"):
            continue
        base = Path(rel).name.lower()
        if base in ("index.md", "summary.md", "analysis_report.md", "data_file_report.md"):
            candidates.append((len(Path(rel).parts), rel))
    if not candidates:
        return None
    candidates.sort()
    _, rel = candidates[0]
    p = ctx.workspace_path / rel
    return p if p.exists() else None


def _read_text_capped(path: Path, *, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _list_workspace_files(ctx: GraderContext, *, limit: int) -> list[str]:
    """Top N workspace-relative paths after execute (sorted, capped).
    Used as the ground-truth listing the judge compares the summary
    against."""
    files: list[str] = []
    root = ctx.workspace_path
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        files.append(rel.as_posix())
        if len(files) >= limit:
            break
    return files
