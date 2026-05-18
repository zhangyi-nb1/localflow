# LocalFlow — Phase-by-Phase Changelog

Source of truth for what shipped, when, and what touched the kernel.
Each phase has been delivered in a focused single-sitting session and
verified with both unit tests and real-data implementation. The §10.7
ledger (kernel-touch count) is tracked explicitly — a project rule is
"adding a new Skill / Tool / Memory category should NOT require kernel
modifications". Phases 1-4.3 + 6.1 = zero kernel touches. Phase 5 is the
documented exception (~25 lines for `forbidden_paths`, a universal
safety primitive that *must* live kernel-side to be plug-in robust).

---

## Phase 0 — Harness skeleton (no LLM)

**Goal**: prove the harness is usable before plugging in an LLM. Build
the inspect → plan → dry-run → approve → execute → verify → rollback
control loop with a rule-based folder organizer.

**Shipped**:
- Pydantic schemas for `TaskSpec` / `ActionPlan` / `Action` / `RollbackManifest` / `VerificationResult` / `WorkspaceSnapshot`
- `policy_guard` (action-type allow-list, path containment)
- `Executor` with per-action defense-in-depth re-check, JsonlLogger audit
- `Verifier` (rules-only, independent of any LLM)
- `Rollback` with backup restore
- CLI: `inspect / plan / dry-run / execute / verify / rollback / status`
- `folder_organizer` skill (rule planner, classifies by extension)

**Files**: entire `app/harness/`, `app/schemas/`, `app/storage/`, `app/tools/file_*`, `app/skills/folder_organizer/`

**Tests added**: ~53 (foundation)

**Kernel touch**: this *is* the kernel — baseline.

---

## Phase 1 — LLM planner

**Goal**: let an LLM emit a typed `ActionPlan` via strict tool call.
Critical: model must NEVER directly write to disk — only emits the
plan; harness performs IO.

**Shipped**:
- `app/agent/`: `OpenAIClient`, `AnthropicClient`, strict tool-call
  schema, `tool_result` repair loop on Pydantic validation failure
- SSE streaming output with Rich Live in CLI (long planning calls
  stream tokens in real time)
- Default provider is OpenAI-compatible; configure via
  `LOCALFLOW_LLM_PROVIDER` / `LOCALFLOW_LLM_MODEL` /
  `LOCALFLOW_LLM_BASE_URL` in `.env` (see `.env.example`)

**Files**: `app/agent/`, `app/cli.py` (new `--planner llm` path)

**Tests added**: ~10

**Kernel touch**: NO — agent layer sits *above* the harness, only
produces `ActionPlan` to feed in.

---

## Phase 2.1 + 2.2 — Content awareness

**Goal**: let the planner see the *contents* of files, not just names.

**Shipped**:
- `app/tools/pdf_ops.py`: pypdf-based text preview (graceful return
  `None` on encoded / scanned / broken PDFs)
- `app/tools/text_ops.py`: first ~2000 chars of text/code/structured/
  tabular files, with NUL-byte binary detection
- `file_scan.scan_workspace(compute_preview=True)` injects previews
  into `FileMeta.text_preview`
- `agent/prompts.py` renders previews into the LLM system prompt;
  prompts updated to encourage semantic rename / classification

**Files**: `app/tools/pdf_ops.py`, `app/tools/text_ops.py`,
`app/tools/file_scan.py` (extended), `app/agent/prompts.py` (extended)

**Tests added**: 14

**Kernel touch**: NO — new module under `app/tools/`, used by
existing scan flow.

---

## Phase 2.3 — Skill ABC + plug-in pattern + `pdf_indexer`

**Goal**: turn LocalFlow from "single-purpose tool" into a Skill
plug-in framework. Each task feature becomes a `Skill` subclass.

**Shipped**:
- `Skill` ABC with `manifest / plan / plan_with_llm / validate /
  report` lifecycle ([app/skills/_base.py](../app/skills/_base.py))
- `SkillRegistry` — process-wide skill catalog
- `pdf_indexer` skill: scan PDFs → extract titles → synthesize a
  single `pdf_index.md` with provenance metadata (Open Deep
  Research-style)
- `folder_organizer` retrofitted as a `Skill` subclass
- CLI fully switched to registry dispatch (`--skill <name>`)

**Files**: `app/skills/_base.py`, `app/skills/folder_organizer/skill.py`,
`app/skills/pdf_indexer/`

**Tests added**: 20

**Kernel touch**: NO — Skill ABC lives in `app/skills/`, the kernel
only knows about the typed return shapes.

---

## Phase 3.1–3.3 — DataOps (`data_reporter` + `data_analyzer`)

**Goal**: read CSV/XLSX, produce report + analysis, render charts.
Without ever giving the LLM raw `exec()` capability.

**Shipped**:
- **3.1**: `data_reporter` skill emits `data_report.md` with per-table
  schema + numeric stats + sample rows (rule-based, no LLM)
- **3.1c**: `RESTORE_FROM_BACKUP` rollback op for "overwrite original
  with backup on undo"
- **3.2**: matplotlib chart generation (`chart_ops.histogram_png` /
  `bar_png`), binary action payload via `metadata.binary_content_b64`,
  `_record_implicit_parents` tracks `mkdir(parents=True)` for rollback
- **3.3a**: typed `AnalysisSpec` Pydantic schema (filter → groupby →
  sort → limit → chart). `data_analysis.execute_analysis` is a pure
  function: spec in, `AnalysisResult` out, NO eval/exec
- **3.3b**: LLM planner outputs `AnalysisSpec` (not pandas code) via
  strict tool call, harness's engine runs it. **Iron rule ⑤ kept** —
  the model still doesn't write code.

**Files**: `app/tools/data_ops.py`, `app/tools/chart_ops.py`,
`app/tools/data_analysis.py`, `app/schemas/analysis.py`,
`app/skills/data_reporter/`, `app/skills/data_analyzer/`,
`app/agent/analysis_prompts.py`, `app/agent/analysis_planner.py`

**Tests added**: ~60 (cumulative across 3.1 → 3.3b)

**Kernel touch**: NO — added one helper `_record_implicit_parents` to
the executor, **internal** to the existing `_do_index` path; no API
change and no new safety primitives.

---

## Phase 4.1 — Filesystem skill discovery

**Goal**: make LocalFlow a *real* plug-in framework. Drop a skill
folder anywhere LocalFlow looks, it loads at startup.

**Shipped**:
- `app/skills/_loader.py`: `discover_and_register_external(registry,
  dirs)` uses `importlib.util.spec_from_file_location` with hashed
  module namespace per skill (avoids `app.skills.*` collisions). Each
  load attempt becomes a `LoadFinding` entry — failures don't block
  other skills.
- Search paths (priority): `$LOCALFLOW_SKILLS_DIR` (multi-path) →
  `<cwd>/.localflow/skills/` → `~/.localflow/skills/`
- Name collision resolution: built-ins register first; external
  collisions error out, logged in audit
- `localflow skills` CLI command: registered table + search-paths +
  full load audit
- [examples/external_skill_example/](../examples/external_skill_example/):
  `workspace_stats` plug-in + multi-flavor install README

**Files**: `app/skills/_loader.py`, `app/cli.py` (new command),
`examples/external_skill_example/`

**Tests added**: 11

**Kernel touch**: NO — loader sits in `app/skills/`, kernel unaware.

**Outline §10.7 attestation**: project becomes "a framework", not "a
tool" — users write a 50-line `Skill` subclass without touching source.

---

## Phase 4.2 — Tool Registry

**Goal**: inventory the shared callable helpers (file_scan, pdf_ops,
data_ops, ...) skills are allowed to use, validate `required_tools`
declarations at register time. Composio-style.

