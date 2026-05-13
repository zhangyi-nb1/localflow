# LocalFlow — Demo Walkthrough

This page is a literal trace of running LocalFlow against the bundled
demo workspace at `examples/messy_downloads/`. Every artifact below is
the **actual output** of a real run (task ID `2026-05-13-001` on
that workspace). Re-running locally reproduces the same shape:

```powershell
cp -r examples/messy_downloads demo_sandbox
localflow plan ./demo_sandbox --goal "organize files by category" --planner rule
localflow dry-run --task-id <id>
localflow execute --task-id <id> --yes
localflow rollback --run-id <id> --yes
```

The point of this doc is to **show** what dry-run looks like, what
goes into a plan, what the verifier produces, and that rollback truly
restores state. Everything else (PHASES.md, ARCHITECTURE.md,
SECURITY.md) is design rationale — this is the proof.

---

## 1. Initial workspace — `demo_sandbox/`

20 files of 10 categories, intentionally messy (top-level + one
subdir):

```
demo_sandbox/
├── agent_memory_survey.pdf
├── app.js
├── backup.zip
├── beach.jpg
├── budget.xlsx
├── clip.mp4
├── config.json
├── data.yaml
├── diagram.png
├── duplicate_paper.pdf
├── lecture_notes.docx
├── main.py
├── readme.txt
├── song.mp3
├── subdir
│   ├── another.pdf
│   └── deep_note.md
├── telemetry.csv
├── todo.md
└── transformer_paper.pdf
```

---

## 2. User goal

```text
organize files by category
```

That's it. No file paths. No "put PDFs in papers/". The skill's
classifier + workspace scan + planner does the rest.

---

## 3. `localflow plan` — produce the structured ActionPlan

```text
$ localflow plan ./demo_sandbox --goal "organize files by category" --planner rule

┌────────────────────────────── LocalFlow ───────────────────────────────┐
│ Task created: 2026-05-13-001                                           │
│ Planner: rule  ·  Plan: plan-593ef6af  ·  Actions: 40  ·  Risk: medium │
│ Files scanned: 19  ·  Goal: organize files by category                 │
└────────────────────────────────────────────────────────────────────────┘

Next: localflow dry-run --task-id 2026-05-13-001
```

The planner emitted **40 actions** for 19 files: 10 `mkdir` (category
dirs), 19 `move` (files into their category dir), 11 `index`
(per-category `index.md` + a top-level `duplicates_report.md`). Risk
came back `medium` because the move operations are categorized as
medium-risk — they're reversible but they relocate user files.

### Representative actions from `plan.json`

A `mkdir` action — low risk, requires approval, reversible:

```json
{
  "action_id": "a-001",
  "action_type": "mkdir",
  "source_path": null,
  "target_path": "archives",
  "reason": "Create category directory for archives",
  "risk_level": "low",
  "reversible": true,
  "requires_approval": true
}
```

A `move` action — medium risk because it relocates user data:

```json
{
  "action_id": "a-019",
  "action_type": "move",
  "source_path": "beach.jpg",
  "target_path": "images/beach.jpg",
  "reason": "Categorize image file into images/",
  "risk_level": "medium",
  "reversible": true,
  "requires_approval": true
}
```

An `index` action — generates the per-category `index.md`:

```json
{
  "action_id": "a-031",
  "action_type": "index",
  "source_path": null,
  "target_path": "audio/index.md",
  "reason": "Generate index for audio/",
  "risk_level": "low",
  "reversible": true,
  "requires_approval": false
}
```

Every action is a **Pydantic-validated struct**, never a string of
shell. The Executor can only act on these shapes.

---

## 4. `localflow dry-run` — preview without touching anything

```text
$ localflow dry-run --task-id 2026-05-13-001

                     Dry-run preview — plan plan-593ef6af

 • Task: 2026-05-13-001
 • Workspace: C:\...\demo_sandbox
 • Risk: medium (ok)
 • Summary: Categorize 19 file(s) into 10 directory(ies); generate 11
   index/report file(s).

Actions

 #   Type   Source          Target              Risk      Approve?   Reason
 ─────────────────────────────────────────────────────────────────────────────
 1   mkdir  ``              archives            low       yes        Create category…
 2   mkdir  ``              audio               low       yes        Create category…
 …
 19  move   beach.jpg       images/beach.jpg    medium    yes        Categorize image…
 20  move   subdir/an…      papers/another.pdf  medium    yes        Categorize pdf…
 …
 31  index  ``              audio/index.md      low       no         Generate index…
 …
```

**This step writes nothing to the workspace** — it renders the table
to the terminal AND writes `.localflow/runs/<id>/dry_run.md` so the
user (or downstream MCP client) has a hash-able artifact. Zero
filesystem mutations under `demo_sandbox/` yet.

---

## 5. `localflow execute --yes` — commit the plan

```text
$ localflow execute --task-id 2026-05-13-001 --yes

OK  executed: 40 actions  ·  verify: passed
Report: .localflow/runs/2026-05-13-001/final_report.md
To undo: localflow rollback --run-id 2026-05-13-001
```

The Executor walks the plan action-by-action, re-runs policy_guard on
each, calls the right tool from `app/tools/file_ops.*`, and appends a
`RollbackEntry` to the manifest. The Verifier then runs independently
(`app/harness/verifier.py`) — six deterministic checks, never asks
the model "did it work?".

### Workspace after execute

