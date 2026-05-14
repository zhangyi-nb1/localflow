# LocalFlow UI (Streamlit) — v0.9.0

A localhost-only browser UI that wraps the same harness the CLI and
MCP server use. Built for users who'd rather click than memorize 5
CLI commands.

> The UI is a **driver**, not the kernel. Every action still passes
> through policy_guard, dry-run, approval, executor, verifier, audit,
> rollback. Nothing in `app/ui/` can perform IO that the CLI couldn't.

**v0.9.0 highlights** (Phase 8.3 agent meta-skill):
- **One skill, one plan, one compound goal**. The Plan page no longer
  asks you to pick between five specialist skills. It always routes
  through the new `agent` meta-skill — an LLM-driven planner that
  decomposes "整理 + 分析 + 绘制柱状图" into a single ActionPlan
  covering every step. No more "run a second task for the chart".
- **Override panel removed**. Specialist skills (folder_organizer,
  workspace_visualizer, …) remain available via CLI (`--skill <name>`)
  and MCP for power users, but they don't show up in the UI anymore.
- **Real PNG charts in one go**. The agent's system prompt teaches
  the LLM to emit `metadata.chart_request` blocks; the skill renders
  them through `chart_ops.bar_png` after the LLM call. Plan.json
  stays text-only and small; the harness still sees a single
  ActionPlan it can dry-run / verify / rollback.

**Earlier highlights** (v0.8.x, still present):
- Sidebar language toggle (English / 中文)
- Sticky `?unsafe=1` mode that survives page navigation
- Radio-driven workspace picker (sandbox subdir vs custom path)
- Active workspace badge always visible at the top of the sidebar
- Memory page with `forbidden_paths` + `naming_style` +
  `prefer_llm_planner` toggles

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
| 📋 **Plan** | Goal textarea → auto-detected skill + planner → structured ActionPlan + risk badge. Manual override available. |
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

A yellow banner appears across every page. In the sidebar, the
**Source** radio now offers a "Custom path" option alongside
"Sandbox subdir" — pick it and a free-text input appears with live
validation (✅ on existing dir, ❌ with the reason otherwise). The
kernel's `policy_guard.resolve_inside` + `forbidden_paths` still
apply; this just lifts the UI-layer restriction so the custom path
becomes a valid workspace choice.

Without `?unsafe=1`, the "Custom path" radio option is hidden
(rather than greyed-out — Streamlit doesn't natively disable a
single radio option) and a caption explains how to enable it.

## Lifecycle through the UI

The full plan → dry-run → execute → verify → rollback flow takes
about 30 seconds and roughly 5 clicks:

1. **📋 Plan page**: type a goal (e.g. "organize by file type" or
   "整理文件并画柱状图统计"). The page shows
   `ℹ️ Auto-detected: skill=agent · planner=llm` and a one-line
   confirmation that the agent will plan end-to-end. Hit
   **Create plan** — that's it. See the action table + risk badge.
2. **🔍 Execute page**: pre-filled with the new task. Hit **Render
   dry-run** → review markdown → tick "I've reviewed every action"
   → **Execute now**. The verifier runs automatically and shows a
   green/red badge.
3. **↺ Rollback page**: pre-filled with the executed task. Hit
   **Preview** → see drift status per entry (green = clean, yellow =
   file modified since execute). Hit **Rollback now (clean)** if no
   conflicts, otherwise pick safe / force.

### Single skill, no override

Starting in v0.9.0 the Plan page exposes exactly **one** skill — the
`agent` meta-skill ([app/skills/agent/](../app/skills/agent/)). It
decomposes compound goals into a single ActionPlan covering organize
+ summarize + chart in one harness cycle (one dry-run, one approval,
one execute). The Override panel from v0.8.x is gone — the agent is
always the right choice for goal-driven UI use, and the harness
catches mistakes regardless.

Specialist skills (`folder_organizer`, `pdf_indexer`, `data_reporter`,
`data_analyzer`, `workspace_visualizer`) stay in the registry for CLI
and MCP power users — they're not removed, just not exposed in the
Plan page. Use `localflow plan ... --skill <name>` from PowerShell if
you want one of them by hand.

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
- **Language preference is session-scoped**, not persisted across
  browser sessions. Set it once per tab.
- **No GIF / screen recording integration**. Use asciinema or similar
  externally if you want to record demos.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: streamlit` on `ui-serve` | `pip install -e ".[ui]"` |
| Port 8501 already in use | `localflow ui-serve --port 8520` |
| Sidebar shows no workspaces | Create `./sandbox/something/` with some files in it, then click **🔄 Refresh** |
| "workspace outside the soft sandbox" error | Either move the workspace under `./sandbox/`, or visit `?unsafe=1` and pick **Custom path** in the sidebar Source radio |
| Custom path radio option missing | Visit `?unsafe=1`; without it the option is hidden by design |
| Custom workspace silently reverts to sandbox after clicking a page | Fixed in v0.8.1 — unsafe mode now latches into session_state so page navigation no longer drops the opt-in |
| Custom path input rejects a path | Make sure it's an absolute path to an existing directory. The error message under the input names the exact reason. |
| Want LLM by default on every plan | Memory page → 🤖 Planner preference → toggle "Prefer LLM planner" ON. (The UI's agent skill already uses LLM by default; this flag affects specialist skills invoked from CLI/MCP.) |
| Want a real PNG bar chart of file counts | Write a compound goal like "整理文件并画一张柱状图统计每个分类的文件数". The agent emits both organize + chart actions in one plan; the post-processor renders a real PNG (not a markdown placeholder). |
| Plan page shows a chart `_error.md` instead of a PNG | The LLM emitted a chart action without the required `chart_request` block. The harness defensively downgrades that to a markdown error placeholder. Re-run with a more explicit goal mentioning "柱状图" / "bar chart" — usually fixes it. |
| Want to force a specific skill (e.g. `pdf_indexer` for batch indexing) | Use the CLI: `localflow plan <workspace> --goal "..." --skill pdf_indexer --planner rule`. Specialist skills aren't exposed in the UI but still work from CLI/MCP. |
| Strings show up as `!!key.something!!` | A translation key is missing — file an issue. UI keeps rendering with the sentinel rather than crashing. |
| Language toggle persists across browser sessions | It doesn't — by design. Streamlit session_state is per-tab. Reset on every new tab. |
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
