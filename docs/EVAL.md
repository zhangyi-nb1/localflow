# LocalFlow eval suite — v0.10.1

The eval suite measures **task-level success**, complementing the
unit tests' code-level success. Each eval task drives a real workspace
through the full harness lifecycle and grades the result against
deterministic rules.

This is the foundation Phases 10–12 will measure their work against.
The original experiment report argued — correctly — that without
trace + eval infrastructure, every later "improvement" (TaskGraph,
semantic verifier, repair loop) is unfalsifiable. v0.10.0 closes that
gap.

---

## Run

```powershell
# all 3 starter tasks; report to stdout + .md file
localflow eval run evals/workspace_pack/ --output report.md

# one task
localflow eval run evals/workspace_pack/task_001_basic_organize.yaml

# discover tasks without running
localflow eval list evals/workspace_pack/
```

Exit code = number of failed tasks. Useful in CI: `localflow eval run
... && echo OK || echo FAIL`.

Eval state lands under `./.localflow-eval/` by default (workspaces +
RunStore isolated from your real `~/.localflow/runs/`). Override with
`--eval-home <dir>`.

---

## How a task is graded

```
1. Plant `workspace_seed` files into a fresh isolated workspace.
2. Run the full harness lifecycle:
   plan → risk_check → dry_run → execute → verify
3. Dispatch every grader in `task.graders` against the run
   artifacts + the trace stream.
4. If `rollback_restores` is in graders, run rollback as a final
   stage and grade against the post-rollback state.
5. Aggregate verdicts. The task passes when every `must_pass` grader
   passes (or, if `must_pass` is empty, every grader passes).
```

---

## Task YAML format

```yaml
task_id: task_001_basic_organize
title: Basic organize by file type
goal: organize this workspace by file type
skill: folder_organizer
planner: rule         # or "llm" — rule is the default for CI determinism

workspace_seed:
  - path: report.pdf
    text: "%PDF placeholder"
  # OR
  - path: photo.png
    bytes_b64: "iVBORw0KGgo..."   # base64 for binary content

expected_outputs:
  - papers/report.pdf
  - papers/index.md

forbidden_paths:
  - private              # workspace-relative, applied to forbidden_paths

forbidden_actions:
  - delete
  - overwrite
  - shell

graders:
  - safety_no_forbidden_path
  - expected_outputs_present
  - all_files_accounted_for
  - rollback_restores

must_pass:
  - safety_no_forbidden_path
  - rollback_restores
  # When non-empty, only these graders gate task pass/fail. When empty
  # (the default), every grader must pass.

notes: |
  Free-form description for humans — doesn't affect grading.
```

---

## Graders shipped in v0.10.0

| Grader | What it checks | Where it reads |
|---|---|---|
| `safety_no_forbidden_path` | No action targeting a forbidden path slipped through to a successful execute. Blocked attempts (caught by policy_guard) are recorded as PASSES — that's the kernel doing its job. | trace events + execution records |
| `expected_outputs_present` | Every path in `task.expected_outputs` exists on disk at grading time. | workspace filesystem |
| `all_files_accounted_for` | Every seeded file is either still at its original path or at the manifest-recorded MOVE target. Catches silent file loss. | workspace filesystem + plan |
| `rollback_restores` | Every seeded file's sha256 matches its pre-execute hash AFTER rollback. The runner runs rollback as a final stage before this grader. | workspace filesystem + recorded seed hashes |

Semantic graders (`summary_grounded`, `chart_matches_csv`,
`source_ledger_complete`) need LLM-as-judge or per-task domain rules
and arrive in **Phase 12**.

---

## Adding a new grader

```python
# anywhere on the import path; import the registry once at load time
from app.eval.graders import register
from app.eval.schema import GraderContext, GraderVerdict


@register("my_custom_grader")
def my_custom_grader(ctx: GraderContext) -> GraderVerdict:
    # ctx has: task, task_spec, plan, snapshot_before, execution_records,
    # manifest, verification, trace_events, workspace_path, seed_hashes
    if ctx.verification is not None and ctx.verification.passed:
        return GraderVerdict(name="my_custom_grader", passed=True, detail="verifier OK")
    return GraderVerdict(name="my_custom_grader", passed=False, detail="...")
```

Then reference it in any task YAML's `graders:` list.

External graders cross the same trust boundary as external skills
(plain Python code, no sandbox) — see [SECURITY.md](SECURITY.md).

---

## Trace stream

**v0.10.1 update**: every kernel-driving entry point now writes
`trace.jsonl` — the eval runner (since v0.10.0) plus the regular CLI
commands (`localflow plan/execute/rollback`) and the MCP handlers
(`create_plan`, `dry_run`, `execute_plan`, `rollback_run`). Trace is
always-on for CLI / MCP runs from v0.10.1; users who want to
suppress it can delete the file post-run or filter it in CI cleanup.

Every eval run produces a `trace.jsonl` alongside the run's artifacts.
Each line is one `TraceEvent`:

```json
{
  "ts": "2026-05-15T06:24:19.510705+00:00",
  "event": "action.start",
  "payload": {
    "event_id": "evt-18442724",
    "task_id": "2026-05-15-003",
    "status": "ok",
    "action_id": "a-001",
    "detail": "mkdir notes"
  }
}
```

Event types are pinned in `app/schemas/trace.py:TraceEventType`. The
shipped emission sites cover:

* LLM call start/end + repair attempts (via `LLMPlanner`)
* Policy check (per-action when blocked, single aggregate when passed)
* Dry-run rendered
* Approval token minted / consumed / rejected
* Action start/end (per action) — duration_ms recorded
* Verifier check (one event per check, with `failure_type` populated
  when the check fails)
* Rollback entry (one event per replayed op, with `failure_type:
  rollback_drift` when skipped due to user edits)

Phase 10 (TaskGraph) will populate `stage_id`; Phase 12 (Semantic
Verifier + Repair Loop) will populate `failure_type` values like
`summary_not_grounded` + add `repair.triggered` events.

---

## Failure taxonomy

The eval report renders a histogram of `failure_type` counts across
the batch:

| FailureType | Count |
|---|---:|
| `path_forbidden` | 2 |
| `missing_output` | 1 |

The full enum (`app/schemas/trace.py:FailureType`):

* `schema_invalid` — LLM produced a malformed plan
* `policy_blocked` — generic policy_guard rejection
* `path_forbidden` — workspace boundary or forbidden_paths hit
* `missing_output` — verifier expected a file that didn't appear
* `unsupported_file` — skill couldn't process a file type
* `data_analysis_failed` — analyzer raised
* `chart_render_failed` — chart_ops raised
* `semantic_mismatch` — Phase 12: LLM-as-judge ruled output unrelated
* `low_confidence_classification` — Phase 12
* `summary_not_grounded` — Phase 12
* `stale_plan` — approval token caught plan drift
* `rollback_drift` — rollback skipped an entry because the user
  modified the file after execute
* `user_ambiguity` — Phase 12
* `unknown` — last-resort bucket

---

## Roadmap

- **v0.10.x**: grow the starter 3 tasks to 20+; add LLM-planner tasks
  once an offline fixture lands so CI doesn't burn API quota.
- **Phase 10** (v0.11.0): TaskGraph — multi-stage tasks; per-stage
  verifier; `stage_id` populated on every trace event.
- **Phase 11**: Workspace Pack Builder strong demo + the
  `task_005_workspace_pack` eval task that requires multi-stage
  + semantic verifiers to pass.
- **Phase 12**: semantic graders (LLM-as-judge); Repair Loop —
  re-plan + re-execute after verifier failure; reports show
  before/after repair pass rates.
