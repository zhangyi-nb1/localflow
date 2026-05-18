# Workspace Pack Builder — v0.14.0

The canonical "this is what LocalFlow actually does" demo. Composes
every harness layer shipped through v0.13 into a 5-stage pipeline
that turns a messy research workspace into a deliverable knowledge
pack.

## Why this demo exists

v0.10–v0.13 each shipped a substrate piece:

| Phase | Layer | Demo so far |
|---|---|---|
| v0.10 | TaskGraph | "compose 2 specialist skills" (task_007) |
| v0.11 | Plan refinement | "user fixes a bad plan" |
| v0.12 | Data-aware routing | "analyze the Excel" (single task) |
| v0.13 | Auto-repair | "harness fixes a bad plan" (semantic verifier) |

Each one was a *capability* in isolation. Phase 14 answers: *does
combining them produce something useful?* The Workspace Pack Builder
runs all four layers against a realistic workspace in one command.

## Quickstart

```powershell
# 1. Plant the seeded workspace (10 messy files).
python examples/research_pack/seed.py

# 2. Preview the 5 stages without running anything.
localflow taskgraph describe examples/research_pack/workspace_pack.yaml

# 3. Run end-to-end.
localflow taskgraph run examples/research_pack/workspace_pack.yaml --yes

# 4. Inspect the produced pack.
ls examples/research_pack/workspace/
```

Stages 1-4 are deterministic (rule-planned). Stage 5 needs an LLM
client (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`); without one, it
gracefully skips via `failure_policy: skip` — the pack still contains
the per-category indexes + PDF index + analysis + overview chart,
just without the synthesised top-level README + sources ledger.

## The 5 stages

```
                      INPUT
                        │
                        ▼
          ┌───────────────────────────┐
          │ s1_organize               │  folder_organizer (rule)
          │ → papers/ data/ images/   │
          │   notes/ misc/ + indexes  │
          └───────────────────────────┘
                        │
                        ▼
          ┌───────────────────────────┐
          │ s2_pdf_index              │  pdf_indexer (rule)
          │ → pdf_index.md            │
          │ (per-PDF title + summary) │
          └───────────────────────────┘
                        │
                        ▼
          ┌───────────────────────────┐
          │ s3_data_analyze           │  data_analyzer (rule)
          │ → analysis_report.md      │
          │ + analysis_charts/*.png   │
          └───────────────────────────┘
                        │
                        ▼
          ┌───────────────────────────┐
          │ s4_workspace_chart        │  workspace_visualizer (rule)
          │ → images/file_counts.png  │
          │ + file_counts_summary.md  │
          └───────────────────────────┘
                        │
                        ▼
          ┌───────────────────────────┐
          │ s5_synthesize             │  agent (LLM)
          │ → README.md               │   failure_policy: skip
          │ + SOURCES.md / ledger.md  │
          └───────────────────────────┘
                        │
                        ▼
                    PACKED WORKSPACE
```

Per-stage outputs live in the workspace root (NOT in
`stages/<id>/`); that's where downstream stages and the final user
expect to find them. The `stages/<id>/` subdirs under the run_dir
hold per-stage artifacts the harness needs (plan.json, dry_run.md,
actions.json, execution_log.jsonl).

## With vs. without LLM

| LLM available | Stages 1-4 | Stage 5 |
|---|---|---|
| ✓ | PASSED | PASSED — README + ledger synthesized |
| ✗ | PASSED | SKIPPED — graceful degradation; per-stage artifacts still produced |

The `failure_policy: skip` on stage 5 is what makes this work. The
graph runner marks the stage SKIPPED, continues, and the aggregated
rollback manifest still spans stages 1-4 so a single
`localflow rollback --run-id <id>` undoes everything.

## With vs. without auto-repair (Phase 13)

Phase 13's `enable_semantic_verifier` memory pref is the opt-in:

```powershell
localflow memory set enable_semantic_verifier true
localflow memory set max_auto_repairs 2
```

When on, after each stage's structural verify passes, the semantic
verifier runs (3 LLM-as-judge graders: `output_addresses_goal`,
`summary_grounded`, `analysis_result_nonempty`). If any grader
rejects, the harness rolls back that stage + revises with a
grader-derived hint + re-executes — up to `max_auto_repairs` cycles.

For workspace_pack.yaml specifically, this is most useful when
stage 5's first attempt produces a generic README that fails
`summary_grounded`: the loop catches it + asks the LLM to ground
the README in the actual workspace files.

## Eval mode

Same pipeline as eval task `task_010_workspace_pack`:

```powershell
# Baseline run (no auto-repair):
localflow eval run evals/workspace_pack/task_010_workspace_pack.yaml

# Baseline vs. auto-repair comparison:
localflow eval run evals/workspace_pack/task_010_workspace_pack.yaml --compare-repair
```

The `--compare-repair` mode runs the task twice (with and without
auto-repair) and emits a markdown table showing per-grader verdict
deltas. Use this to measure whether v0.13's auto-repair improves the
outcome on this realistic workload.

## Reading the produced pack

After a successful run:

```
examples/research_pack/workspace/
  README.md                  ← s5 synthesized — top-level entry, links per-stage outputs
  SOURCES.md                 ← s5 synthesized — file inventory with sha256s
  pdf_index.md               ← s2: 3-PDF index with titles
  analysis_report.md         ← s3: groupby + aggregations on the CSV/XLSX
  duplicates_report.md       ← s1: any sha256 collisions (e.g., duplicate PNGs)
  file_counts_summary.md     ← s4: markdown summary of the file-count chart
  papers/                    ← s1: 3 organized PDFs + index.md
  data/                      ← s1: CSV + index.md
  spreadsheets/              ← s1: XLSX + index.md
  images/                    ← s1: 2 PNGs + index.md; also s4's file_counts.png
  notes/                     ← s1: TXT + Markdown + index.md
  misc/                      ← s1: anything folder_organizer can't classify
  analysis_charts/           ← s3: PNG charts per analyzed dataframe
```

## Composability

The workspace_pack.yaml is a regular TaskGraph. Append your own
stages (e.g., upload the pack to a static-site host via a custom
skill, push SOURCES.md to a database, etc.) by editing the YAML.
See [TASKGRAPH.md](TASKGRAPH.md) for the full StageSpec schema.

To replace the LLM-driven synthesis with a deterministic alternative,
write a `pack_synthesizer` skill (subclass `Skill`, implement
`plan()` to emit `index` actions writing README.md + SOURCES.md from
the workspace snapshot) and swap `skill: agent` → `skill:
pack_synthesizer` in the YAML. The rest of the pipeline is
unchanged.

## Limitations / honest scope

- **Topic clustering** — the experiment report imagined
  `topics/<topic>/index.md` (semantic clustering by topic, e.g.
  "memory", "rag_eval"). v0.14 ships extension-based categories
  (`papers/`, `data/`, `images/`, ...) because that's what
  `folder_organizer` does today. Semantic clustering needs a new
  skill (deferred to Phase 15+).
- **review/ dir** — the report imagined a dedicated `review/` dir
  for low-confidence files. v0.14 leaves unknowns in `misc/`. A
  dedicated low-confidence triage skill is a v0.14.x followup.
- **Typed source_ledger.json** — v0.14 ships a markdown ledger
  (SOURCES.md). A typed JSON with stable schema for downstream
  tooling is deferred.

These are honest gaps; the pipeline still produces a usable pack.
The 5-stage composition is the headline.