**Shipped**:
- `app/tools/_registry.py`: `ToolSpec` (frozen dataclass: name,
  callable_ref, module, category, description, side_effects=False),
  `ToolRegistry` (register / get / has / list), lazy default factory
- 15 tools registered: 11 read / 2 transform / 2 render. `file_ops.*`
  (mutating IO) **intentionally excluded** — kernel-only, never
  Skills' to call directly
- `SkillManifest.required_tools: list[str] = []` (new field). When
  `SkillRegistry.register(skill, tool_registry=...)` is called, every
  declared name must resolve, else `SkillError`
- `localflow tools` CLI command + new "Tools" column in
  `localflow skills`

**Files**: `app/tools/_registry.py`, `app/schemas/skill.py` (extend),
`app/skills/_base.py` (validation hook), each built-in's `skill.py`
(declares deps)

**Tests added**: 24

**Kernel touch**: NO — `app/skills/_base.py` and `app/schemas/skill.py`
are framework, not kernel. Outline §10.7 still holds.

---

## Phase 4.3 — Unified Skill Contract Test Template

**Goal**: every skill (built-in or external) plug-able into a single
8-stage lifecycle test. "Does my skill work with LocalFlow?" gets a
one-call answer.

**Shipped**:
- `app/skills/_contract.py`: `run_skill_contract(skill, *,
  workspace_seeder, workspace_root, run_store, ...)` → `ContractReport`
  with `StageResult[]`. 8 stages: `manifest_valid` →
  `plan_empty_workspace` → `plan_happy_path` → `validate_accepts_own_plan`
  → `validate_rejects_garbage` → `execute_and_verify` →
  `rollback_restores` → `report_non_empty`. Each stage in its own
  try/except so a failure surfaces all downstream skips with reasons.
- All 4 built-ins parametrized through the contract; `folder_organizer`
  finally gets dedicated E2E coverage (previously only tested
  indirectly).
- External skill example: [examples/external_skill_example/test_contract.py](../examples/external_skill_example/test_contract.py)

**Files**: `app/skills/_contract.py`, `tests/test_skill_contracts.py`,
`tests/test_skill_registry.py` (extended)

**Tests added**: 10

**Kernel touch**: NO. Contract treats `Executor` / `Verifier` /
`Rollback` as black boxes.

---

## Phase 5 — Memory & personalization MVP

**Goal**: persistent user preferences. Outline §14 lists 5 categories;
this MVP ships 2 + lays the framework.

**Shipped**:
- `app/memory/`: `MemoryPreferences` (Pydantic schema with
  `forbidden_paths: list[str]` + `naming_style: NamingStyle` +
  `schema_version: int`), `MemoryStore` (atomic write, JSONL audit
  log), `apply_naming_style(name, style)` (4 styles: original /
  snake_case / kebab-case / lower)
- `TaskSpec` schema gets `forbidden_paths: list[str] = []` +
  `preferences: dict[str, Any] = {}` (skill-consumable bag)
- **KERNEL TOUCH** (the only one): `policy_guard.evaluate_action` /
  `assess_plan` / `_check_path_fields` gain `forbidden_paths` keyword
  parameter; `Executor.__init__` adds the same. ~25 lines total,
  fully backwards-compatible (defaults to empty tuple).
- `folder_organizer.planner` reads
  `task.preferences.get("naming_style", "original")` and transforms
  filenames at line 89
- CLI: `localflow memory list / forbid / unforbid / set / unset /
  audit` sub-app; plan command prints "Applied preferences from
  memory: ..." header when non-default

**Files**: `app/memory/`, `app/schemas/task.py`,
`app/harness/policy_guard.py` + `executor.py` + `control_loop.py`,
`app/skills/folder_organizer/planner.py`, `app/cli.py`

**Tests added**: 60

**Kernel touch**: **YES** — the documented exception. `forbidden_paths`
MUST be kernel-side; otherwise a forgetful Skill author could silently
bypass a user's "never touch X" rule. Doing it skill-side would
defeat the whole "plug-in safety" claim Phase 4 was built around.

**Outline §10.7 ledger**: 11 consecutive zero-kernel phases broken
deliberately. Phase 6.1 resumes the streak.

---

## Phase 6.1 — LocalFlow as MCP server

**Goal**: expose existing CLI surface to external MCP clients (Claude
Code, Claude Desktop, ...) over stdio JSON-RPC. **Zero new behavior** —
just wrap.

**Shipped**:
- `mcp = ["mcp>=1.6,<2.0"]` optional dep
- `app/mcp/`: `_serialize.py` (Pydantic/dataclass/Path/datetime/enum
  → JSON-safe), `tools.py` (15 `ToolDef` + handlers), `server.py`
  (`run_mcp_server()` boots stdio_server + Server.run; exceptions
  become `{"error": ...}` payloads, never break protocol)
- `localflow mcp-serve` CLI command (lazy SDK probe, graceful error
  when uninstalled)
- 15 MCP tools: read-only 7, state-changing 4 (create_plan / dry_run /
  execute_plan [requires `approved: true`] / rollback_run), memory
  mutations 4
- `[docs/MCP.md](MCP.md)`: setup + 15-tool reference + Claude Code
  config snippet

**Files**: `app/mcp/`, `app/cli.py` (one new command),
`pyproject.toml` (one new optional dep), `docs/MCP.md`

**Tests added**: 24

**Kernel touch**: NO — fully additive. **§10.7 streak resumes.**

**Real-world verification**: end-to-end JSON-RPC roundtrip via
`mcp.client.stdio.stdio_client` confirmed. `.mcp.json` wired into
Claude Code, `list_skills` / `create_plan` / `dry_run` / `execute_plan`
(with `approved=true`) / `rollback_run` all driven by Claude.
**Critical safety property held**: Phase 5's `forbidden_paths` blocked
execution through the MCP path identically to the CLI path —
`secrets/creds.txt` never touched.

---

## Phase 8.0 — Streamlit UI MVP (v0.7.0)

**Goal**: replace 5-step CLI commands with a clickable browser UI.
Outline L466/L512/L800/L1315 deferred UI until "CLI/API stable" —
v0.6.3 satisfied that condition, so v0.7.0 ships the UI.

**Shipped**:
- `app/ui/` package: `_sandbox.py`, `_layout.py`, `main.py`, 4
  pages (Plan / Execute / Rollback / Memory)
- `localflow ui-serve` CLI command — defaults to `127.0.0.1:8501`,
  graceful failure when streamlit dep missing
- Soft sandbox: workspace dropdown only shows subdirs of `./sandbox/`
  by default; `?unsafe=1` URL flag lifts with prominent banner
- Approval ceremony in browser: dry-run renders, checkbox required,
  Execute button only enabled after checkbox tick
- Rollback page visualizes Phase 7.1 drift detection — yellow rows
  for entries where the file changed since execute, safe-vs-force
  buttons

**Files**: `app/ui/` (new package), `app/cli.py` (one new command),
`pyproject.toml` (one new optional dep + version bump)

**Tests added**: 17 (`tests/test_ui_sandbox.py`) — sandbox boundary
parsing, query-param truthy values, eligible-workspace listing.
Streamlit UI itself smoke-tested via subprocess + HTTP 200 probe.

**Kernel touch**: NO — `app/ui/` is a brand-new driver layer that
reuses `control_loop.*` / `MemoryStore` / `Rollback` as black boxes.
**11th** zero-kernel phase (Phase 5 remains the lone exception).

**Reuses Phase 7.1**: the UI's Execute page mints approval tokens
the same way the MCP server does (`mint_token`), so both drivers
have symmetric "dry-run → approve → execute" ceremony. The
Rollback page uses `Rollback.preview()` to show drift inline.

