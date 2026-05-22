# Project Handoff Pack — example workspace (Phase 20)

A mid-project mess (code + notes + data + config + a logo) +
the `project_handoff_pack` recipe → a handoff-ready package.

## Quickstart

```powershell
python examples\project_handoff_pack\seed.py
localflow pack describe project_handoff_pack
localflow pack run project_handoff_pack --workspace examples\project_handoff_pack\workspace --yes
```

## What you get

After a successful run:

```
examples\project_handoff_pack\workspace\
├── code/                          ← Python files moved here
│   ├── main.py
│   ├── scoring.py
│   ├── config.py
│   ├── test_scoring.py
│   └── index.md                   ← per-category index
├── data/
│   ├── sample.csv
│   └── index.md
├── images/
│   ├── logo.png
│   ├── file_counts.png            ← workspace-shape chart
│   └── index.md
├── notes/
│   ├── TODO.md
│   ├── meeting_prep.md
│   └── index.md
├── misc/
│   ├── .env.example
│   ├── pyproject_snippet.toml
│   └── index.md
├── file_counts_summary.md
├── README.md                      ← LLM-written project summary
└── SOURCES.md                     ← file inventory with SHA-256
```

The synthesised `README.md` references the file layout and the
content of `TODO.md` / `meeting_prep.md` so a newcomer can find
their footing without you in the room.

## Recipe behavior

3 stages:

| # | Stage | Skill | Planner | Output |
|---|---|---|---|---|
| 1 | `s1_organize` | `folder_organizer` | rule | per-category dirs + indexes |
| 2 | `s2_workspace_chart` | `workspace_visualizer` | rule | `images/file_counts.png` |
| 3 | `s3_synthesize` | `agent` | **llm** | `README.md` + `SOURCES.md` |

LLM key required for stage 3; without one, stages 1-2 still run
and you keep the organized layout (no synthesised summary).

## Deliverable verifiers

Configured in `recipes/project_handoff_pack.yaml`:

- `deliverable_completeness_verifier` — every output present
- `coverage_verifier` — no input file silently dropped
- `source_ledger_verifier` — SOURCES.md cites real files
- `summary_grounding_verifier` — README grounded in workspace
- `topic_coherence_verifier` — `code/`, `notes/`, etc. are
  semantically coherent buckets

## Rolling back

```powershell
ls .localflow\runs | Sort-Object Name -Descending | Select-Object -First 1
localflow rollback --run-id <run_id>
```

Restores the workspace to the post-seed state: 10 files in the
flat layout, no `code/` / `notes/` / etc. subdirs, no generated
README / SOURCES.
