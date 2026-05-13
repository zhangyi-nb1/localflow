# LocalFlow UI (Streamlit) — v0.7.0

A localhost-only browser UI that wraps the same harness the CLI and
MCP server use. Built for users who'd rather click than memorize 5
CLI commands.

> The UI is a **driver**, not the kernel. Every action still passes
> through policy_guard, dry-run, approval, executor, verifier, audit,
> rollback. Nothing in `app/ui/` can perform IO that the CLI couldn't.

## Install

```powershell
pip install -e ".[ui]"
# or, if you want everything:
pip install -e ".[all]"
```

## Start

```powershell
localflow ui-serve
```

Defaults to `http://127.0.0.1:8501` (localhost only — not exposed on
the LAN). The browser opens automatically. To bind a different
port / host:

```powershell
localflow ui-serve --port 8520
localflow ui-serve --host 0.0.0.0   # NOT recommended outside dev
```

Stop with `Ctrl+C` in the terminal.

## The four pages

| Page | What it does |
|---|---|
| 🏠 **Home** | Workspace summary + sidebar quick-stats |
| 📋 **Plan** | Skill dropdown + goal textarea → structured ActionPlan + risk badge |
| 🔍 **Execute** | Dry-run preview → approval checkbox → execute → auto-verify |
| ↺ **Rollback** | Drift-aware preview → safe rollback (skip conflicts) or force |
| ⚙ **Memory** | Edit `forbidden_paths` + `naming_style`, browse audit log |

## Soft sandbox

By default the UI's workspace dropdown only lists subdirectories of
`./sandbox/` (relative to wherever you ran `localflow ui-serve` from).
This is **defense in depth** on top of the kernel's actual safety
machinery — it stops you accidentally picking `C:\Users\...\Documents`
in a half-second click.

Steps:
1. Create `./sandbox/` if it doesn't exist
2. Put your demo/test workspaces inside: e.g. `./sandbox/messy_downloads/`
3. Start the UI — sandbox subdirs show up in the dropdown

### Custom path (escape hatch)

If you need a workspace outside `./sandbox/`, append `?unsafe=1`
to the URL:

```
http://127.0.0.1:8501/?unsafe=1
```

A yellow banner appears across every page, and a "Custom path"
input becomes available in the sidebar. **The kernel's
`policy_guard.resolve_inside` + `forbidden_paths` still apply** —
this just lifts the UI-layer dropdown restriction.

## Lifecycle through the UI

The full plan → dry-run → execute → verify → rollback flow takes
about 30 seconds and roughly 6 clicks:

1. **📋 Plan page**: pick skill (default `folder_organizer`), type
   a goal, hit **Create plan**. See the action table + risk badge.
2. **🔍 Execute page**: pre-filled with the new task. Hit **Render
   dry-run** → review markdown → tick "I've reviewed every action"
   → **Execute now**. The verifier runs automatically and shows a
   green/red badge.
3. **↺ Rollback page**: pre-filled with the executed task. Hit
   **Preview** → see drift status per entry (green = clean, yellow =
   file modified since execute). Hit **Rollback now (clean)** if no
   conflicts, otherwise pick safe / force.

## Hash-drift demo (Phase 7.1 visualized)

The killer use case for the UI is making rollback's drift detection
**visible** instead of CLI-only:

1. Plan + execute via UI (workspace under `./sandbox/`).
2. While the run is intact, open PowerShell and manually edit one of
   the moved files: `echo "user edit" >> sandbox/.../papers/x.pdf`
3. Back in UI → Rollback page → **Preview**.
4. The entry for `x.pdf` shows up in yellow with the drift reason.
5. **Safe rollback** skips it; **Force rollback** (after extra confirm)
   overwrites the user's edit.

Same machinery the CLI's `localflow rollback --force` and the MCP
`rollback_preview` tool use — the UI just makes it 2 clicks instead
of 3 commands.

## What the UI doesn't do (yet)

- **No LLM streaming**. The Rich Live token stream the CLI shows
  during `--planner llm` is CLI-only. UI shows a spinner.
- **No realtime workspace tree**. After execute, you need to look at
  the verifier output for confirmation — not a live file tree diff.
- **No theming / branding**. Plain Streamlit default styling.
- **No multi-user / auth**. Single-user localhost only.
- **No GIF / screen recording integration**. Use asciinema or similar
  externally if you want to record demos.

These are all v0.8.0+ candidates.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: streamlit` on `ui-serve` | `pip install -e ".[ui]"` |
| Port 8501 already in use | `localflow ui-serve --port 8520` |
| Sidebar shows no workspaces | Create `./sandbox/something/` with some files in it |
| "workspace outside the soft sandbox" error | Either move the workspace under `./sandbox/`, or visit `?unsafe=1` |
| Page renders but actions don't fire | Check the terminal where you ran `ui-serve` — Streamlit logs there |

## Security posture

- **Localhost-only by default** (`127.0.0.1`). Same default as the MCP
  stdio server.
- **No auth**. If you bind `--host 0.0.0.0`, anyone on your LAN can
  drive the harness. Don't do that on untrusted networks.
- **Inherits ALL kernel safety**. The UI cannot bypass `policy_guard`,
  `forbidden_paths`, dry-run, approval (via Streamlit checkbox), or
  the independent verifier — those run inside the same Python process
  as the UI.
- **No new actions, no new IO surface**. UI calls
  `control_loop.run_*` directly; new file paths under
  `app/harness/` = 0.

See [docs/SECURITY.md](SECURITY.md) for the full threat model.
