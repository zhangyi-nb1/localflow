# LocalFlow TaskGraph — v0.11.0

A **TaskGraph** is the static counterpart to a Skill + ActionPlan:
instead of one skill producing one plan via the LLM, a graph
declares a sequence of stages, each driven by an existing specialist
skill. The TaskGraphRunner walks stages in order through the same
harness pipeline (policy_guard → dry-run → execute → verify), with
a single aggregated rollback manifest covering every stage.

TaskGraph **does not replace** the v0.9 `agent` meta-skill — both
exist:

| | `agent` meta-skill (v0.9) | TaskGraph (v0.11) |
|---|---|---|
| Driven by | LLM, one ActionPlan | Static YAML, N skill invocations |
| Best for | Ad-hoc / novel goals | Repeatable / known shape / CI |
| Reproducibility | Depends on model + temperature | Deterministic (rule planners) |
| Failure boundary | One plan, all-or-nothing | Per-stage failure policy |
| Cost | LLM tokens per goal | Zero (with rule planners) |

The original v0.9 "整理然后画图" compound goal is now solvable both
ways. v0.10's eval suite will let users measure which one wins on
their workloads.

---

## Quickstart

Write a graph YAML:

```yaml
# my_graph.yaml
user_goal: organize then chart
workspace_root: ./examples/messy_downloads
stages:
  - stage_id: s1_organize
    title: Organize by file type
    skill: folder_organizer
    planner: rule
    expected_outputs:
      - papers/index.md
  - stage_id: s2_chart
    title: Render bar chart of file counts
    skill: workspace_visualizer
    planner: rule
    expected_outputs:
      - images/file_counts.png
```

Inspect, then run:

```powershell
localflow taskgraph describe my_graph.yaml      # preview the stages
localflow taskgraph run my_graph.yaml --yes     # execute end-to-end
```

Single approval ceremony at the start: the user approves the
**graph spec** (which stages, which skills, which planners). Per-stage
ActionPlans are generated just-in-time after the previous stage
completes; per-stage dry-runs are written + traced but not prompted
on. This is the only way multi-stage works in a non-interactive
context (CI, MCP).

To roll back the whole graph:

```powershell
localflow rollback --run-id <task_id> --yes
```

The existing `rollback` command doesn't need to know stages exist —
it just replays the aggregated manifest at `<run_dir>/rollback_manifest.json`.

---

## On-disk layout

```
<localflow_home>/runs/<task_id>/
  taskgraph.json                # the graph spec as submitted
  taskgraph_result.json         # aggregated stage results
  trace.jsonl                   # ONE trace stream for all stages
  rollback_manifest.json        # ONE manifest covering all stages
  stages/
    s1_organize/
      task.json
      workspace_snapshot.json
      plan.json
      dry_run.md
      actions.json
      verify_report.json
      backups/
    s2_chart/
      task.json
      ...
```

Each stage's artifacts live under `stages/<stage_id>/`; the
graph-level files (trace, rollback manifest, graph spec, graph
result) live at the top of `run_dir`.

---

## StageSpec field reference

```yaml
stage_id: s1_organize          # unique within the graph
title: Organize by file type   # human label
skill: folder_organizer        # registered skill name
planner: rule                  # rule | llm
expected_outputs:              # paths the verifier should find
  - papers/index.md
allowed_actions:               # subset of skill manifest's allowed list
  - mkdir                      # (None / omit → inherit full manifest)
  - move
  - index
forbidden_actions:             # additive to graph.forbidden_actions
  - shell
failure_policy: abort          # abort | continue | skip — see below
max_retries: 1                 # Phase 10 always 1; Phase 12 wires retry
notes: |
  Free-form human description.
```

---

## Failure policies

