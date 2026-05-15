# LocalFlow Semantic Verifier + Auto-Repair Loop — v0.13.0

The structural verifier (Phase 0+) answers
*"did every action execute and every expected file end up on disk?"*.
That's necessary but not sufficient. A plan can execute cleanly +
produce the expected files + pass every structural check while still
being **semantically wrong**:

- `data_analyzer` produces `analysis_report.md` whose every analysis
  ended in `EMPTY_RESULT` because the spec referenced a column the
  source data doesn't have. The file is there. The verifier is happy.
  The user is not.
- `folder_organizer` writes a `papers/index.md` whose body is generic
  boilerplate — it doesn't mention any of the actual files it claims
  to index.
- `agent` produces a chart whose X-axis labels don't match any
  category in the source data because the LLM hallucinated counts.

Phase 13 closes this loop. After execute + structural verify, the
**Semantic Verifier** runs LLM-as-judge graders against the actual
output + the user's goal. On rejection, the harness automatically
rolls back, asks the planner to revise with a grader-derived hint,
re-executes, and re-verifies. Up to `max_auto_repairs` cycles.

> v0.10 measures · v0.11 composes · v0.12 lets *users* correct ·
> **v0.13 lets the harness correct itself**.

---

## Opt-in only

Semantic verification is **off by default**. Reasons:

1. Each grader = 1 LLM call. Three starter graders = up to 3 calls
   per execute on top of the planner. Auto-repair compounds this:
   max_attempts=2 means up to ~9 grader calls + 2 plan-revise calls
   per task in the worst case.
2. Behaviour change for existing users — a plan that previously
   shipped clean might suddenly trigger rollback + revise on the
   same workspace.
3. CI environments without an LLM API key need to keep working —
   graders gracefully skip when no client is available, but if you
   *enabled* the verifier you probably want it to actually run.

Turn it on:

```powershell
localflow memory set enable_semantic_verifier true
localflow memory set max_auto_repairs 2    # default — 0 = report-only
```

UI: **⚙ Memory** page → **🔁 Semantic + Repair** tab → toggle on.

Turn off for a single run:

```powershell
localflow execute --task-id <id> --no-auto-repair
```

---

## The three starter graders

| Grader | Applies to | Rejects when |
|---|---|---|
| `output_addresses_goal` | any task with text outputs | The output's content fails to materially address the user's goal (generic boilerplate, meta-description, etc.) |
| `summary_grounded` | tasks that produce `index.md` / `summary.md` / `analysis_report.md` | The summary doesn't reference the actual files in the workspace (hallucinated names, placeholder language) |
| `analysis_result_nonempty` | `data_analyzer` outputs (`analysis_report.md`) | Every analysis in the report ended in `EMPTY_RESULT` / `INVALID_SPEC` / `READ_ERROR` |

