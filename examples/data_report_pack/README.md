# Data Report Pack — example workspace (Phase 20)

A tabular-only workspace + the `data_report_pack` recipe →
deliverable analytical report.

## Quickstart

```powershell
# 1. Seed the workspace (3 CSVs + 1 XLSX + a README note)
python examples\data_report_pack\seed.py

# 2. Inspect the pack you're about to run
localflow pack describe data_report_pack

# 3. Run the pack
localflow pack run data_report_pack --workspace examples\data_report_pack\workspace --yes
```

## What you get

After a successful run:

```
examples\data_report_pack\workspace\
├── revenue.csv                   (original)
├── users.csv                     (original)
├── errors.csv                    (original)
├── quarterly_summary.xlsx        (original)
├── README.md                     ← LLM-rewritten (replaces the seed README)
├── SOURCES.md                    ← per-file lineage
├── analysis_report.md            ← per-CSV analysis sections
├── analysis_charts/              ← PNG bar / line / hist charts
│   ├── revenue_by_product.png
│   ├── active_users_trend.png
│   └── ...
├── images/
│   └── file_counts.png           ← workspace-shape overview
└── file_counts_summary.md
```

## Recipe behavior

3 stages — narrower than `research_pack` (no PDF index, no organizer):

| # | Stage | Skill | Planner | What it produces |
|---|---|---|---|---|
| 1 | `s1_data_analyze` | `data_analyzer` | rule | `analysis_report.md` + `analysis_charts/*.png` |
| 2 | `s2_workspace_chart` | `workspace_visualizer` | rule | `images/file_counts.png` + summary |
| 3 | `s3_synthesize` | `agent` | **llm** | `README.md` + `SOURCES.md` |

Stage 3 needs an LLM key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in
`.env`). Without one, stage 3 is SKIPPED and you still get the
analysis + overview chart from stages 1-2.

## Deliverable verifiers (Phase 19)

Configured in `recipes/data_report_pack.yaml`:

- `deliverable_completeness_verifier` — every declared output present
- `source_ledger_verifier` — paths in SOURCES.md actually exist
- `summary_grounding_verifier` — README cites real files
- `chart_data_consistency_verifier` — chart numbers match CSV stats

Run output (`localflow pack run`) shows the verdict table and writes
`<run_dir>/recipe_verification.json` with details + repair hints.

## Rolling back

```powershell
# Find the run id (most recent first)
ls .localflow\runs | Sort-Object Name -Descending | Select-Object -First 1

# Undo the whole pack run (all 3 stages, single command)
localflow rollback --run-id <run_id>
```

The rollback restores the workspace to exactly its post-seed state:
3 CSVs + 1 XLSX + the original `README.md` notes file (the LLM-
generated README is removed, the original is restored).
