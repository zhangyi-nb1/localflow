# Recipes / Packs — the productisation layer (v0.17.0)

> Productisation guide §5 (positioning) + §12 Phase B (Recipe / Pack System).

LocalFlow's Phase 17 introduces a **Recipe** layer above TaskGraph.
Where TaskGraph asks "which skills run in what order?", a Recipe
answers the question the user actually has:

> "What deliverable pack do I want?"

The user picks a pack name (`research_pack`, `data_report_pack`,
`project_handoff_pack`) — never a skill name — and the harness
compiles it down to a TaskGraph the v0.11 runner already knows how
to execute.

## Why Recipes (vs. just writing more skills)

The productisation guide diagnoses LocalFlow's biggest product-shape
issue (§3.1): "current features exist as **modules**, not as **user
outcomes**." The fix (§4.3) is to flip from skill-first to
recipe-first:

| Skill-first (pre-v0.17) | Recipe-first (v0.17+) |
|---|---|
| User has to know skill names | User picks a pack |
| Each new need → new skill | New need → new recipe over existing skills |
| Output: a folder somewhere | Output: a labelled deliverable pack |
| Surface: "Choose `folder_organizer`" | Surface: "Create a Research Pack" |

§10.7 invariant maintained: **zero kernel changes**. Recipes are an
application-layer concept that compile to TaskGraph, which the
existing executor / verifier / rollback paths drive unchanged.

## Schema (matches productisation guide §12 Phase B verbatim)

```yaml
name: research_pack
title: Research Pack
description: |
  Turn a messy pile of research material...
tags: [research, study, knowledge]

input_expectation:
  file_kinds: [pdf, text, tabular, excel, image]
  min_files: 2
  require_any: [pdf, text, tabular, excel]
  keywords: [research, paper, 资料, 研究, ...]

stages:
  - stage_id: s1_organize
    title: Categorise files by type
    skill: folder_organizer
    planner: rule
    expected_outputs: [papers/index.md, data/index.md, ...]
  # ... more stages

expected_outputs:
  - papers/index.md
  - pdf_index.md
  - analysis_report.md
  - README.md
  # ...

verifiers:
  - expected_outputs_present
  - every_input_accounted_for
  - rollback_restores

repair_policy:
  enabled: false
  max_rounds: 1
```

Every field maps to behaviour:

- `input_expectation` → the **router** (`app.recipes.router`) uses
  these signals to rank recipes against a real workspace. No LLM in
  v0.17 — pure deterministic keyword + file-kind scoring.
- `stages` → translates 1:1 to `StageSpec`. Each entry is a `(skill,
  planner, expected_outputs, failure_policy)` tuple identical to a
  TaskGraph YAML.
- `expected_outputs` → the **pack-level** deliverables surfaced in
  `pack describe` and the Streamlit Pack page. Sum of every stage's
  outputs plus any synthesised top-level files.
- `verifiers` → recipe-level grader names. Phase 17 records as
  metadata; Phase 19 wires them into the harness so a pack is marked
  PARTIAL when a deliverable-level grader fails.
- `repair_policy.enabled` → when true, `compile_to_taskgraph()`
  promotes every stage whose `failure_policy` is `ABORT` to `REPAIR`
  with `max_retries=max_rounds`. `SKIP` / `CONTINUE` stages keep
  their authored policy.

## The three flagship packs (v0.17.0)

| Pack | Best for | Stages | Deliverables |
|---|---|---|---|
| `research_pack` | Researchers / students / knowledge workers | 5 | `papers/index.md`, `data/index.md`, `images/index.md`, `notes/index.md`, `misc/index.md`, `pdf_index.md`, `analysis_report.md`, `images/file_counts.png`, `file_counts_summary.md`, `README.md`, `SOURCES.md` |
| `data_report_pack` | Data-only workspaces (CSV / XLSX) | 3 | `analysis_report.md`, `images/file_counts.png`, `file_counts_summary.md`, `README.md`, `SOURCES.md` |
| `project_handoff_pack` | Code projects being handed off | 3 | `code/index.md`, `data/index.md`, `images/index.md`, `notes/index.md`, `misc/index.md`, `images/file_counts.png`, `file_counts_summary.md`, `README.md`, `SOURCES.md` |

Each ships in `recipes/*.yaml` at the repo root. Override the
directory via `LOCALFLOW_RECIPES_DIR=/path/to/your/recipes`.

## CLI surface

```powershell
# Browse — what packs are available?
localflow pack list

# Detail — what does this pack produce?
localflow pack describe research_pack

# Suggest — which pack fits this workspace?
localflow pack suggest ./my_messy_dir --goal "整理研究资料"

# Run — compile a recipe to a TaskGraph and execute end-to-end.
localflow pack run research_pack --workspace ./my_messy_dir

# With auto-repair (also needs:
#   localflow memory set enable_semantic_verifier true)
localflow pack run research_pack --workspace ./my_messy_dir --enable-repair
```

`localflow pack run` is functionally identical to
`localflow taskgraph run` against the compiled graph — same approval
ceremony, same single aggregated rollback, same trace events. The
only difference is *what* the user is approving (a named pack vs. a
raw YAML).

## UI surface

The Streamlit UI gains a new `📦 Pack` page (filename prefix `0_` so
it appears first in the sidebar). Three sub-flows on one page:

1. **Browse** — every loaded recipe as an expandable card with
   description, tags, stage list, and a `▶ Run` button.
2. **Suggest** — workspace scan + (optional) goal text → ranked
   recipe table.
3. **Run** — inline execution. Result table + rollback hint shown
   in-page.

## Router scoring rule (deterministic, no LLM)

```
score = (keyword hits × 2)
      + (file-kind matches, capped at 5)
      - (10 if min_files violated)
      - (5  if require_any violated)
```

Ties broken by recipe name (alphabetical) for determinism. A negative
score means "unsuitable"; positive = "consider". `best_match()`
refuses to return a zero-score recipe — the CLI surfaces "no recipe
matched, run `localflow pack list`" instead.

Phase 17 stops here intentionally. **Phase 18** will add a
GoalInterpreter that, when no recipe scores high enough, asks
clarifying questions via the LLM and falls back to a router pick.

## Adding your own recipe

1. Drop a YAML file into `recipes/` (or set `LOCALFLOW_RECIPES_DIR`).
2. Make sure every `skill:` value resolves in the SkillRegistry
   (`localflow skills list`).
3. Re-run `localflow pack list` to confirm it loaded; load errors
   surface in a separate warning section.

The schema enforces:
- Unique stage_ids within a recipe.
- At least one stage.
- `repair_policy.max_rounds` ∈ [1, 3].
- Pydantic v2 strict mode for every field.

## Backwards compatibility

- `examples/research_pack/workspace_pack.yaml` (the v0.14.0 demo)
  remains valid and usable via `localflow taskgraph run` — Phase 17
  is additive.
- Old eval tasks (`task_007`, `task_010`) still pass unchanged.
- The Plan / Execute / Rollback / Memory pages in the UI are
  untouched. Pack is a fourth flow alongside them.
