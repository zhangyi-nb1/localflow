# Workspace Pack Builder — v0.14 strong demo

Turns a messy research workspace into a deliverable knowledge pack in
one command. Composes every harness layer shipped through v0.13:

| Layer | Used for |
|---|---|
| v0.10 TaskGraph | The 5-stage pipeline below |
| v0.11 Plan refinement | Available per stage via UI if needed |
| v0.12 Data-aware routing + Excel preview | Stage 3's data_analyzer reads .xlsx cells |
| v0.13 Auto-repair (opt-in) | `localflow memory set enable_semantic_verifier true` |

## Quickstart

```powershell
# 1. Plant the messy seed workspace (10 files: 3 PDFs, 1 CSV, 1 XLSX,
#    2 PNGs, 2 notes, 1 unknown stub).
python examples/research_pack/seed.py

# 2. Preview the 5 stages (no execution).
localflow taskgraph describe examples/research_pack/workspace_pack.yaml

# 3. Run the whole pipeline end-to-end.
localflow taskgraph run examples/research_pack/workspace_pack.yaml --yes

# 4. Inspect the produced pack.
ls examples/research_pack/workspace/
```

## What the pipeline produces

| Stage | Skill | Output |
|---|---|---|
| `s1_organize` | folder_organizer | `papers/` `data/` `images/` `notes/` `misc/` directories + per-category `index.md` files |
| `s2_pdf_index` | pdf_indexer | `pdf_index.md` listing every PDF with extracted title |
| `s3_data_analyze` | data_analyzer | `analysis_report.md` + `analysis_charts/*.png` (groupby + aggregation on the seeded CSV/XLSX) |
| `s4_workspace_chart` | workspace_visualizer | `images/file_counts.png` + `file_counts_summary.md` (post-organize file-count breakdown) |
| `s5_synthesize` | agent (LLM) | `README.md` (top-level entry point linking the per-stage outputs) + `SOURCES.md` (file inventory with sha256s) |

Stages 1-4 are **rule-planned** — fully deterministic, no LLM cost,
works offline. Stage 5 is **LLM-planned** because synthesising prose
and a sources ledger requires real text generation.

## Without an LLM API key

Stage 5 is marked `failure_policy: skip`, so a missing
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` causes only the synthesis
stage to be skipped (with a clean SKIPPED status). You still get a
fully-organized workspace + indexes + analysis + chart from stages
1-4.

## With auto-repair enabled

Phase 13's auto-repair loop is opt-in via memory pref:

```powershell
localflow memory set enable_semantic_verifier true
localflow memory set max_auto_repairs 2
```

When enabled, after each stage's structural verifier passes the
semantic verifier runs (3 LLM-as-judge graders: `output_addresses_goal`,
`summary_grounded`, `analysis_result_nonempty`). If any grader
rejects, the harness rolls back that stage and re-plans with a
grader-derived hint. See
[docs/SEMANTIC_VERIFIER.md](../../docs/SEMANTIC_VERIFIER.md) for the
full mechanic.

## Rollback

The whole pipeline shares one aggregated rollback manifest:

```powershell
localflow rollback --run-id <task_id> --yes
```

restores the original seeded state — every move, every generated
file, every category dir is undone.

## Eval mode

The same pipeline is wired as eval task `task_010_workspace_pack` in
[evals/workspace_pack/](../../evals/workspace_pack/), so you can run:

```powershell
localflow eval run evals/workspace_pack/task_010_workspace_pack.yaml --compare-repair
```

to measure how Phase 13's auto-repair changes the pass rate on this
realistic multi-stage workload.

## Composability

The workspace_pack.yaml is a regular TaskGraph file — append your own
stages (e.g., upload the produced pack to a static-site hosting
service via a custom skill, push the SOURCES.md to a database, etc.)
by editing the YAML. See [docs/TASKGRAPH.md](../../docs/TASKGRAPH.md)
for the full StageSpec schema.