---

## Phase 8.0.1–8.0.4 — UI hardening (v0.7.1–v0.7.4)

Bug-fix releases on top of the Phase 8.0 MVP:

- **v0.7.1**: disable Streamlit's first-run email prompt (was
  silently waiting for input on a hidden stdin)
- **v0.7.2**: add "🔍 Continue to Execute →" + "↺ Continue to
  Rollback →" cross-page navigation buttons after a successful
  plan / execute
- **v0.7.3**: fix `RollbackPreview.entry_count` AttributeError
  (regression test in `test_rollback.py`)
- **v0.7.4**: in the Rollback page, split `outcome.failed` into
  cascaded-from-conflict vs real failures — `delete_created_dir`
  ops that fail because the dir is non-empty due to a user-kept
  conflict are surfaced as PARTIAL with a blue info note, not
  red FAILED.

**Kernel touch**: NO. All four releases ship UI-side changes only.

---

## Phase 8.1 — UI UX overhaul (v0.8.0)

**Goal**: address three real-user pain points exposed by v0.7.0–v0.7.4
testing — Custom path was unreachable, the Skill/Planner dropdowns
exposed internals users didn't want to see, and the UI was English-only.

**Shipped**:
- `app/ui/_i18n.py` — flat `_DICT` (~120 keys), `t(key, **kwargs)`
  lookup with two-level fallback (requested lang → English →
  `!!key!!` sentinel), `render_language_toggle()` sidebar widget
  bound to `st.session_state["ui_lang"]`. Streamlit-free at import
  time so the dict is unit-testable.
- `app/ui/_autodetect.py` — `autodetect_skill()` +
  `autodetect_planner()`. Combines goal keywords (bilingual lists)
  with the workspace's file-type distribution and the skill's
  `supports_llm()` flag to pick both layers. Returns a reason string
  so the UI can explain its choice.
- `app/ui/_layout.py` — sidebar rewritten around a workspace-source
  radio (Sandbox subdir vs Custom path). Active-workspace badge
  always at the top. Custom-path input lives directly under the
  radio (no expander) with live validation; the option is hidden
  entirely without `?unsafe=1` (Streamlit can't disable a single
  radio choice). Language toggle at the very top.
- `app/ui/pages/1_Plan.py` — goal-only form, auto-detect badge + reason
  line, `▶ Override (advanced)` collapsed expander with the
  classic Skill + Planner widgets for power users. Auto-detect runs
  on every keystroke (cheap workspace scan cached per workspace
  in session_state).
- Every UI string (~120 unique keys) routed through `t()` —
  language toggle now switches the entire app instantly.

**Files**: `app/ui/_i18n.py` (NEW), `app/ui/_autodetect.py` (NEW),
`app/ui/_layout.py` (rewritten), `app/ui/main.py` (i18n migration),
`app/ui/pages/1_Plan.py` (rewritten), `app/ui/pages/2_Execute.py`
(i18n migration), `app/ui/pages/3_Rollback.py` (i18n migration),
`app/ui/pages/4_Memory.py` (i18n migration). `pyproject.toml`
version 0.7.4 → 0.8.0.

**Tests added**: 36 (`test_ui_i18n.py` 16 + `test_ui_autodetect.py`
20) — covers every autodetect branch, every i18n contract clause,
every critical key. Pure Python, zero Streamlit dep. Total suite
283 → 318.

**Kernel touch**: NO. `app/harness/`, `app/schemas/`, `app/tools/`,
`app/skills/`, `app/memory/`, `app/mcp/` all untouched. **12th**
zero-kernel-touch phase. (Phase 5 remains the lone exception.)

**Design note on the Override expander**: the user's original ask
was to hide Skill + Planner entirely. I pushed back: when the
heuristic guesses wrong, users with no override would be stuck.
A collapsed expander gives the dead-simple flow (95% case) without
trapping power users. The decision is surfaced in the auto-detect
reason line so users know exactly what's being chosen.

---

## Phase 14 — Workspace Pack Builder (v0.14.0)

**Trigger**: v0.10-v0.13 each shipped a substrate piece (TaskGraph,
Plan Refinement, Data-Aware Routing, Auto-Repair). Each was a
capability in isolation. The experiment report's `Section 8` calls
out **Workspace Pack Builder** as the canonical strong demo proving
those layers actually stack into a real-world pipeline. v0.14
delivers exactly that.

**Goal**: turn a messy research workspace (PDFs + CSVs + images +
notes) into a deliverable knowledge pack via one command. Compose
every existing skill into a 5-stage TaskGraph; bundle the example
workspace + a runnable YAML graph + an eval task that benchmarks
the pipeline under v0.13's `--compare-repair` mode.

**Shipped**:
- `examples/research_pack/seed.py` — script planting a 10-file
  messy workspace (3 PDFs with real %PDF headers so pypdf can
  extract titles, 1 CSV with 30 rows of synthetic experiment data,
  1 XLSX with model scores, 2 PNGs, 2 notes, 1 unknown-type stub).
- `examples/research_pack/workspace_pack.yaml` — the canonical
  5-stage TaskGraph (folder_organizer → pdf_indexer → data_analyzer
  → workspace_visualizer → agent). Stages 1-4 rule-planned;
  stage 5 LLM-planned with `failure_policy: skip` so CI without an
  API key still produces stages 1-4 outputs.
- `examples/research_pack/README.md` — quickstart.
- `app/eval/graders/structural.py` — new `every_input_accounted_for`
  grader (Phase 14 coverage check: each seeded file must be either
  moved to a target dir OR cited by basename in a generated `.md`
  report).
- `evals/workspace_pack/task_010_workspace_pack.yaml` — eval task
  with 6 graders + `must_pass` set tuned so the task passes in CI
  even when stage 5 skips.
- `tests/test_coverage_grader.py` — 4 tests pinning the grader's
  two coverage branches + empty-seed + missing-file edge case.
- `tests/test_pack_builder_demo.py` — 4 tests including a real
  end-to-end run of stages 1-4 against the seeded workspace.
- `docs/PACK_BUILDER.md` — full walkthrough.

