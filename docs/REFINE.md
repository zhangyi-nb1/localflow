# LocalFlow Plan Refinement Loop — v0.12.0

The harness's safety net was always good at *catching execute-time
mistakes* — policy guard rejects forbidden paths, the verifier flags
missing outputs, rollback restores files when something goes wrong.
But **plan-time mistakes** — the LLM produces a plan that's
*valid* yet doesn't match what the user actually wanted — had no
in-loop recovery. The only options were:

1. Execute it anyway and rollback (wasted filesystem ops)
2. Close the UI, retype the goal, lose history

Phase 11 closes that gap. After dry-run, the user can supply a
clarification ("you misread my goal — I want analysis of the data
inside the file, not a folder reorganization") and the same task gets
a fresh `plan_v(N+1).json`. No execution, no rollback, no lost
history.

> "The model proposes; the harness disposes" — refinement is the
> "before the harness disposes, let me propose again" step.

---

## Quickstart

### UI (Streamlit)

1. Go to **📋 Plan**, type your goal, click **Create plan**.
2. The plan summary appears with the auto-detected skill + planner.
3. Below the summary, an expander labelled
   `🔄 Not what you wanted? Refine the plan (5 revision(s) left)`
   contains a text-area + button.
4. Type what was wrong (e.g. "用饼图展示分类，不是柱状图"), click
   **🔁 Re-plan with this hint**.
5. Streamlit re-renders the page with the new plan. Iterate up to
   5 times per task.
6. Once satisfied, click **🚀 Execute →**.

### CLI

```powershell
# After `localflow plan ./ws --goal "..."` returns task_id 2026-05-16-001
localflow revise --task-id 2026-05-16-001 --hint "use a pie chart, not bar"
localflow dry-run --task-id 2026-05-16-001    # preview the revised plan
localflow execute --task-id 2026-05-16-001     # run it
```

`localflow revise` does NOT execute. It only:

1. Loads `task.json` + `workspace_snapshot.json` + the current
   `plan.json` (the v(N) baseline).
2. Calls `skill.revise(task, snapshot, prior_plan, hint)` (default
   delegates to `plan_with_llm(prior_plan_actions=..., user_hint=...)`).
3. Validates the new plan via skill.validate + policy guard.
4. Persists `plans/plan_v(N+1).json`, mirrors to `plan.json`,
   appends a row to `revisions.jsonl`.
5. Emits one `plan.revised` trace event.

The workspace stays untouched. A subsequent `localflow execute`
runs the refined plan.

---

## On-disk layout

```
<localflow_home>/runs/<task_id>/
  task.json                  # unchanged
  workspace_snapshot.json    # unchanged — refinement doesn't re-scan
  plan.json                  # mirrors the LATEST version (v1, v2, v3…)
  plans/
    plan_v1.json             # backfilled when the first revise happens
    plan_v2.json
    plan_v3.json
  revisions.jsonl            # one JSONL row per revise
  trace.jsonl                # PLAN_REVISED event per revise
  dry_run.md                 # rewritten on each dry-run / execute
```

`plan.json` always reflects the latest version, so the executor /
verifier / rollback code paths keep working unchanged — they have
no concept of "version" because they don't need one.

### revisions.jsonl row schema

```json
{
  "ts": "2026-05-16T10:32:00+00:00",
  "version": 2,
  "prior_plan_id": "plan-abc12345",
  "new_plan_id": "plan-def67890",
  "user_hint": "use a pie chart for the category proportions",
  "prior_action_count": 12,
  "new_action_count": 8
}
```

### Trace event payload

`plan.revised` payload:

```json
{
  "prior_plan_id": "plan-abc12345",
  "new_plan_id": "plan-def67890",
  "version": 2,
  "user_hint": "use a pie chart for the category proportions"
}
```

`detail` field reads `v2: use a pie chart for the category proportions`
(truncated at 200 chars).

---

## How the hint reaches the LLM

The internal repair loop in `app/agent/planner.py` already handles
the case where the validator rejects a plan: it appends the model's
prior tool_use + a `tool_result` with `is_error=True` carrying the
validation error. The LLM sees both and regenerates.

Refinement reuses that mechanic with one twist: there *was* no prior
LLM call (the refinement is the first call in this iteration). So
we synthesize a single user-turn message that says:

```
REVISION REQUEST — your previous plan did NOT match the user's intent.

Your previous plan emitted the following actions:

```json
[ ... compact JSON of every action from plan_v(N) ... ]
```

The user reviewed it and provided this clarification:

> use a pie chart for the category proportions

Please regenerate a fresh ActionPlan from scratch that addresses the
user's clarification. Do not simply tweak the prior plan — consider
whether your prior decomposition itself was wrong (e.g. you tried to
organize files when the user wanted to analyze data inside one of
them). Use the same submit_action_plan tool to submit the revised
plan.
```

This goes after the standard system prompt + workspace context. The
LLM then makes one submit_action_plan tool call and the rest of the
flow proceeds exactly like a first-attempt plan (validation, policy
guard, dry-run rendering).

No new state machine. No provider-specific synthesised tool_use
blocks. Just one more user message.

---

## MAX_REVISIONS = 5

Hard cap, enforced in `control_loop.run_revise`. After five
revisions you're better off restarting with a clearer initial goal
than continuing to chase the LLM. The 6th attempt raises
`SkillError("plan already revised 5 times — consider restarting
with a clearer initial goal")`.

The CLI surfaces this as a clean non-zero exit + error message; the
UI surfaces it as `⚠️ Plan revised 5 times already — consider
restarting with a clearer initial goal.`

---

## When to refine vs. restart

| Situation | Refine | Restart |
|---|---|---|
| LLM misunderstood ONE specific detail (chart kind, target dir) | ✓ | |
| LLM picked the wrong overall decomposition (organize vs. analyze) | ✓ (often works) | maybe |
| The workspace itself changed since the scan | | ✓ (refinement reuses the snapshot) |
| You want to switch skills (agent → data_analyzer) | | ✓ (skill is locked to task_id) |
| You realize your initial goal was unclear or wrong | | ✓ |

The skill is locked to the task at plan-time (because
`autodetect_skill` consumes the goal). If you started with the wrong
skill, refinement won't help — it can only ask the same skill's LLM
planner to try again. Restart from the Plan page to re-trigger
auto-detection.

---

## Refine + TaskGraph

TaskGraph (Phase 10) is **out of scope** for v0.12.0 refinement.
Multi-stage graphs are deterministic compositions; refining a stage
mid-graph requires re-pinning the approval ceremony and rewiring the
aggregated rollback manifest. Phase 13 may add it; today, refinement
only works on single-skill plans (the v0.9 agent path, the v0.11
data_analyzer path, the older folder_organizer / pdf_indexer paths
when surfaced via CLI).

---

## What refinement DOES NOT do

- It does **not** re-scan the workspace. The user's edits between
  plan v1 and revise are invisible to plan v2. To pick those up,
  restart the task.
- It does **not** change `task.skill` or `task.workspace_root`.
  Those are task-level invariants.
- It does **not** trigger automatically on grader rejection or
  policy-guard rejection. Those failures happen inside the LLM
  client's internal repair loop. Refinement is **user-initiated**
  and is about *semantic* mismatch, not validation mismatch.
- It does **not** export multiple plan versions to MCP clients.
  MCP `plan_refine` is a Phase 12.x followup that needs a careful
  approval-token re-mint design.

---

## Phase 12 hook

The `failure_policy: repair` value reserved on
[StageSpec](../app/schemas/taskgraph.py) is the auto-repair entry
point. Phase 12's Semantic Verifier will detect "the plan executed
successfully but the output doesn't match the goal" and trigger a
`run_revise` call internally with a grader-derived hint. v0.12.0
keeps refinement user-driven so the contract is honest about
*who* decided the plan was wrong.
