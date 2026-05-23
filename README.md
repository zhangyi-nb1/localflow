# LocalFlow Agent

[![CI](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![Release](https://img.shields.io/github/v/tag/zhangyi-nb1/localflow?label=release)](https://github.com/zhangyi-nb1/localflow/releases)

> **Branch status** — `main` is **v0.22.0-dev** (Phases 17–22
> productisation arc merged but not yet tagged). Latest tagged release:
> **v0.16.1**. To run the v0.22 feature set today, install from source
> (`pip install -e ".[all]"`). The next tag will cut the Phase 17–22
> work as v0.22.0.

**LocalFlow is a local-first Agent Execution Harness.**
It lets LLM agents work on real local workspaces through typed plans,
preview, approval, controlled execution, trace, independent verification,
repair, and rollback.

Deliverable packs are the current demo and application layer: behind one
approval you can get a README, per-category indexes, reports, charts, a
sources ledger, and a review queue — while the harness keeps the run
auditable and reversible.

> The model proposes typed actions. The harness controls execution.

```powershell
localflow pack run research_pack --workspace .\my_messy_dir\
```

Pick a deliverable pack when you want a ready-made workflow instead of
stitching together skills:

| Pack | Best for | Produces |
|---|---|---|
| **Research Pack** | Mixed PDFs + data + notes + images you want to learn / cite | per-category indexes, per-PDF summaries, analysis report, overview chart, README, SOURCES |
| **Data Report Pack** | CSV / Excel only — you want a presentable analytical report | per-CSV analysis with charts, overview, executive README, SOURCES |
| **Project Handoff Pack** | Mid-project code + notes + data you need to hand off | organized code/notes/data layout, project README, setup notes, SOURCES |

Don't know which fits? Let the Goal Interpreter ask:

```powershell
localflow goal "整理我的研究资料" --workspace .\my_messy_dir\
# → confident pick OR 1-3 clarifying questions OR router fallback
```

---

## What's in a deliverable pack?

A successful `localflow pack run research_pack` against a messy folder yields:

```
my_workspace/
├── papers/, data/, images/, notes/, misc/   ← organized buckets with per-category index.md
├── review/                                  ← unclassifiable files surface here
├── pdf_index.md                             ← every PDF with title + summary
├── analysis_report.md                       ← per-CSV analysis sections + linked charts
├── analysis_charts/                         ← matplotlib PNGs
├── images/file_counts.png                   ← workspace-shape overview
├── file_counts_summary.md
├── README.md                                ← LLM-synthesised, grounded in the above
└── SOURCES.md                               ← every input file with SHA-256 + size
```

Plus, under `.localflow/runs/<run_id>/`:
- `recipe_verification.json` — per-verifier verdicts + hints
- `rollback_manifest.json` — single-command undo for the entire pack
- `trace.jsonl` — every plan / action / verify event, audit-ready

> Looking for an animated end-to-end demo? GIF + before/after
> screenshots are being prepared — see [`assets/README.md`](assets/README.md)
> for the recording spec. Until they land, [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md)
> has a literal text trace of every artifact a real run produces.

---

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[all]"           # everything (dev + mcp + ui + data + pdf + openai)
```

Or pick what you need:

```powershell
pip install -e .                  # base: harness + folder_organizer + LLM clients
pip install -e ".[data]"          # + pandas / matplotlib / openpyxl — chart skills
pip install -e ".[pdf]"           # + pypdf — pdf_indexer
pip install -e ".[ui]"            # + Streamlit (auto-pulls pandas)
pip install -e ".[mcp]"           # + MCP SDK for `localflow mcp-serve`
pip install -e ".[dev]"           # + pytest / ruff + every data / pdf lib for CI
```

## Quickstart A — Browser UI (recommended for demos)

```powershell
localflow ui-serve
# → opens http://127.0.0.1:8501 in your default browser
```

1. **Pick a workspace** in the left sidebar (or visit `?unsafe=1` to pick a path outside `./sandbox/`).
2. **Plan page** — type a goal ("organize by file type", "整理文件并画柱状图统计", anything compound). The agent auto-decomposes it; you see the planned actions + risk badge.
3. **Execute page** — render the dry-run markdown, tick the approval box, hit *Execute*. The verifier runs automatically.
4. **Rollback page** — drift-aware preview + safe/force rollback if you want to undo.

Full walkthrough: [**docs/UI.md**](docs/UI.md) (EN) · [**docs/UI_zh.md**](docs/UI_zh.md) (中文)

## Quickstart B — CLI (developers / scripts / CI)

The 6-stage lifecycle as discrete commands — every command corresponds
to one stage of the harness, with `dry-run` + `verify` explicit so you
can wire it into automation:

```powershell
# 1. PLAN  — the LLM (or rule planner) emits a structured ActionPlan; nothing on disk yet.
localflow plan ./examples/messy_downloads --goal "organize by file type" --planner rule
# → Task created: 2026-05-13-001  ·  Actions: 40  ·  Risk: medium

# 2. DRY-RUN  — render a markdown preview of every action; still read-only.
#    Also mints the approval_token consumed by `execute`.
localflow dry-run --task-id 2026-05-13-001

# 3. EXECUTE  — the ONLY stage that mutates the workspace. `--yes` = explicit approval.
localflow execute --task-id 2026-05-13-001 --yes
# → executed: 40 actions  ·  verify: passed

# 4. VERIFY  — the verifier is rule-based and independent of the model.
localflow verify --task-id 2026-05-13-001
# → PASSED — All 6 checks passed.

# 5. ROLLBACK  — replay the rollback manifest in reverse. Bit-exact restoration.
localflow rollback --run-id 2026-05-13-001 --yes
# → undone: 40  ·  failed: 0
```

A literal trace of every artifact produced by this run — before/after
file trees, plan JSON, dry-run table, verify report, rollback result —
is in [**docs/demo_walkthrough.md**](docs/demo_walkthrough.md).

| Stage | What it does | What can it touch |
|---|---|---|
| `plan` | Produce a Pydantic `ActionPlan` from a workspace scan + a goal | reads workspace, writes only `<task_id>/task.json` and `plan.json` |
| `dry-run` | Render the plan as human-readable markdown + mint MCP approval token | writes only `dry_run.md` and `approval_token.json` |
| `execute` | The **only** stage allowed to perform real filesystem IO | runs every action through `policy_guard` first; appends a `RollbackEntry` per write |
| `verify` | Run rule-based completion checks against on-disk state and the manifest | read-only |
| `rollback` | Replay the manifest in reverse order; restore backups for overwrites | hash-checks each target before touching it; refuses on drift unless `--force` |

---

## Safety model

The model never executes side effects — it only emits a typed
`ActionPlan`. Every write goes through dry-run → approval → executor
(the only module touching the filesystem) → verifier (rules, never the
LLM) → rollback manifest. Paths are kernel-checked against
`workspace_root` + user-set `forbidden_paths` before each action runs;
MCP `execute_plan` additionally requires a one-shot drift-sensitive
approval token minted by `dry_run`. Rollback hash-checks every target
and refuses to clobber drift unless `--force`.

Full threat model + per-mitigation tests: [**docs/SECURITY.md**](docs/SECURITY.md) · [**docs/security_test_matrix.md**](docs/security_test_matrix.md)

### Why a harness, not a naive tool-call agent?

The default "LLM that calls tools" pattern hands the model `shell(cmd)`
or `delete(path)` directly. One hallucination or prompt-injection later,
the filesystem is broken with no preview, no approval, and no undo.
LocalFlow inverts that — the model only emits a Pydantic plan, and the
kernel is the only code allowed to touch disk:

| Property | Naive tool-call agent | LocalFlow |
|---|---|---|
| Dry-run before any write | ✗ | ✓ markdown preview + approval token |
| Workspace boundary enforced | weak (path prefix) | ✓ kernel `resolve_inside` + `forbidden_paths` |
| Single-command rollback of a whole run | ✗ | ✓ `RollbackManifest`, drift-aware |
| Independent verifier (rules, not LLM self-eval) | ✗ | ✓ 6 structural + 7 deliverable checks |
| Action trace, audit-ready | partial | ✓ `trace.jsonl` per run, 7 event sites |

---

## Architecture

Five layers, dependencies point downward, the **Harness Kernel** is the only piece allowed to touch the filesystem:

```
Drivers (CLI · MCP · Streamlit UI)
  └─ Skills (agent meta-skill + folder_organizer · pdf_indexer · data_reporter · data_analyzer · workspace_visualizer · external plug-ins)
       └─ Tool Registry (15 declarable helpers)   |   Harness Kernel (policy_guard / dry_run / approval / executor / verifier / rollback / audit / control_loop)
            └─ Memory (forbidden_paths · naming_style · prefer_llm_planner)
```

Detailed layer-by-layer breakdown, the **8 iron rules** that anchor the design, and the extension guide: [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md)

---

## Evaluation

LocalFlow's reliability is measured by an eval suite, not just unit tests.
Ten starter tasks under [`evals/workspace_pack/`](evals/workspace_pack/)
drive a real workspace end-to-end through the harness and grade the
result.

| Surface | What it asserts | Quantity |
|---|---|---|
| Unit tests | Code-level correctness across 5 OS × Python matrix in CI | **681 passing** |
| Eval tasks | Task-level success on realistic seeded workspaces | **10 tasks** |
| Structural verifiers | Per-run completion checks (rule-based, kernel-side) | **6 checks** |
| Recipe verifiers | Deliverable quality — 4 structural + 3 LLM-as-judge | **7 checks** |
| Rollback restoration | Bit-exact undo + drift detection | covered by `task_001`–`task_007` |
| Policy enforcement | `forbidden_paths` + `forbidden_actions` blocking | `task_003` + `task_004` |

Run the suite: `localflow eval run evals/workspace_pack/ --output report.md`.
Failed pipelines exit with code 1; pipelines that ran cleanly but failed
deliverable quality checks exit with code 3 — so CI distinguishes
"broken" from "shipped but flagged".

Per-task grader API + trace event schema + how to add a grader:
[**docs/EVAL.md**](docs/EVAL.md).

---

## Documentation

| Read this | When you want to |
|---|---|
| [docs/PROJECT_DIRECTION.md](docs/PROJECT_DIRECTION.md) | Understand the harness-first project direction and decision rules |
| [docs/UI.md](docs/UI.md) · [docs/UI_zh.md](docs/UI_zh.md) | Drive LocalFlow from the browser UI |
| [docs/PACK_BUILDER.md](docs/PACK_BUILDER.md) | Understand how a pack composes 5 stages end-to-end |
| [docs/RECIPES.md](docs/RECIPES.md) | Author a new recipe / pack |
| [docs/TASKGRAPH.md](docs/TASKGRAPH.md) | Drive a multi-stage graph by hand (YAML) |
| [docs/VERIFIERS.md](docs/VERIFIERS.md) | Understand the 7 deliverable verifiers |
| [docs/CAPABILITIES.md](docs/CAPABILITIES.md) | Goal Interpreter + typed primitives |
| [docs/REFINE.md](docs/REFINE.md) | Manual `localflow revise` loop |
| [docs/SEMANTIC_VERIFIER.md](docs/SEMANTIC_VERIFIER.md) | Auto-repair (LLM-as-judge) |
| [docs/EVAL.md](docs/EVAL.md) | Eval suite + grader API + trace schema |
| [docs/MCP.md](docs/MCP.md) | Drive LocalFlow as an MCP server |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 5-layer breakdown + 8 iron rules + extension guide |
| [docs/SECURITY.md](docs/SECURITY.md) · [docs/security_test_matrix.md](docs/security_test_matrix.md) | Threat model + per-mitigation tests |
| [docs/PHASES.md](docs/PHASES.md) | Full per-phase changelog + §10.7 ledger |
| [docs/demo_walkthrough.md](docs/demo_walkthrough.md) | A literal end-to-end CLI trace |

Releases (with verified wheel artifacts) under [**GitHub Releases**](https://github.com/zhangyi-nb1/localflow/releases).

---

## License

MIT — see [pyproject.toml](pyproject.toml).