`analysis_result_nonempty` is mostly deterministic (substring search
against the renderer's known empty-marker strings). The other two
delegate to an LLM judge via [`app/agent/judge.py`](../app/agent/judge.py).

Each grader's rejection carries a `suggested_hint` phrased as a
**direct instruction** for the planner — that's how the repair loop
gets a concrete starting point for plan v(N+1).

---

## How the repair loop works

```
   ┌──────────────┐
   │ run_execute  │  (existing kernel, unchanged)
   └──────┬───────┘
          ▼
   ┌──────────────┐
   │ run_verify   │  (structural, unchanged; if fails → no repair)
   └──────┬───────┘
          ▼
   ┌──────────────────────────────────┐
   │ SemanticVerifier.verify          │
   │   - runs every registered semantic grader
   │   - aggregates: passed iff all passed
   │   - auto_repair_eligible: ≥1 failed verdict with suggested_hint
   └──────┬───────────────────────────┘
          │ (when auto_repair_eligible AND max_auto_repairs > 0)
          ▼
   ┌──────────────────────────────────┐
   │ run_repair_loop (max_attempts ×) │
   │   1. trace.emit REPAIR_TRIGGERED │
   │   2. Rollback (force=False)      │
   │   3. control_loop.run_revise(hint)│
   │      → plan_v(N+1)               │
   │   4. run_execute(new_plan)       │
   │   5. run_verify (structural)     │
   │   6. semantic_verifier.verify    │
   │   stop on: passed | exhausted |   │
   │            drift | not_revisable │
   └──────────────────────────────────┘
```

Critical invariants:

- **Each repair iteration is a full lifecycle pass** — same
  policy_guard, same executor, same rollback semantics. The repair
  loop doesn't bypass any kernel checks.
- **Rollback uses `force=False`** — if the user has edited any of
  the workspace files between executes, the loop halts with
  `halt_reason="rollback_drift"` rather than clobbering their work.
- **Each repair counts as a plan revision** — `plans/plan_v(N+1).json`
  is written under the existing v0.12 versioning scheme, so manual
  refinement and auto-repair share the same audit trail.

---

## On-disk artifacts

```
<localflow_home>/runs/<task_id>/
  semantic_verify.json   # latest SemanticVerificationResult
  repairs.jsonl          # one JSON line per auto-repair attempt
  plans/
    plan_v1.json         # original
    plan_v2.json         # first repair revision
    plan_v3.json         # second repair revision (if any)
  trace.jsonl            # contains plan.revised + repair.triggered events
  ...
```

`semantic_verify.json` schema:

```json
{
  "task_id": "...",
  "run_id": "...",
  "passed": false,
  "verdicts": [
    {
      "grader": "analysis_result_nonempty",
      "passed": false,
      "reason": "every analysis (2/2) ended in empty/error...",
      "suggested_hint": "Re-plan with a different AnalysisSpec...",
      "duration_ms": 12,
      "token_usage": {}
    }
  ],
  "failed_verdicts": [...],
  "summary": "1/3 semantic verdict(s) rejected: analysis_result_nonempty",
  "auto_repair_eligible": true,
  "created_at": "2026-05-16T..."
}
```

`repairs.jsonl` row:

```json
{
  "ts": "2026-05-16T10:32:00+00:00",
  "attempt": 1,
  "grader": "analysis_result_nonempty",
  "suggested_hint": "Re-plan with a different AnalysisSpec...",
  "plan_version": 2,
  "structural_passed": true,
  "semantic_passed": true,
  "note": ""
}
```

---

## CLI surface

### `localflow verify-semantic --task-id <id>`

Report-only. Runs the semantic verifier against an already-executed
run's outputs. No rollback, no re-execute. Exit 0 when every verdict
passes, 1 otherwise. Useful for grading completed runs after the fact
or as a CI gate.

```powershell
localflow verify-semantic --task-id 2026-05-16-007
```

### `localflow repair --task-id <id>`

One manual repair cycle. Drives semantic verifier → rollback → revise
→ re-execute → re-verify. Caps at `--max-attempts` (default: memory
pref `max_auto_repairs`).

```powershell
localflow repair --task-id 2026-05-16-007 --max-attempts 3
```

### `localflow execute --no-auto-repair`

Forces the auto-repair loop off for this single run even when the
memory pref enables it. Useful when you specifically want to see the
"raw" semantic verdict without the harness retrying.

### `localflow memory set ...`

```powershell
localflow memory set enable_semantic_verifier true
localflow memory set enable_semantic_verifier false
localflow memory set max_auto_repairs 0     # report-only mode
localflow memory set max_auto_repairs 5     # upper limit
```

---

## TaskGraph integration

A stage can opt into auto-repair by setting
`failure_policy: repair` + `max_retries`:

```yaml
stages:
  - stage_id: s1_analyze
    title: Analyze the data
    skill: data_analyzer
    planner: llm
    failure_policy: repair
    max_retries: 2
```

When stage s1 runs, after structural verify the semantic verifier
fires; on rejection the repair loop tries up to 2 attempts before
the stage falls through to ABORT (the default downstream policy).

The previously-reserved `StageSpec.max_retries` field (introduced in
Phase 10, never consumed) is finally wired by Phase 13. Setting
`max_retries: 0` with `failure_policy: repair` makes the policy
behave identically to ABORT — useful for explicit "verify only"
runs.

---

## Eval comparison mode

```powershell
localflow eval run evals/workspace_pack/ --compare-repair
```

Runs each eval task TWICE: once with `enable_auto_repair=False`
(baseline), once with `True` + `max_auto_repairs=2`. Renders a
side-by-side markdown table:

```
| Task                              | Baseline | After Repair | Δ          |
| --------------------------------- | -------- | ------------ | ---------- |
| task_001_basic_organize           | ✓        | ✓            | —          |
| task_008_data_analysis_quality    | ✗        | ✓            | ↑ repaired |
| task_004_empty_workspace          | ✗        | ✗            | —          |
```

This is the empirical lens for "does v0.13's repair loop actually
improve outcomes on this batch?". Different workloads will show
different gains.

---

## Trace events

Two events emitted by the repair loop:

- `plan.revised` (from Phase 11) — same event whether the revise was
  triggered by user (`localflow revise`) or by the auto-repair loop.
- `repair.triggered` (new in Phase 13) — emitted ONCE per repair
  attempt with `failure_type=SEMANTIC_MISMATCH`. Payload includes
  `attempt`, `max_attempts`, `grader`, and the truncated
  `suggested_hint`.

Eval graders + trace consumers can filter on these to compute
"average repair attempts to passing" or "which grader most often
triggers a repair?".

---

## When NOT to enable

- **CI runs that must finish in bounded LLM cost** — keep off.
  Report-only mode (`max_auto_repairs=0`) is a middle ground:
  verdicts get surfaced, but no retries.
- **Highly deterministic workflows** — repeated runs with identical
  inputs that already produce correct output. Semantic verifier
  adds non-determinism (LLM judge can be inconsistent across calls)
  without a real upside.
- **Hot-path automations** — turn off when latency budget is tight.
  A typical semantic-verify pass adds 1-5 seconds; auto-repair adds
  more (one full execute cycle per attempt).

When in doubt, leave it OFF and run `localflow verify-semantic` ad
hoc against runs you're suspicious of.

---

## Phase 14 hook

The reserved `failure_policy: repair` is now live, but cross-stage
repair (where a stage's failure traces back to an upstream stage's
output) is not. Phase 14 may add:

- LLM-driven graders that read across stage outputs.
- A "rollback to stage N + re-run from there" primitive.
- MCP `verify_semantic` / `repair_run` tools (Phase 14.x).
- Vision-based `chart_accurate` grader (deferred from Phase 13 due to
  vision token cost).