```
demo_sandbox/
├── archives/
│   ├── backup.zip
│   └── index.md
├── audio/
│   ├── song.mp3
│   └── index.md
├── code/
│   ├── app.js
│   ├── main.py
│   └── index.md
├── data/
│   ├── config.json
│   ├── data.yaml
│   ├── telemetry.csv
│   └── index.md
├── documents/
│   ├── lecture_notes.docx
│   └── index.md
├── images/
│   ├── beach.jpg
│   ├── diagram.png
│   └── index.md
├── notes/
│   ├── deep_note.md
│   ├── readme.txt
│   ├── todo.md
│   └── index.md
├── papers/
│   ├── agent_memory_survey.pdf
│   ├── another.pdf
│   ├── duplicate_paper.pdf
│   ├── transformer_paper.pdf
│   └── index.md
├── spreadsheets/
│   ├── budget.xlsx
│   └── index.md
├── video/
│   ├── clip.mp4
│   └── index.md
├── subdir/         ← empty after files moved out
└── duplicates_report.md
```

Notes:
- `subdir/another.pdf` was moved up into `papers/`; `subdir/` is left
  empty (rollback knows to delete it on undo)
- A `duplicates_report.md` at the root flags `agent_memory_survey.pdf`
  and `duplicate_paper.pdf` as content-identical via SHA-256
- Every category dir has its own `index.md` listing members

### Verifier verdict

The verifier writes its own independent `verify_report.json`:

```json
{
  "task_id": "2026-05-13-001",
  "run_id":  "2026-05-13-001",
  "passed":  true,
  "checks": [
    { "name": "all_actions_accounted",     "passed": true, "detail": "ok" },
    { "name": "moves_relocated_sources",   "passed": true, "detail": "ok" },
    { "name": "rollback_covers_writes",    "passed": true, "detail": "ok" },
    { "name": "no_path_escapes",           "passed": true, "detail": "ok" },
    …
  ]
}
```

This file is auto-loaded by `read_run` MCP tool / `localflow status`.

### Final report (top of `final_report.md`)

```markdown
# Final report — task `2026-05-13-001`

- Skill: `folder_organizer`
- Workspace: `C:\...\demo_sandbox`
- Goal: organize files by category

## Execution summary

- Total actions: **40**
- Succeeded: **40**
- Failed: **0**
- Skipped (checkpoint): **0**
- Rollback entries recorded: **40**

## Verifier verdict

**PASSED** — All 6 checks passed.

| Check | Result | Detail |
|-------|--------|--------|
| all_actions_accounted | ✓ | ok |
| moves_relocated_sources | ✓ | ok |
| rollback_covers_writes | ✓ | ok |
…
```

---

## 6. `localflow rollback` — restore the workspace

```text
$ localflow rollback --run-id 2026-05-13-001 --yes

OK  undone: 40  ·  failed: 0
```

All 40 actions undone in reverse order: indexes deleted, files moved
back to their source paths, category dirs swept (only when empty),
backups restored where overwrites had happened.

### Workspace after rollback — **identical to step 1**

```
demo_sandbox/
├── agent_memory_survey.pdf
├── app.js
├── backup.zip
├── beach.jpg
├── budget.xlsx
├── clip.mp4
├── config.json
├── data.yaml
├── diagram.png
├── duplicate_paper.pdf
├── lecture_notes.docx
├── main.py
├── readme.txt
├── song.mp3
├── subdir
│   ├── another.pdf
│   └── deep_note.md
├── telemetry.csv
├── todo.md
└── transformer_paper.pdf
```

Bit-exact match with the pre-execute state — the rollback manifest
captured byte hashes + source paths for every move, so undo is
deterministic.

---

## 7. The same flow over MCP

The CLI calls `control_loop.run_*` directly. Phase 6.1 + Phase 7's
MCP server wraps the same entry points behind `stdio` JSON-RPC, with
two important differences:

1. **`execute_plan` requires an `approval_token`** — minted by a
   prior `dry_run` call, 10-minute TTL, single-use, bound to the
   exact plan + dry-run + workspace. See
   [docs/SECURITY.md](SECURITY.md#approval-tokens).
2. **`memory_unforbid_path` is hidden by default** — opt in via
   `LOCALFLOW_MCP_ALLOW_DANGEROUS=1`. See
   [docs/SECURITY.md](SECURITY.md#dangerous-tools-hidden-by-default).

Driven from Claude Code chat the flow looks like:

```text
You: "use localflow to organize ./demo_sandbox"
Claude: [tool: localflow:create_plan(workspace=…, goal=…)]
        → {task_id: "2026-05-13-001", action_count: 40, risk_level: "medium"}
Claude: [tool: localflow:dry_run(task_id="2026-05-13-001")]
        → {markdown: "…", approval_token: "Yo7…3Xz", approval_expires_at: "…"}
Claude: shows you the markdown
You: "looks good, execute"
Claude: [tool: localflow:execute_plan(task_id="2026-05-13-001",
                                       approval_token="Yo7…3Xz")]
        → {success: true, executed_count: 40, verification_passed: true}
```

Without the token Claude **cannot** execute. Phase 7 fixed this — see
[localflow_project_review_and_improvement.md](../localflow_project_review_and_improvement.md)
Issue 2 for the original threat model and rationale.

---

## What this proves

| Property | Proven by |
|---|---|
| Plans are structured, not prose | §3 (Pydantic ActionPlan with typed actions) |
| Dry-run is read-only | §4 (workspace untouched until §5) |
| Verifier is independent of the model | §5 ("verify: passed" comes from rules, not LLM) |
| Rollback is bit-exact | §6 (post-rollback tree == pre-execute tree) |
| Approval gate works across drivers | §7 (CLI `--yes` and MCP `approval_token` give equivalent control) |
