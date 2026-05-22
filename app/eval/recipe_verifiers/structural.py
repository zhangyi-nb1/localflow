"""Phase 19 — deterministic recipe-level verifiers (no LLM).

Four verifiers from productisation guide §10:

  * coverage_verifier — every input file is moved OR cited in a .md
  * source_ledger_verifier — paths in SOURCES.md actually exist
  * review_queue_verifier — low-confidence files surface in review/
  * deliverable_completeness_verifier — every recipe.expected_output exists

All four are pure file inspection — fast, free, runnable in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.eval.recipe_verifiers._registry import register
from app.eval.recipe_verifiers._schema import (
    RecipeVerifierContext,
    RecipeVerifierVerdict,
)

# Maximum file size we'll read for content scanning. Anything bigger
# is unlikely to be a generated report and inflates verifier latency.
MAX_READ_BYTES = 500_000


def _read_text(path: Path) -> str:
    """Bounded text read — returns "" on any error or oversized file."""
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _iter_md_files(workspace: Path):
    for p in workspace.rglob("*.md"):
        if p.is_file():
            yield p


# ───────────────────────────────────── 1. coverage_verifier


@register("coverage_verifier")
def coverage_verifier(ctx: RecipeVerifierContext) -> RecipeVerifierVerdict:
    """Productisation guide §10 #1 — "every input file is either moved
    or surfaces in a generated report".

    Logic mirrors Phase 14's ``every_input_accounted_for`` grader but
    runs at the **pack** level: it consumes the runner's aggregated
    `moves` map instead of inspecting a single skill's plan.

    Each input file from ``snapshot_inputs`` must be either:
      (a) moved to a target the runner recorded AND that target exists
          on disk, OR
      (b) mentioned by basename in any generated ``*.md`` file in the
          workspace (pdf_index, analysis_report, per-category index,
          README, SOURCES, …).

    Files that vanish without either signal fail — they're the
    "silently dropped during the pipeline" scenario the guide flags.
    """
    inputs = list(ctx.snapshot_inputs)
    if not inputs:
        return RecipeVerifierVerdict(
            name="coverage_verifier",
            passed=True,
            detail="no input files to track",
            skipped=True,
        )

    ws = ctx.workspace_path
    moves = ctx.moves

    md_blob = "\n".join(_read_text(p) for p in _iter_md_files(ws))

    unaccounted: list[str] = []
    for original in sorted(inputs):
        moved_to = moves.get(original)
        if moved_to is not None and (ws / moved_to).exists():
            continue
        basename = original.rsplit("/", 1)[-1]
        if basename and basename in md_blob:
            continue
        unaccounted.append(original)

    if unaccounted:
        return RecipeVerifierVerdict(
            name="coverage_verifier",
            passed=False,
            detail=(
                f"{len(unaccounted)}/{len(inputs)} input file(s) neither moved "
                f"nor cited: {', '.join(unaccounted[:5])}"
                + (f", …(+{len(unaccounted) - 5})" if len(unaccounted) > 5 else "")
            ),
            score=(len(inputs) - len(unaccounted)) / len(inputs),
            suggested_hint=(
                "Re-plan so every input file is either moved to a category "
                "directory OR named in an index / report markdown."
            ),
        )
    return RecipeVerifierVerdict(
        name="coverage_verifier",
        passed=True,
        detail=f"all {len(inputs)} input(s) accounted for (moved or cited)",
        score=1.0,
    )


# ───────────────────────────────────── 2. source_ledger_verifier

# Lines like:    - `papers/foo.pdf` (4321 B, sha256:abcd…)
# or:            * paper.pdf — see papers/
# We extract every ``path-like`` token and check it exists under the workspace.
_LEDGER_FILE_RX = re.compile(
    r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+)`",  # backticked path-with-extension
)


@register("source_ledger_verifier")
def source_ledger_verifier(ctx: RecipeVerifierContext) -> RecipeVerifierVerdict:
    """Productisation guide §10 #2 — "every source cited in the
    deliverable should resolve to a real file".

    Reads ``SOURCES.md`` (the canonical source ledger filename
    produced by recipes that include an ``agent`` synthesis stage).
    Extracts every backticked path-like token and asserts each one
    resolves to a real file under ``workspace_path``.

    Skips cleanly when no SOURCES.md was produced (recipe doesn't
    declare it or the LLM stage skipped due to no key).
    """
    ledger = ctx.workspace_path / "SOURCES.md"
    if not ledger.exists():
        return RecipeVerifierVerdict(
            name="source_ledger_verifier",
            passed=True,
            detail="no SOURCES.md produced; nothing to verify",
            skipped=True,
        )

    text = _read_text(ledger)
    cited = set(_LEDGER_FILE_RX.findall(text))
    if not cited:
        return RecipeVerifierVerdict(
            name="source_ledger_verifier",
            passed=True,
            detail="SOURCES.md present but contains no path citations",
            skipped=True,
        )

    missing: list[str] = []
    for rel in sorted(cited):
        if not (ctx.workspace_path / rel).exists():
            missing.append(rel)

    if missing:
        return RecipeVerifierVerdict(
            name="source_ledger_verifier",
            passed=False,
            detail=(
                f"{len(missing)}/{len(cited)} citation(s) point at files that "
                f"don't exist: {', '.join(missing[:5])}"
                + (f", …(+{len(missing) - 5})" if len(missing) > 5 else "")
            ),
            score=(len(cited) - len(missing)) / len(cited),
            suggested_hint=(
                "Regenerate SOURCES.md and only cite files you can verify "
                "exist in the produced pack; do not invent paths."
            ),
        )
    return RecipeVerifierVerdict(
        name="source_ledger_verifier",
        passed=True,
        detail=f"all {len(cited)} citation(s) resolve to real files",
        score=1.0,
    )


# ───────────────────────────────────── 3. review_queue_verifier


@register("review_queue_verifier")
def review_queue_verifier(ctx: RecipeVerifierContext) -> RecipeVerifierVerdict:
    """Productisation guide §10 #5 — "low-confidence files should
    surface in review/, not be force-classified".

    Two heuristics, OR-combined:
      (a) Files in the workspace whose extension is unknown to the
          folder_organizer's curated table (the same one the
          ``classify_content`` primitive uses) should end up either
          (i) in a ``review/`` directory, OR (ii) cited in a
          ``review/*.md`` report.
      (b) If the recipe explicitly enables the
          ``route_low_confidence_to_review`` preference and the
          workspace has ANY review/ contents, that counts as the
          pack honouring the preference.

    Passes trivially when the workspace contains no unclassifiable
    files. Recipes that opt into low-confidence routing have a higher
    bar than those that don't — but neither path is mandatory.
    """
    ws = ctx.workspace_path

    known_exts = {
        ".pdf", ".doc", ".docx",  # paper-ish
        ".csv", ".tsv", ".xlsx", ".xls", ".parquet",  # tabular
        ".md", ".markdown", ".txt", ".rst",  # notes
        ".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp",  # code
        ".json", ".yaml", ".yml", ".xml", ".toml", ".ini",  # structured
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",  # images
    }

    # Inputs whose extension wasn't in the curated set. We look at
    # PRE-run inputs because post-run those files have been moved.
    unknown_inputs: list[str] = []
    for rel in ctx.snapshot_inputs:
        _, _, ext = rel.lower().rpartition(".")
        ext = f".{ext}" if ext else ""
        if ext not in known_exts:
            unknown_inputs.append(rel)

    if not unknown_inputs:
        return RecipeVerifierVerdict(
            name="review_queue_verifier",
            passed=True,
            detail="no unclassifiable inputs in workspace",
            skipped=True,
        )

    review_dir = ws / "review"
    review_dir_has_content = review_dir.exists() and any(review_dir.iterdir())

    # Pull any review/*.md text for citation checks.
    review_md_blob = ""
    if review_dir.exists():
        for md in review_dir.rglob("*.md"):
            review_md_blob += "\n" + _read_text(md)

    forced: list[str] = []
    for rel in unknown_inputs:
        basename = rel.rsplit("/", 1)[-1]
        moved_to = ctx.moves.get(rel)
        in_review = (
            moved_to is not None and moved_to.startswith("review/")
        ) or (basename in review_md_blob)
        if not in_review:
            forced.append(rel)

    if forced:
        return RecipeVerifierVerdict(
            name="review_queue_verifier",
            passed=False,
            detail=(
                f"{len(forced)}/{len(unknown_inputs)} unclassifiable file(s) "
                f"were force-classified instead of routed to review/: "
                f"{', '.join(forced[:5])}"
                + (f", …(+{len(forced) - 5})" if len(forced) > 5 else "")
                + (
                    " (review/ has content)"
                    if review_dir_has_content
                    else " (review/ is missing)"
                )
            ),
            score=(len(unknown_inputs) - len(forced)) / len(unknown_inputs),
            suggested_hint=(
                "Enable `route_low_confidence_to_review` for this run, or "
                "manually move unclassifiable files into a review/ directory."
            ),
        )
    return RecipeVerifierVerdict(
        name="review_queue_verifier",
        passed=True,
        detail=(
            f"all {len(unknown_inputs)} unclassifiable file(s) routed to "
            "review/ or cited in review/*.md"
        ),
        score=1.0,
    )


# ───────────────────────────────────── 4. deliverable_completeness_verifier


@register("deliverable_completeness_verifier")
def deliverable_completeness_verifier(
    ctx: RecipeVerifierContext,
) -> RecipeVerifierVerdict:
    """Productisation guide §10 #6 — "README, topic index, charts,
    review report all generated?".

    Operates on the **recipe-level** ``expected_outputs`` (Phase 17
    introduced this field). For each declared deliverable, check the
    file exists on disk under ``workspace_path``.

    Empty deliverable lists short-circuit to a skipped pass — the
    recipe author can opt out of this check by leaving the field
    empty (though every shipped flagship declares deliverables).
    """
    declared = list(ctx.recipe.expected_outputs)
    if not declared:
        return RecipeVerifierVerdict(
            name="deliverable_completeness_verifier",
            passed=True,
            detail="recipe declares no expected deliverables",
            skipped=True,
        )

    ws = ctx.workspace_path
    missing: list[str] = []
    present: list[str] = []
    for rel in declared:
        if (ws / rel).exists():
            present.append(rel)
        else:
            missing.append(rel)

    if missing:
        return RecipeVerifierVerdict(
            name="deliverable_completeness_verifier",
            passed=False,
            detail=(
                f"{len(present)}/{len(declared)} deliverables present; "
                f"missing: {', '.join(missing[:8])}"
                + (f", …(+{len(missing) - 8})" if len(missing) > 8 else "")
            ),
            score=len(present) / len(declared),
            suggested_hint=(
                "Re-run the failing stage so the missing deliverables are "
                "produced, or amend the recipe's expected_outputs to drop "
                "optional artefacts."
            ),
        )
    return RecipeVerifierVerdict(
        name="deliverable_completeness_verifier",
        passed=True,
        detail=f"all {len(declared)} declared deliverable(s) present",
        score=1.0,
    )
