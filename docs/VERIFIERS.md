# Recipe-level Deliverable Verifiers (v0.19.0)

> Productisation guide §10 — "不要只扩大 Skill，更要扩大 Verifier"
> ("don't just grow skills, grow verifiers"). Seven verifiers,
> exactly as the guide listed them, wired into `pack run`.

Phase 19 closes the loop the productisation guide §3.3 flagged: pack
runs were verifying "did files get produced?" (structural) but not
"are the deliverables substantively correct?" (semantic). The new
verifier layer runs **after** the TaskGraph finishes and gives the
user a typed verdict on whether the produced pack is actually
deliverable.

## The 7 verifiers

| Verifier name | Type | Productisation §10 | What it checks |
|---|---|---|---|
| `coverage_verifier` | structural | #1 | Every input file is either moved to a category OR cited by basename in any `*.md`. Catches "silently dropped during the pipeline." |
| `source_ledger_verifier` | structural | #2 | Every backticked path in `SOURCES.md` resolves to a real file in the workspace. Catches hallucinated citations. |
| `review_queue_verifier` | structural | #5 | Unclassifiable files (extensions not in the curated table) end up in `review/` or get cited in `review/*.md`. Catches force-classification. |
| `deliverable_completeness_verifier` | structural | #6 | Every path in `recipe.expected_outputs` exists on disk after the pack runs. |
| `summary_grounding_verifier` | semantic (LLM) | #3 | README / summary file's claims line up with the workspace contents. Catches generic boilerplate + hallucinated filenames. |
| `chart_data_consistency_verifier` | semantic (LLM) | #4 | Chart caption numbers + categories are consistent with the underlying CSV / XLSX data. |
| `topic_coherence_verifier` | semantic (LLM) | #7 | Files inside a topic / category directory are plausibly related under a common topic. |

## How verifiers run

Two integration points:

1. **`localflow pack run <name>`** runs every verifier listed in the
   recipe's `verifiers:` field after the TaskGraph completes. Results
   are persisted to `<run_dir>/recipe_verification.json` and shown
   as a table.
2. **Exit codes**: `0` = pack passed + all verifiers passed (or
   skipped); `1` = pipeline crashed; **`3`** = pipeline ran cleanly
   but ≥ 1 verifier failed. CI uses this to distinguish broken
   pipelines from broken quality.

```powershell
localflow pack run research_pack --workspace ./my_workspace
# → produces pack stages + verifier table
# → exit 0 (all pass) / 1 (stages failed) / 3 (verifiers failed)
```

## Failure → repair flow

Every failing verdict carries a `suggested_hint` phrased so the
planner LLM can act on it directly. Hints follow the same format as
Phase 13 semantic graders' hints:

```
Hint for `chart_data_consistency_verifier`: Rewrite the caption to
reflect the CSV statistics (e.g., model/epoch/accuracy summary)
or omit the folder-count table entirely.
```

**v0.21.0 (Phase 21)** wires these hints into an auto-repair loop:

1. When `pack run` finishes with stages PASSED but ≥ 1 verifier
   FAILED and `recipe.repair_policy.enabled=true`, the loop kicks in.
2. The first non-skipped fail verdict with a hint is selected.
3. Its target stage is resolved via
   `recipe.repair_target_map[verifier_name]` (or default: the recipe's
   last LLM-planned stage — usually the synthesis step).
4. `replay_from_stage` rolls back the affected entries and replays
   the stage with `skill.plan_with_llm(user_hint=<the hint>)`.
5. Verifiers re-run against the post-replay workspace.
6. Repeat up to `repair_policy.max_rounds` (≤ 3) or until verdict
   passes.

The result is written to `<run_dir>/recipe_repair.json`:

```json
{
  "repaired": true,
  "rounds_used": 1,
  "halt_reason": "passed",
  "attempts": [
    {
      "attempt": 1,
      "triggered_by_verifier": "summary_grounding_verifier",
      "suggested_hint": "Remove unsupported sheet-name claims...",
      "target_stage": "s5_synthesize",
      "post_attempt_passed": true,
      "failed_after_attempt": [],
      "duration_ms": 7400
    }
  ],
  "final_verification": { ... }
}
```

The shipped flagship recipes all opt in (`enabled: true,
max_rounds: 2`). Override per recipe:

```yaml
repair_policy:
  enabled: false      # opt out completely
  max_rounds: 1

repair_target_map:
  # Coverage problems = organizer's fault, not the synth's.
  coverage_verifier: s1_organize
  review_queue_verifier: s1_organize
```

## Graceful degradation

- **Structural verifiers** never need an LLM. They run in CI without
  any API key.
- **Semantic verifiers** call `app.agent.judge` which auto-skips
  when no LLM client is available. Skipped verdicts count as a
  PASS for aggregation but are reported separately so the user
  sees coverage gaps.
- **Unregistered verifier name in `recipe.verifiers:`** produces a
  failed verdict with `detail` listing the registered names — a typo
  doesn't abort the verification phase.
- **Verifier exception** is caught and surfaced as `passed=False,
  detail="verifier raised: ..."`. A buggy verifier doesn't kill
  verification.

## Adding your own verifier

```python
from app.eval.recipe_verifiers import register, RecipeVerifierContext, RecipeVerifierVerdict

@register("my_check")
def my_check(ctx: RecipeVerifierContext) -> RecipeVerifierVerdict:
    if some_signal_is_off(ctx):
        return RecipeVerifierVerdict(
            name="my_check",
            passed=False,
            detail="signal X is off",
            suggested_hint="re-plan to ensure signal X is set",
        )
    return RecipeVerifierVerdict(name="my_check", passed=True, detail="ok")
```

Then list `my_check` in a recipe's `verifiers:` field. The next
`pack run` runs it.

## What `RecipeVerifierContext` carries

```python
class RecipeVerifierContext(BaseModel):
    recipe: RecipeSpec
    workspace_path: Path
    snapshot_inputs: list[str]      # PRE-run input paths
    moves: dict[str, str]            # original -> final (from rollback manifest)
    task_graph_result: TaskGraphResult | None
    run_id: str | None
```

Deliberately small: verifiers inspect the workspace + the runner's
manifest + the recipe. They never reach into stage internals — the
abstraction line stays at "what was the user's pack supposed to
produce, and what did they actually get?"

## On-disk artefact: `recipe_verification.json`

```json
{
  "run_id": "2026-05-19-036",
  "recipe_name": "research_pack",
  "passed": false,
  "failed_count": 3,
  "skipped_count": 1,
  "verdicts": [
    {
      "name": "coverage_verifier",
      "passed": true,
      "detail": "all 10 input(s) accounted for (moved or cited)",
      "score": 1.0,
      "skipped": false
    },
    {
      "name": "review_queue_verifier",
      "passed": false,
      "detail": "1/1 unclassifiable file(s) were force-classified ...",
      "score": 0.0,
      "skipped": false,
      "suggested_hint": "Enable `route_low_confidence_to_review` ..."
    },
    ...
  ]
}
```

The schema is at [app/eval/recipe_verifiers/_schema.py](../app/eval/recipe_verifiers/_schema.py).

## Live verification (against v0.14's research workspace)

```
$ localflow pack run research_pack --workspace examples/research_pack/workspace --yes
[stages 1-5 all PASSED, ~13s]
Deliverable verifiers: FAILED (3)
  coverage_verifier                pass   all 10 input(s) accounted for (moved or cited)
  deliverable_completeness_verifier fail  9/11 deliverables present; missing: README.md, SOURCES.md
  source_ledger_verifier           skipped no SOURCES.md produced; nothing to verify
  review_queue_verifier            fail   1/1 unclassifiable file(s) were force-classified ...: untitled.dat
  summary_grounding_verifier       pass   The summary names only files and folders present ...
  chart_data_consistency_verifier  fail   The caption describes file counts by folder, but the CSV is experiment results ...
  topic_coherence_verifier         fail   `images/`: index.md lists 2 files, but the directory contains 3 ...
```

The verifiers caught real issues the pack pipeline never surfaced
before. Each failure has a `suggested_hint` ready to feed back into
a Phase 20+ auto-repair loop.

## Backwards compatibility

- Existing `eval` graders (`expected_outputs_present` etc.) are
  unchanged. Recipe verifiers live in a separate registry; old
  eval tasks keep using the eval graders.
- Recipes without a `verifiers:` field run with zero overhead —
  the pack-run integration short-circuits early.
- The new `recipe_verification.json` artefact is additive; nothing
  else in `<run_dir>` changes.
