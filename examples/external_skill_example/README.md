# External Skill Example — `workspace_stats`

Phase 4.1 plugin demonstrating LocalFlow's filesystem skill discovery.

> Skill-layer reference: [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) ·
> Phase notes: [docs/PHASES.md](../../docs/PHASES.md) Phase 4.1 + 4.3.

## What it does

Scans a workspace, counts files by category (pdf, image, text, ...),
and writes a single `workspace_stats.md` table to the workspace root.

## Install (any one of three options)

### Option A — per-workspace install

From your project directory:

```powershell
mkdir .\.localflow\skills -Force
xcopy /E /I examples\external_skill_example .\.localflow\skills\workspace_stats
localflow skills
```

The skill should appear in the table with `external` in the Class column.

### Option B — user-global install

```powershell
mkdir $HOME\.localflow\skills -Force
xcopy /E /I examples\external_skill_example $HOME\.localflow\skills\workspace_stats
```

Now the skill is available in any workspace on this user account.

### Option C — environment variable (CI / power users)

```powershell
$env:LOCALFLOW_SKILLS_DIR = "C:\path\to\my_skills_dir"
localflow skills
```

You can pass multiple dirs separated by `;` (Windows) or `:` (Unix).

## Run it

```powershell
localflow plan .\sandbox --goal "Stats my workspace" --skill workspace_stats
localflow execute --task-id <tid> --yes
type .\sandbox\workspace_stats.md
localflow rollback --run-id <tid> --yes
```

## Anatomy

```
external_skill_example/
├── skill.yaml      ← optional manifest (documentation; not read at runtime)
├── skill.py        ← REQUIRED: defines a Skill subclass
└── README.md       ← this file
```

LocalFlow's discovery rule: any subdirectory under a search path that
contains a `skill.py` is tried. The `skill.py` MUST define exactly one
class inheriting from `app.skills._base.Skill`. Everything else
(planner.py / validator.py / reporter.py) is just internal organization
— inline them or split them as you see fit.

## Testing your skill (Phase 4.3)

Use [`run_skill_contract`](../../app/skills/_contract.py) to verify your
Skill goes through LocalFlow's canonical 8-stage lifecycle:

1. `manifest_valid` 2. `plan_empty_workspace` 3. `plan_happy_path`
4. `validate_accepts_own_plan` 5. `validate_rejects_garbage`
6. `execute_and_verify` 7. `rollback_restores` 8. `report_non_empty`

Minimal test (see [test_contract.py](test_contract.py) for the full
worked example):

```python
from pathlib import Path
import pytest
from app.skills import run_skill_contract
from app.storage.run_store import RunStore
from your_skill_module import YourSkill

def seed(root: Path):
    (root / "anything.txt").write_text("x")

def test_my_skill_contract(tmp_path):
    rs = RunStore.create(home=tmp_path / ".localflow")
    report = run_skill_contract(
        YourSkill(),
        workspace_seeder=seed,
        workspace_root=tmp_path / "ws",
        run_store=rs,
    )
    if not report.all_passed:
        pytest.fail("\n".join(str(s) for s in report.failed_stages()))
```

Run with `pytest`. Each stage is wrapped in try/except so a failure
early on (e.g., bad manifest) still surfaces downstream failures — you
get the whole picture in one run, not whack-a-mole.

## Declaring tool dependencies (Phase 4.2)

If your skill calls any of the shared helpers under `app/tools/` (e.g.
`data_ops.read_tabular`, `chart_ops.histogram_png`), declare them in
the manifest's `required_tools` list:

```python
required_tools=["data_ops.read_tabular", "chart_ops.histogram_png"]
```

LocalFlow validates the names against the Tool Registry at load time —
typos surface immediately as `error: requires unknown tool '...'` in
the audit table. Run `localflow tools` to see the full catalog.

## Troubleshooting

Run `localflow skills` — the "External skill load audit" table shows
every directory LocalFlow tried and why each succeeded or was skipped.

Common reasons for not appearing:

| Symptom | Fix |
|---|---|
| `path does not exist` | Create the dir first: `mkdir .\.localflow\skills -Force` |
| `no skill.py` | File must be named exactly `skill.py`, not `__init__.py` or `my_skill.py` |
| `no Skill subclass found` | Make sure your class inherits from `app.skills._base.Skill` |
| `register failed: skill already registered` | Name collision with a built-in. Change the `name` field in your `manifest` property. |
| `register failed: ... requires unknown tool 'X'` | Phase 4.2 — your manifest's `required_tools` lists a tool that isn't in the registry. Run `localflow tools` and copy the exact name. |
| `import failed: ...` | Your skill.py crashed at import time. Check the error message. |