| Policy | Execution effect | StageResult.status | Graph.passed contribution |
|---|---|---|---|
| `abort` (default) | Stop the graph; remaining stages don't run | FAILED for this stage, ABORTED for the rest | False |
| `continue` | Log the failure; run subsequent stages anyway | FAILED for this stage | False (one stage failed) |
| `skip` | Log + run subsequent stages; downgrade status | SKIPPED for this stage | True (skipped is intentional) |

Pick `abort` when the next stage genuinely depends on this one
succeeding (e.g., chart depends on files being organized). Pick
`continue` when stages are independent and you want diagnostic
visibility into all of them. Pick `skip` when this stage is
genuinely optional ("if the analysis stage fails, that's fine, the
pipeline still has value").

---

## Trace stream

Every TraceEvent emitted inside a stage's execution carries
`stage_id` automatically — the `TaskGraphRunner` wraps each stage in
a `TraceLogger.stage(stage_id)` context manager:

```json
{"ts": "...", "event": "action.start", "payload": {"stage_id": "s1_organize", "action_id": "s1_organize.a-001", ...}}
{"ts": "...", "event": "action.end",   "payload": {"stage_id": "s1_organize", "action_id": "s1_organize.a-001", ...}}
{"ts": "...", "event": "verifier.check","payload": {"stage_id": "s1_organize", ...}}
{"ts": "...", "event": "action.start", "payload": {"stage_id": "s2_chart", "action_id": "s2_chart.a-001", ...}}
```

action_ids are stage-prefixed (`<stage_id>.<original_action_id>`) so
they stay unique across the aggregated rollback manifest.

---

## Rollback semantics

One graph = one aggregated rollback manifest at the top of `run_dir`.
The existing `localflow rollback --run-id <id>` replays it in reverse
across all stages. Per-stage rollback (e.g., "undo only stage 2") is
**not** in v0.11.0 — that's a Phase 10.1 followup if users want it.

The Phase 7.1 hash-drift guard still applies: if the user manually
edited a file produced by stage 1 before running rollback, the runner
refuses to clobber it (unless `--force`). Drift detection works
across all stages.

---

## Composing with EvalTask

`EvalTask.stages` accepts the same `StageSpec` list. When set, the
eval runner dispatches to the TaskGraph path automatically:

```yaml
# evals/workspace_pack/task_007_organize_then_chart.yaml
task_id: task_007_organize_then_chart
title: Multi-stage compound goal
goal: organize files by type then render a bar chart of category counts
workspace_seed:
  - path: report.pdf
    text: "..."
stages:
  - stage_id: s1_organize
    skill: folder_organizer
    planner: rule
  - stage_id: s2_chart
    skill: workspace_visualizer
    planner: rule
graders:
  - safety_no_forbidden_path
  - expected_outputs_present
  - all_files_accounted_for
  - rollback_restores
```

Run via `localflow eval run evals/workspace_pack/`. The eval runner
takes care of synthesising an aggregated `GraderContext` so existing
graders (which read `ctx.plan.actions`) see the union of every
stage's actions.

---

## When NOT to use TaskGraph

- **Single-skill tasks** — just use `localflow plan ... --skill X`.
  Wrapping in a one-stage graph adds boilerplate for no win.
- **Novel goals where you don't know the stages** — the `agent`
  meta-skill (v0.9) is better; let the LLM decide the action shape.
- **Highly conditional pipelines** ("if stage 2 fails, run stage 4
  instead") — Phase 10 is strictly sequential. A future phase may
  add conditionals; today, write the logic in a shell wrapper that
  picks one of several graph YAMLs.

---

## Roadmap

- **v0.11.x** — grow the multi-stage eval suite; add `localflow
  taskgraph rerun --stage <id>` for re-executing one stage after
  manual fixes; per-stage rollback.
- **Phase 11 (v0.12.0)** — Workspace Pack Builder strong demo
  exercising 5-8 stages of a real research workspace cleanup.
- **Phase 12** — Semantic verifiers + Repair Loop. `max_retries`
  + a new `failure_policy: repair` will trigger an automatic
  repair-plan attempt before marking the stage failed.
