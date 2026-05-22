# Capability Primitives (v0.18.0)

> Productisation guide §4.3 — "from skill-first to recipe-first":
> recipes compose **capability primitives**, not skills directly.
> This document is the canonical inventory of those primitives.

LocalFlow's layering, after Phase 18:

```
┌─────────────────────────────────────────────────────────────┐
│ User goal                                                   │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Goal Interpreter  (Phase 18 — app/agent/goal_interpreter.py)│
│   • Router scores recipes deterministically                 │
│   • LLM asks clarifying questions when ambiguous            │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Recipe layer  (Phase 17 — app/recipes/, recipes/*.yaml)     │
│   compile_to_taskgraph()                                    │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ TaskGraph runner / Harness Kernel (Phase 10 / §10.7)        │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Skill layer  (folder_organizer / pdf_indexer / …)           │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Capability Primitives  (Phase 18 — app/primitives/)         │
│   typed inputs/outputs; thin wrappers over tools            │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────┐
│ Tool layer  (app/tools/* — pure functions)                  │
└─────────────────────────────────────────────────────────────┘
```

The **primitive layer is intentionally thin**. Its purpose is to give
the LLM Goal Interpreter (and future deliverable verifiers) a stable
typed surface to refer to — *not* to replace skills or tools. A new
backend (vision LM, MCP server, …) can swap in behind a primitive
without changing the verifier consuming its output.

## The 10 primitives

| # | Primitive | Status (v0.18) | Implementation | Productisation guide §4.3 |
|---|---|---|---|---|
| 1 | `extract_content` | ✅ **typed wrapper** in `app/primitives/extract_content.py` | Dispatch over ContentKind → `app.tools.pdf_ops` / `text_ops` / `data_ops` | Listed |
| 2 | `classify_content` | ✅ **typed wrapper** in `app/primitives/classify_content.py` | Curated extension table; Phase 19 may add an LLM variant | Listed |
| 3 | `cluster_topics` | 🟡 catalog-only | `app/skills/topic_clusterer` | Listed |
| 4 | `generate_index` | 🟡 catalog-only | `app/skills/folder_organizer` (emits per-category `index.md` as part of MOVE actions) | Listed |
| 5 | `build_source_ledger` | 🟡 catalog-only | `app/tools/source_ledger_ops` already provides a typed `SourceLedger` (Phase 14.1) | Listed |
| 6 | `analyze_table` | 🟡 catalog-only | `app.tools.data_analysis.execute_analysis` + `app.skills.data_analyzer` | Listed |
| 7 | `render_chart` | 🟡 catalog-only | `app/tools/chart_ops` (already typed) | Listed |
| 8 | `synthesize_report` | 🟡 catalog-only | `app/skills/agent` LLM path (must keep going through the Harness for approval / rollback) | Listed |
| 9 | `fetch_sources` | 🟡 catalog-only | `app/skills/webcollect` + `ActionType.FETCH` (Phase 16's 2nd §10.7 exception) | Listed |
| 10 | `validate_deliverable` | 🟡 catalog-only | `app.eval.graders.*` (Phase 19 will wrap with a typed primitive) | Listed |

### Why only 2 are "real wrappers"?

The productisation guide warns against premature abstraction. A
primitive should **earn its wrapper** when at least one of these is
true:

- The Goal Interpreter (Phase 18) actually needs to compose it at the
  function level.
- A verifier (Phase 19) needs a stable output schema to inspect.
- A future backend (vision LM, MCP) needs a swap-in point.

For Phase 18, only `extract_content` and `classify_content` clear that
bar. The other 8 are documented in `app/primitives/catalog.py` so
authors know they exist and where to find them — that catalog is the
single source of truth for the LLM's tool descriptions and for
future-phase planning.

## The catalog API (`app.primitives.catalog`)

```python
from app.primitives import get_catalog, get_primitive, list_names

for name in list_names():
    entry = get_primitive(name)
    print(name, entry.implemented, entry.backed_by)
```

Each :class:`PrimitiveEntry` carries:

- `name` — stable identifier.
- `summary` — one-line product description.
- `implemented: bool` — True only when `callable_` is wired up.
- `backed_by` — tool / skill that currently provides the behaviour
  (for the LLM Goal Interpreter to reference, and for verifiers to
  trace lineage).
- `callable_` — the Python function for the 2 implemented entries.

## The typed I/O schemas (`app.primitives._schemas`)

Every implemented primitive uses these:

- `ContentRef` — pointer to a file (`rel_path` + `kind` + `size_bytes`
  + optional `sha256`).
- `Content` — output of `extract_content`: `preview` text + `metadata`
  dict + optional `error` code.
- `Classification` — output of `classify_content`: `label` + `confidence`
  + optional `rationale`.
- `ContentKind` enum — coarse `document / note / table / image / code
  / structured / binary` taxonomy used by the interpreter when it
  reasons about workspaces.

Phase 19 will reuse these schemas in the deliverable verifiers (e.g.
`SourceLedgerVerifier` will assert that every `Content.ref` mentioned
in a generated `SOURCES.md` actually traces back to a real file).

## Adding your own primitive

1. Decide whether it really needs a typed wrapper (see "earn its
   wrapper" above). If not, just add a `PrimitiveEntry` to
   `app/primitives/catalog.py` pointing at the existing skill / tool.
2. If it does, write a module under `app/primitives/` with a single
   public function taking and returning the typed schemas in
   `_schemas.py`. Re-export from `app/primitives/__init__.py`.
3. Register the entry in `catalog.py` with `implemented=True`.
4. Add I/O contract tests in `tests/test_primitives.py`.

## What's NOT in v0.18

- Wrapping the other 8 entries — deferred until a Phase 19 verifier or
  Phase 20 recipe needs them at the function level.
- Multi-modal primitives (vision-language extraction of images) —
  Phase 22+ when the WebCollect deepening lands.
- A primitive-level execution trace — Phase 9's TraceLogger already
  captures skill / tool calls; a separate primitive trace would be
  duplication.