**Live verification** (run during development):
- `python examples/research_pack/seed.py` plants 10 files.
- `localflow taskgraph run examples/research_pack/workspace_pack.yaml --yes`
  with an LLM key configured: ALL 5 stages PASSED, total ~19 s
  (16 s of which is stage 5's LLM call). The produced workspace
  contained the full pack: README.md, pdf_index.md,
  analysis_report.md, analysis_charts/ (with pie charts —
  v0.12.0's Phase 12 heuristic kicked in for the model column),
  per-category dirs with index.md files, duplicates_report.md,
  file_counts_summary.md, sources ledger.

**Kernel touch**: NO. Pure composition + 1 grader + YAML + docs.
**23rd** zero-kernel-touch phase. `app/harness/*` unchanged.

---

## Phase 13 — Semantic Verifier + Auto-Repair Loop (v0.13.0)

**Trigger**: v0.12 closed the *user-driven* correction loop (manual
`localflow revise --hint`). The natural follow-up is the *automatic*
counterpart — give the harness an LLM-as-judge layer that runs
*after* execute + structural verify, and a retry-with-repair cycle
that fires when a semantic grader rejects the run.

The three motivating failure modes (all observed in eval traces):

- `data_analyzer` produces `analysis_report.md` whose every analysis
  ended in `EMPTY_RESULT` (column reference miss) — structural OK,
  semantically empty.
- `folder_organizer` writes `papers/index.md` containing generic
  boilerplate that doesn't mention the actual files it claims to
  index.
- `agent` produces a chart whose X-axis labels don't match any
  category in the source data.

**Shipped**:
- `app/schemas/semantic.py` — `SemanticVerdict` + `SemanticVerificationResult`
- `app/agent/judge.py` — thin LLM-as-judge wrapper (typed
  `{verdict, reason, suggested_hint}` schema; graceful no-op when no
  API key)
- `app/eval/graders/semantic.py` — 3 starter graders, registered via
  the same `@register` decorator as structural ones
- `app/harness/semantic_verifier.py` — runtime verifier (separate
  from the structural `Verifier`; §10.7 boundary preserved)
- `app/harness/repair_loop.py` — orchestrates rollback → revise →
  re-execute → re-verify, bounded by `max_auto_repairs`
- `app/harness/control_loop.py` — new `run_with_auto_repair` composite
  (additive — existing `run_*` functions unchanged)
- `app/harness/taskgraph_runner.py` — `failure_policy: repair`
  dispatch inside `_run_one_stage` (existing dispatch at the runner
  body unchanged)
- `app/memory/_schema.py` + `_store.py` — schema_version 2 → 3 +
  `_migrate()` helper + new fields `enable_semantic_verifier` +
  `max_auto_repairs` + corresponding mutators
- `app/storage/run_store.py` — `semantic_verify.json` +
  `repairs.jsonl` artifacts
- `app/schemas/trace.py` — finally consumes the reserved
  `REPAIR_TRIGGERED` + `SEMANTIC_MISMATCH` enum values
- `app/cli.py` — `localflow verify-semantic`, `localflow repair`,
  `localflow execute --no-auto-repair`, memory toggles for the two
  new prefs
- `app/ui/pages/2_Execute.py` — semantic verdict panel after the
  structural badge (only renders when verifier ran)
- `app/ui/pages/4_Memory.py` — new "🔁 Semantic + Repair" tab with
  toggle + slider
- `app/ui/_i18n.py` — EN + 中文 keys for the new surfaces
- `app/eval/runner.py` — `enable_auto_repair` + `max_auto_repairs`
  kwargs; eval runner can be driven through `run_with_auto_repair`
- `app/cli.py eval run` — `--enable-repair` + `--compare-repair` +
  `--max-auto-repairs` flags; comparison mode renders a side-by-side
  markdown table
- 5 new test modules, 30 new tests (465 → 495 total)
- `docs/SEMANTIC_VERIFIER.md`

**Kernel touch**: NO. `app/harness/{executor,verifier,rollback}.py`
remain byte-identical. `control_loop.py` gains one composite +
`taskgraph_runner.py` gains one branch — both additive. `trace.py`
adds one helper (`emit_repair_triggered`). **22nd** zero-kernel-touch
phase.

**§10.7 invariant**: `git diff app/harness/executor.py
app/harness/verifier.py app/harness/rollback.py` = empty.

---

## Phase 11 — Plan Refinement Loop + Data-Aware Routing (v0.12.0)

**Trigger**: a real-world v0.11 UI run exposed two coupled failures —
the agent meta-skill received an Excel file with NO content preview
(only filename + hash) and so produced a meta-description of the
workspace + a file-type bar chart instead of analyzing the data. The
report literally confessed `"未能直接读取表格单元格内容"`. And the
harness had no in-loop fix-up surface — the only recovery options
were "rollback + retype goal" or "live with the wrong plan".

**Goal**: turn the bug into a demonstration of the harness's
correction capability. Two tracks:

**Track A — Data-aware routing + Excel preview + pie/line**:
- `app/ui/_autodetect.py`: when the goal mentions analysis verbs
  (分析/解读/统计/analyze/interpret/aggregate/...) AND the workspace
  contains a tabular file, route to `data_analyzer` instead of the
  agent meta-skill. `data_analyzer` reads cells via pandas.
- `app/tools/data_ops.py` adds `extract_tabular_preview()` —
  `file_scan` now extracts the first ~10 rows of every .xlsx/.csv
  as a markdown table into `FileMeta.text_preview`. The LLM sees
  real data when reasoning about a spreadsheet.
- `app/tools/chart_ops.py` gains `pie_png` + `line_png`. The
  `ChartRequest.kind` literal extends to include `pie`.
  `data_analyzer/planner.py` picks `pie` for ≤6-category groupby
  results and `line` for datetime + numeric pairs.

**Track B — Plan refinement loop**:
- `Skill.revise(task, snapshot, prior_plan, hint)` default in
  `app/skills/_base.py` — delegates to `plan_with_llm` with
  `prior_plan_actions` + `user_hint` threaded through. The agent
  planner synthesizes a "your previous plan was X; the user said Y;
  please re-plan" user message before the first LLM turn — reuses the
  same single-call codepath as a fresh plan, no new state machine.
- `RunStore` gains `plans/plan_v<n>.json` versioning + `revisions.jsonl`
  audit log. `plan.json` always mirrors the latest version so
  executor / verifier / rollback are oblivious.
- `control_loop.run_revise()` is the orchestration entry point —
  caps at `MAX_REVISIONS = 5`, validates the revised plan, emits one
  `TraceEventType.PLAN_REVISED` event.
- `localflow revise --task-id <id> --hint "..."` CLI surface.
- UI: Plan page renders a refine expander below the plan summary
  with EN + 中文 i18n. Click → text-area → `重新规划` button →
  Streamlit re-render with the new plan. Iterate up to 5 times.

**Files**: NEW `app/harness/control_loop.run_revise` + 5 test modules
+ `docs/REFINE.md`. EXTENDED `app/agent/{planner,analysis_planner}.py`,
`app/skills/_base.py`, `app/skills/data_analyzer/planner.py`,
`app/storage/run_store.py`, `app/schemas/{trace,analysis}.py`,
`app/tools/{chart_ops,data_ops,file_scan}.py`, `app/ui/_autodetect.py`,
`app/ui/pages/1_Plan.py`, `app/ui/_i18n.py`, `app/cli.py`.

**Tests**: 35 new (430 → 465).

**Kernel touch**: NO. `control_loop.run_revise` is a new function
that sits next to existing `run_*` orchestrators; it calls into
existing kernel modules but does not mutate their behaviour. The
executor / verifier / rollback never see "plan versions" because they
read `plan.json` which mirrors the latest. **21st** zero-kernel-touch
phase.

---

## Phase 10 — TaskGraph / Multi-Stage Execution (v0.11.0)

**Goal**: give compound goals a deterministic alternative to the v0.9
`agent` meta-skill. The user's original "整理然后画图" goal can now be
solved two ways:

- **agent** (v0.9): one LLM call, one ActionPlan — flexible but
  every step is at the model's mercy.
- **TaskGraph** (v0.11): a static YAML graph of skill invocations —
  zero LLM cost, byte-deterministic, per-stage failure policy.

Both paths coexist; users pick the right tool. The eval suite from
v0.10 will quantify which one wins on which workload.

**Shipped**:

- **TaskGraph schemas** ([app/schemas/taskgraph.py](localflow/app/schemas/taskgraph.py))
  — `StageSpec` / `TaskGraph` / `StageResult` / `TaskGraphResult` +
  `StageFailurePolicy` (abort / continue / skip) +
  `StageStatus` (passed / failed / skipped / aborted). Phase 12's
  REPAIR policy + `max_retries` semantics are reserved (schema field
  exists, runner ignores them for now).
- **RunStore extension** ([app/storage/run_store.py](localflow/app/storage/run_store.py))
  — `stage_dir(stage_id)` + `stages_root` + `taskgraph_path` /
  `taskgraph_result_path`. One-line addition per property, no
  rewrites of existing path helpers.
- **TraceLogger.stage()** ([app/harness/trace.py](localflow/app/harness/trace.py))
  — `with trace.stage("s1_organize"):` decorates every event emitted
  inside the block with `stage_id="s1_organize"`. Implemented via
  `contextvars.ContextVar` — single-threaded today, future-proof for
  parallel stages. Existing emission sites unchanged.
- **TaskGraphRunner** ([app/harness/taskgraph_runner.py](localflow/app/harness/taskgraph_runner.py))
  — walks stages sequentially through the standard
  `control_loop.run_*` pipeline. Per-stage `StageRunStore` (a thin
  RunStore subclass) redirects each stage's artifacts under
  `<run_dir>/stages/<stage_id>/`. One graph-level `trace.jsonl` +
  one aggregated `rollback_manifest.json` at the top.
  action_ids are stage-prefixed (`s1.a-001` etc.) to keep them
  unique across the merged manifest.
- **EvalTask.stages** ([app/eval/schema.py](localflow/app/eval/schema.py))
  — optional field. When set, `run_eval()` dispatches to the
  TaskGraph path; when absent, single-skill behaviour from v0.10
  unchanged. The multi-stage runner synthesises an aggregated
  ActionPlan from every stage's plan.json so existing graders
  (which read `ctx.plan.actions`) see the union of all stages'
  actions.
- **CLI** ([app/cli.py](localflow/app/cli.py)) —
  `localflow taskgraph describe <yaml>` (preview) +
  `localflow taskgraph run <yaml> [--workspace ...] [--yes]`
  (single approval ceremony on the graph spec; per-stage plans
  generated just-in-time; rollback via existing
  `localflow rollback --run-id <id>`).
- **`task_007_organize_then_chart`** ([evals/workspace_pack/](localflow/evals/workspace_pack/task_007_organize_then_chart.yaml))
  — first multi-stage starter task. Stage 1: folder_organizer.
  Stage 2: workspace_visualizer. The v0.9-original compound goal,
  solved deterministically. Eval suite now 7/7.

**Files**: 1 new schema, 1 new runner, 1 new CLI subcommand group,
1 new starter task YAML, 1 new doc (`docs/TASKGRAPH.md`),
4 new test files (`test_taskgraph_schema.py` +
`test_taskgraph_runner.py` + `test_eval_multi_stage.py` +
extensions to `test_trace_schema.py` + `test_cli_trace.py`).
`pyproject.toml` 0.10.1 → 0.11.0.

**Tests**: 402 → 430 (+28: 11 schema + 9 runner + 3 multi-stage eval
+ 3 trace stage-ctx + 2 CLI taskgraph). Lint + format clean.

**§10.7**: NO kernel-BEHAVIOUR changes. The TaskGraphRunner is a new
file SIBLING to control_loop.py — it composes the existing
`control_loop.run_*` functions, does not modify them. `TraceLogger`
gains the `stage()` context manager (additive — events without a
contextual stage still emit `stage_id=None` exactly as before). All
6 v0.10.1 eval tasks (no `stages` field) continue to pass via the
single-skill path unchanged. **20th** consecutive zero-kernel-behaviour
phase.

**Behaviour change visible to users**: `<run_dir>/` now optionally
contains a `stages/` subdirectory + `taskgraph.json` +
`taskgraph_result.json` when the run was driven by a TaskGraph.
Existing single-skill runs are byte-identical to v0.10.1.

**Worked example** — the user's v0.9 complaint goal handled both
ways:

```powershell
# Path 1 — LLM (existing v0.9):
localflow plan ./workspace --goal "organize then chart" --skill agent --planner llm

# Path 2 — static composition (new v0.11):
localflow taskgraph run my_graph.yaml --yes
#   where my_graph.yaml = folder_organizer → workspace_visualizer
```

Both produce a valid run_dir + trace + rollback manifest. The user
picks based on whether the goal is novel (agent) or repeatable
(TaskGraph).

---

## Phase 9.1 — Trace coverage for CLI + MCP, expanded eval suite (v0.10.1)

**Goal**: close the two gaps the v0.10.0 verification guide called out
honestly — regular CLI commands and MCP tools didn't emit trace
(only `localflow eval run` did) — and grow the starter eval suite
from 3 → 6 tasks so the failure-mode coverage isn't a token sample.

**Shipped**:

- **CLI trace wiring** ([app/cli.py](localflow/app/cli.py)) — `plan`,
  `dry-run`, `execute`, `rollback` commands construct a TraceLogger
  per run and thread it through every `control_loop.run_*` call +
  the Executor / Rollback constructors + the LLM streaming planner
  path. Effect: every `localflow plan/execute` now produces a
  trace.jsonl alongside the existing artifacts.
- **MCP trace wiring** ([app/mcp/tools.py](localflow/app/mcp/tools.py))
  — `create_plan`, `dry_run`, `execute_plan`, `rollback_run` handlers
  construct a TraceLogger per call. MCP clients get the same
  observability surface CLI users get.
- **3 new eval tasks** ([evals/workspace_pack/](localflow/evals/workspace_pack/)):
  - `task_004_forbidden_action_blocked` — regression pin that
    folder_organizer never emits delete/overwrite/shell actions
    even when the plan asks for them implicitly
  - `task_005_empty_workspace` — edge case: zero-action plan walks
    the full lifecycle cleanly (rollback no-op, every grader
    trivially passes)
  - `task_006_duplicate_files_reported` — two byte-identical files
    → both moved + duplicates_report.md emitted, no delete ever
    attempted (pins the "report duplicates, never delete" rule)
- **5 new tests**: 3 task-coverage tests + 2 CLI trace tests
  (`test_cli_plan_emits_trace_jsonl` /
  `test_cli_execute_emits_action_and_verifier_events`).

**Files**: `app/cli.py`, `app/mcp/tools.py`,
3 new YAMLs under `evals/workspace_pack/`,
`tests/test_cli_trace.py` (new), `tests/test_eval_runner.py` (updated
to expect 6 tasks). `pyproject.toml` 0.10.0 → 0.10.1.

**Tests**: 397 → 402. Lint + format clean.

**§10.7**: NO kernel-behaviour changes. The Phase 9 additive-only
TraceLogger-kwarg pattern is exactly the surface I extended here
— still 19th consecutive zero-kernel-behaviour phase. The
`test_executor_with_trace_none_writes_no_trace_file` invariant test
still passes (library callers that don't pass trace still see no
file).

**Behaviour change visible to users**: regular CLI runs now create a
`trace.jsonl` in `<run_dir>/`. This isn't breaking — the file is
observation-only, ignored by everything except the eval graders.
Users who want the v0.10.0 behaviour (no trace.jsonl on normal runs)
can delete the file post-execute or in CI cleanup hooks.

---

## Phase 9 — Trace + Eval Harness (v0.10.0)

**Goal**: address the user's experiment-report diagnosis that
LocalFlow had reached the Control + Safety + Persistence layers of
the 5-layer Harness model but stalled at structural Verification with
no Improvement Harness at all. The report explicitly recommended
building Trace + Eval **before** anything else — Phases 10–12
(TaskGraph, Workspace Pack Builder, Semantic Verifier + Repair Loop)
all need measurable graders to justify their existence. v0.10.0 lands
that foundation.

**Shipped**:

- **TraceEvent schema** ([app/schemas/trace.py](localflow/app/schemas/trace.py))
  — closed enum of 13 kernel event types + 14 failure types. The
  `FailureType` enum is pinned including the Phase 12 placeholder
  values (`semantic_mismatch`, `summary_not_grounded`, etc.) so the
  histogram code doesn't need a schema bump when Phase 12 starts
  emitting them.
- **TraceLogger** ([app/harness/trace.py](localflow/app/harness/trace.py))
  — sister to AuditLogger; writes `trace.jsonl` via the same atomic
  `JsonlLogger` primitive; reads back into typed `TraceEvent`
  objects; groups by failure_type for histograms.
- **Emission wired at 7 sites** (additive only — kernel behaviour
  unchanged when `trace=None`):
  - `app/agent/planner.py` — LLM_CALL_START / END + LLM_REPAIR + token usage
  - `app/harness/control_loop.py` — POLICY_CHECK + DRY_RUN_RENDERED
  - `app/harness/executor.py` — ACTION_START / END + per-action duration
  - `app/harness/verifier.py` — VERIFIER_CHECK per check with failure_type
  - `app/harness/rollback.py` — ROLLBACK_ENTRY per replayed op, drift surfaced
  - `app/mcp/approval.py` — TOKEN_MINTED / CONSUMED / REJECTED
- **Eval package** ([app/eval/](localflow/app/eval/)):
  - `schema.py` — `EvalTask`, `GraderContext`, `EvalResult`,
    `GraderVerdict`, `WorkspaceFile`
  - `runner.py` — runs one task end-to-end in an isolated workspace
    + isolated RunStore; runs every grader; runs rollback if
    `rollback_restores` grader is present; aggregates failure
    histogram from trace
  - `report.py` — markdown report with batch summary + failure
    histogram + per-task verdicts
  - `graders/structural.py` — 4 starter graders:
    `safety_no_forbidden_path`, `expected_outputs_present`,
    `all_files_accounted_for`, `rollback_restores`
  - Semantic graders deferred to Phase 12 (need LLM-as-judge).
- **CLI**: `localflow eval list <target>` + `localflow eval run
  <target>` — exit code = failed task count.
- **3 starter eval tasks** ([evals/workspace_pack/](localflow/evals/workspace_pack/)):
  - `task_001_basic_organize` — folder_organizer + rule, exercises
    Safety + Operational correctness end-to-end
  - `task_002_compound_chart` — agent skill rule fallback (LLM-free
    so CI stays deterministic)
  - `task_003_forbidden_path_blocked` — pins the policy_guard's
    forbidden_paths behaviour, including `must_pass` filtering so
    the task passes even when one grader sensibly reports a missing
    target (the target's move was correctly blocked).
- **docs/EVAL.md** — task YAML format, grader API, trace schema
  walkthrough, failure taxonomy table, custom-grader cookbook.
- **Hygiene**: clarifying comments in `app/agent/prompts.py` +
  `app/skills/agent/llm_planner.py` explaining why ruff format
  leaves their long lines alone (literal LLM prose, not a
  formatting bug). The report's §13.1 concern about "raw GitHub
  unreadable" was overstated — these lines are intentional, but
  the comments make that obvious to future reviewers.

**Files**: 6 new files in `app/eval/`, `app/schemas/trace.py`,
`app/harness/trace.py`, `evals/workspace_pack/` (3 YAMLs),
`docs/EVAL.md`, plus 4 new test files. Plus additive trace-emission
in 6 existing files. `pyproject.toml` 0.9.1 → 0.10.0.

**Tests**: 368 → 397 (+29: 8 schema + 6 emission + 10 graders + 5
runner). Lint + format clean.

**Live end-to-end check** (3 starter tasks against a fresh sandbox):
all 3 pass with the expected grader verdicts, the report markdown
populates with the path_forbidden histogram from task_003, and every
run's `trace.jsonl` is well-formed.

**§10.7**: zero kernel **behaviour** changes. Every trace-emission
hook accepts an optional `TraceLogger` kwarg defaulting to `None`,
and the kernel produces byte-identical artifacts whether trace is
attached or not (pinned by `test_executor_with_trace_none_writes_no_trace_file`).
The kernel kwarg additions are observation surfaces, not behaviour
surfaces — this is the 18th consecutive zero-kernel-behaviour phase.
(Phase 5 remains the lone behaviour-changing exception.)

**What v0.10.0 does NOT claim**: agent semantic correctness is
unchanged from v0.9.1. This phase builds the **instrumentation** to
measure it. After this release we have:

- A way to say "task_002 passed 4/4 graders, took 78 ms, 0 trace failures"
- A trace stream showing which kernel layer caught each LLM mistake
- A failure-type histogram across the eval suite
- A grader registry external code can extend

This is the foundation Phases 10–12 will measure their work against.
Without it, every later "improvement" would be unfalsifiable.

---

## Phase 8.3.1 — project hygiene + opt-in external skills (v0.9.1)

**Goal**: address review feedback on the v0.9.0 ship — stale doc
sections, eagerly-loaded external skills (security risk), README
still CLI-only after the UI became the primary surface, and the
long-deferred dependency split that the v0.9.0 comment claimed was
out of scope.

**Shipped**:

- **README rewrite** — version refs synced (`Tests: 357 → 359`,
  Architecture box updated to mention `agent` + `workspace_visualizer`,
  `mcp/` description bumped from 15 → 18 tools, sample wheel filename
  `0.6.3 → 0.9.0`, layout's `tests/` line `259 → 359`). Quickstart
  split into two sections: **WebUI Quickstart** (recommended for
  demos, uses `localflow ui-serve`) and **CLI Quickstart** (developer
  path, shows every stage including `dry-run` and `verify`
  explicitly).
- **UI.md cleanup** — removed the v0.8.x "Auto-detect heuristic"
  paragraph that still claimed five-way skill routing, removed the
  Override-panel troubleshooting rows. Replaced with a clean "Single
  skill, no override" section that matches the v0.9.0 code reality.
- **External skill opt-in** ([app/skills/_loader.py](localflow/app/skills/_loader.py)) —
  v0.7.x shipped external skill auto-loading with a stderr warning;
  in practice users ignored the warning. v0.9.1 flips the default:
  `LOCALFLOW_ENABLE_EXTERNAL_SKILLS=1` is now required to load
  anything from `~/.localflow/skills/`. The legacy
  `LOCALFLOW_DISABLE_EXTERNAL_SKILLS=1` kill switch still wins when
  both are set (back-compat with CI scripts). Audit table shows
  "skipped: external skills opt-in required" for missed loads.
- **Dependency split** — pandas / openpyxl / matplotlib / pypdf moved
  out of the base install into `[data]` and `[pdf]` extras
  ([pyproject.toml](localflow/pyproject.toml)). Survey confirmed every
  heavy import in v0.9.0 is already inside a function body, so the
  SkillRegistry registers all skills with zero heavy imports — the
  comment in pyproject claiming "eagerly imports at module-load time"
  was stale by Phase 6.1. chart_ops now raises a friendly
  ImportError pointing at `[data]` when matplotlib is missing.
- **Agent meta-skill integration tests** ([tests/test_agent_integration.py](localflow/tests/test_agent_integration.py)) —
  7 new tests covering the harness's defensive layers against the
  agent's expanded action surface: compound plan executes + verifies
  + rolls back cleanly; path-traversal in target_path blocked by
  policy_guard; duplicate action_ids rejected by plan validator;
  forbidden_paths from memory blocked at risk-check; chart_request
  with zero-value still renders; validator catches a PNG action that
  skipped post-processing; rollback removes both generated chart PNGs
  and index.md files.

**Files modified**: `README.md`, `docs/UI.md`, `docs/SECURITY.md`,
`docs/PHASES.md`, `app/skills/_loader.py`, `app/tools/chart_ops.py`,
`pyproject.toml`, `tests/test_skill_loader.py`. New:
`tests/test_agent_integration.py`.

**Tests**: 359 → 368 (+9: 7 integration tests in
`test_agent_integration.py`; +2 in `test_skill_loader.py` for the
opt-in default and the DISABLE-wins-over-ENABLE precedence).
Lint + format clean.

**§10.7**: NO kernel touches. Doc + loader + dependency refactor only.
**17th** zero-kernel-touch phase.

---

## Phase 8.3 — agent meta-skill (v0.9.0)

**Goal**: close the v0.8.2 design gap where compound goals like
"organize then chart then summarize" still routed to a single
specialist skill that could only do one third of the task. Real-user
feedback: the multi-skill picker + override panel was rated 蠢 ("dumb")
and the user wanted "agent 自行决定 + harness 纠错保证质量".

**Shipped**:

- **New `agent` meta-skill** ([app/skills/agent/](localflow/app/skills/agent/)) —
  the v0.9.0 default. LLM-driven. Allowed actions: every category
  (mkdir / move / rename / copy / index). Required tool:
  `chart_ops.bar_png`. Produces a SINGLE ActionPlan covering every
  step of a compound goal in one cycle.
- **Custom system prompt** ([app/skills/agent/llm_planner.py](localflow/app/skills/agent/llm_planner.py)) —
  extends the folder-organizer prompt with a `chart_request`
  convention: the LLM emits INDEX actions with target_path ending in
  `.png` and metadata.chart_request = `{kind, title, xlabel, counts}`.
  Python renders the PNG via `chart_ops.bar_png` after the LLM call.
  LLM never produces base64 bytes itself, keeping plan.json small +
  human-readable.
- **`render_chart_actions` post-processor**: walks the LLM's plan,
  renders every chart_request to PNG bytes, substitutes
  `binary_content_b64`. Malformed specs degrade to markdown error
  placeholders instead of crashing the plan.
- **Refactor `app/agent/planner.py`**: `plan_with_llm` now accepts a
  `system_prompt` kwarg (default = legacy folder_organizer prompt for
  back-compat). The agent skill passes its own. **Pluggable prompts
  are the only way a new skill can use the existing LLM repair-loop
  infrastructure without forking it.**
- **UI simplification** ([app/ui/_autodetect.py](localflow/app/ui/_autodetect.py)
  + [app/ui/pages/1_Plan.py](localflow/app/ui/pages/1_Plan.py)) —
  auto-detect always returns `agent` + `llm` (or `rule` for empty
  goal). Override expander removed. Capability-gap warning removed
  (no gaps when one skill handles everything). The Plan page is
  now: goal text area + one-line "agent will plan end-to-end" +
  Create plan button.

**Files** (new): `app/skills/agent/` (6 files), `tests/test_agent_skill.py`.
**Files** (modified): `app/agent/planner.py`, `app/skills/__init__.py`,
`app/ui/_autodetect.py`, `app/ui/pages/1_Plan.py`, `app/ui/_i18n.py`,
`tests/test_ui_autodetect.py`, `pyproject.toml` (0.8.2 → 0.9.0).

**Tests**: 359 → 357 net (the v0.8.2 autodetect tests were rewritten
from 27 to 19 to match the new always-agent contract — minus 8;
`test_agent_skill.py` adds 15 — plus 15; minus 9 from removing the
v0.8.2 compound + capability-gap cases that the agent now handles
internally. Lint + format clean.

**§10.7**: NO kernel touches. The `system_prompt` parameter is added
to the agent planner (which lives in `app/agent/`, NOT
`app/harness/`). The harness still sees a generic ActionPlan it knows
how to dry-run + execute + verify + rollback. **16th** zero-kernel-touch
phase.

**Specialist skills stay in the registry**: folder_organizer /
pdf_indexer / data_reporter / data_analyzer / workspace_visualizer
remain available via CLI (`--skill <name>`) and MCP (`create_plan`).
Only the UI defaults to agent — power users keep their precision tools.

**Worked example** — the user's compound goal:

> 将文件按种类整理，然后在各自文件夹下总结文件的信息，最后把各文件夹的文件数以柱状图绘制然后放在图象文件夹下

v0.8.2 routed to `data_reporter + rule` and produced a markdown file
pretending to be a chart. v0.9.0 routes to `agent + llm`; the LLM
decomposes the goal into mkdir + move (organize part) + per-folder
index.md (summarize part) + INDEX action with chart_request (chart
part). The Python post-processor renders the real PNG. ONE plan,
ONE approval, ONE execute.

---

## Phase 8.2 — workspace_visualizer + smart planner upgrades (v0.8.2)

**Goal**: close the v0.8.1 gap where user testing exposed three real
limits — auto-detect routing compound goals to the wrong skill, no
skill drawing real PNG charts of file metadata, and no way to make
LLM the default planner without bypassing the heuristic.

**Shipped**:

- **New skill** `workspace_visualizer` ([app/skills/workspace_visualizer/](localflow/app/skills/workspace_visualizer/)):
  counts files by parent folder (when ≥60% of files live in subdirs)
  or by file_type otherwise, then renders a real PNG bar chart via
  `chart_ops.bar_png` + base64-encoded INDEX action. Mirrors the
  binary-write mechanism `data_analyzer` / `data_reporter` already
  use for column charts. Rule-only — counts and matplotlib have
  nothing for an LLM to add.
- **Compound-goal detection** in [app/ui/_autodetect.py](localflow/app/ui/_autodetect.py):
  goals with 然后/再/最后/then/finally/etc. (or three+ distinct
  action verbs) auto-upgrade the planner to LLM. Rule planners can
  only emit one action category — multi-step goals silently lose
  the rest unless an LLM synthesizes the plan.
- **Capability-gap warning** on the Plan page: when the auto-detected
  skill can't satisfy every part of the goal (e.g. user asks for
  organize+chart and only folder_organizer was picked), surface a
  yellow warning with a suggested second-step skill. The warning
  doesn't block — the user can still run the plan and pick up the
  missing part as a second task.
- **`prefer_llm_planner` memory pref** ([app/memory/_schema.py](localflow/app/memory/_schema.py)):
  new boolean field, defaults to False. When ON, every LLM-capable
  skill uses LLM regardless of goal text. New CLI command
  (`localflow memory set prefer_llm_planner true|false`), new MCP
  tools (`memory_set_prefer_llm_planner`,
  `memory_unset_prefer_llm_planner`), new Memory-page tab
  ("🤖 Planner preference"). Schema bumped 1 → 2.
- **Smarter routing heuristic**: a goal with `organize` keyword in a
  mixed workspace (more non-tabular than tabular files) now routes
  to `folder_organizer` even with a stray xlsx — the user is
  organizing their workspace, not analyzing the spreadsheet.

**Files**: 5 new files in `app/skills/workspace_visualizer/`,
extended `app/ui/_autodetect.py` + `_i18n.py`, modified
`app/memory/_schema.py` + `_store.py`, `app/cli.py`, `app/mcp/tools.py`,
`app/ui/pages/1_Plan.py` + `4_Memory.py` + `_layout.py`,
`app/skills/__init__.py`.

**Tests added**: 40 (`test_workspace_visualizer.py` 14 +
`test_ui_autodetect.py` extensions 17 + `test_memory_store.py`
extensions 6 + `test_cli_memory.py` extensions 5 + `test_mcp_tools.py`
update 2). Total 319 → 359.

**Kernel touch**: NO. The schema_version bump is memory-side, not
kernel-side. `app/harness/*`, `app/schemas/*`, `app/tools/*`
untouched. **15th** zero-kernel-touch phase.

**The full bilingual decision tree** the user wrote that exposed this:

> 将文件按种类整理，然后在各自文件夹下总结文件的信息，最后把各文件夹的文件数以柱状图绘制然后放在图象文件夹下

Before v0.8.2: routed to `data_reporter + rule` because of one
incidental xlsx + "总结" keyword → output was a markdown mermaid
block, not a real chart. After v0.8.2: routes to `folder_organizer +
llm` (organize keyword in mixed workspace + compound 然后 marker),
with a yellow gap warning nudging the user to run
`workspace_visualizer` as a second task for the real PNG chart.

---

## Phase 8.1.1 — Sticky unsafe mode (v0.8.1)

**Bug**: in v0.8.0, after the user opted into unsafe path mode with
``?unsafe=1`` and picked a custom workspace outside `./sandbox/`, the
first page navigation (e.g. clicking "Plan" in the sidebar) silently
reverted the workspace to a sandbox subdir. Custom path was no longer
visible in the Source radio either.

**Root cause**: Streamlit's multi-page navigation **strips URL query
parameters**. ``render_unsafe_banner`` and ``render_sandbox_sidebar``
both read ``?unsafe=1`` fresh from ``st.query_params`` on every
render, so a page change → unsafe=False → Custom-path radio hidden →
sandbox dropdown picker runs → ``SESSION_WORKSPACE_KEY`` overwritten.

**Fix**: new ``_resolve_unsafe()`` helper in ``app/ui/_layout.py``
latches ``unsafe=True`` to ``st.session_state["unsafe_mode_enabled"]``
on first detection. Once a session opts in, the bit sticks through
every page render until the tab is closed. A fresh tab without
``?unsafe=1`` still starts in safe mode — the latch is per-session,
not global.

**Test**: ``test_resolve_unsafe_latches_to_session_state`` in
``tests/test_ui_sandbox.py`` mocks ``st.query_params`` +
``st.session_state`` and verifies all three cases (first-visit latch,
post-nav persistence, fresh-session reset). Total 318 → 319.

**Kernel touch**: NO.

---

## Outline §10.7 final ledger

| Phase | Kernel-touch | Notes |
|-------|--------------|-------|
| 0 | — | the kernel itself |
| 1 | NO | agent layer above |
| 2.1 + 2.2 | NO | new modules under `app/tools/` |
| 2.3 | NO | Skill ABC + registry under `app/skills/` |
| 3.1–3.3 | NO | `_record_implicit_parents` is internal to existing executor path, no API change |
| 4.1 | NO | filesystem skill loader |
| 4.2 | NO | Tool Registry |
| 4.3 | NO | contract test template |
| **5** | **YES (~25 lines)** | `forbidden_paths` — universal safety primitive, kernel-only by design |
| 6.1 | NO | new package `app/mcp/`, 1 CLI command |
| 7.1 (v0.6.3) | NO | doc/format/UX hardening + rollback hash-guard in `app/harness/rollback.py` — extends existing class only, no new kernel primitives |
| **8.0 (v0.7.0)** | NO | new package `app/ui/`, 1 CLI command (`ui-serve`) |
| 8.0.1–8.0.4 (v0.7.1–v0.7.4) | NO | UI bug-fix sequence |
| **8.1 (v0.8.0)** | NO | UI UX overhaul: i18n + autodetect + sidebar rewrite |
| 8.1.1 (v0.8.1) | NO | Sticky unsafe mode (Streamlit page-nav drops query params) |
| **8.2 (v0.8.2)** | NO | workspace_visualizer skill + compound-goal detection + capability-gap warning + prefer_llm_planner memory pref |
| **8.3 (v0.9.0)** | NO | agent meta-skill (LLM-driven, one-shot compound execution) + pluggable system prompts + UI collapse to single skill |
| 8.3.1 (v0.9.1) | NO | Project hygiene — README split, UI.md cleanup, external skill opt-in default, [data]/[pdf] dep extras, agent integration tests |
| **9 (v0.10.0)** | NO (additive only) | Trace + Eval Harness: TraceEvent schema + TraceLogger + emission at 7 kernel sites + `app/eval/` package with 4 structural graders + `localflow eval run/list` CLI + 3 starter eval tasks + docs/EVAL.md |
| 9.1 (v0.10.1) | NO | CLI + MCP commands now wire trace; starter eval suite grew 3 → 6 tasks |
| **10 (v0.11.0)** | NO (additive only) | TaskGraph — multi-stage execution: schemas + runner + `TraceLogger.stage()` ctx + `localflow taskgraph` CLI + `EvalTask.stages` + 1 starter multi-stage eval task |
| **11 (v0.12.0)** | NO (additive only) | Plan Refinement Loop + Data-Aware Routing — `Skill.revise` default + `control_loop.run_revise` + `RunStore` plan versioning + `TraceEventType.PLAN_REVISED` + `localflow revise` CLI + UI refine expander; Excel preview in scanner; pie + line chart kinds; `autodetect_skill` routes analysis goals to `data_analyzer` |
| **13 (v0.13.0)** | NO (additive only) | Semantic Verifier + Auto-Repair Loop — `SemanticVerifier` (new module next to existing structural Verifier) + 3 LLM-as-judge graders (`output_addresses_goal` / `summary_grounded` / `analysis_result_nonempty`) + `run_repair_loop` + `control_loop.run_with_auto_repair` composite + `localflow verify-semantic` / `repair` CLI + `--no-auto-repair` flag + UI Execute-page verdict panel + Memory toggles + `StageFailurePolicy.REPAIR` wired up (uses Phase 10's reserved `max_retries`) + eval `--compare-repair` mode; emits `REPAIR_TRIGGERED` + `SEMANTIC_MISMATCH` (both reserved since Phase 9) |
| **14 (v0.14.0)** | NO | Workspace Pack Builder strong demo — 5-stage `examples/research_pack/workspace_pack.yaml` composing folder_organizer + pdf_indexer + data_analyzer + workspace_visualizer + agent; `examples/research_pack/seed.py` plants the messy seed; 1 new structural grader (`every_input_accounted_for`); 1 new eval task (`task_010_workspace_pack`); 8 new tests (495 → 503); `docs/PACK_BUILDER.md` walkthrough. No new harness primitives — pure composition demo proving v0.10-v0.13 substrate stacks. |
| 14.1 (v0.14.1) | NO | Workspace Pack polish — typed `SourceLedger` + `SourceEntry` schema + `localflow ledger` CLI; folder_organizer's `route_low_confidence_to_review` task pref routes `other`-classified files to `review/` with `review/unresolved_files.md`; new `topic_clusterer` skill (LLM-driven semantic topic grouping into `topics/<topic>/` dirs — distinct from folder_organizer's extension-based categories). +15 tests (503 → 518). |

**Score**: 1 deliberate exception across 24 deliveries. The rule held.

---

## Deferred (groundwork laid)

- **Phase 5.x** — remaining 3 memory categories from outline §14:
  directory structure pref / report template / common task recipes.
  Schema already has `schema_version` for migration; pattern is
  proven; adding each is a new field + one consumer site.
- **Phase 6.2** — MCP **client** (reverse direction): LocalFlow calls
  external MCP servers (community filesystem, fetch, search, ...) and
  registers their tools into Phase 4.2 Tool Registry. Builds on the
  `mcp>=1.6` SDK Phase 6.1 already pulled in.
- **Phase 6.3** — WebCollect skill (HTTPS GET → workspace markdown).
  Needs new `ActionType.FETCH` (second deliberate kernel touch),
  domain allow-list in memory, robots.txt check, content-type
  handling.
- **Phase 6.x** — browser only-read (browser-use-style), external
  service connector.

Each phase's full design rationale, real-data validation notes, and
lessons-learned were captured during development. The most important
ones are inlined above; the master design doc is
[localflow_agent_harness_outline.md](../localflow_agent_harness_outline.md).
