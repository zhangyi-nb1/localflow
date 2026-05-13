# MCP Server (Phase 6.1)

LocalFlow can run as an [MCP](https://modelcontextprotocol.io) server so
Claude Code, Claude Desktop, or any MCP client can drive it over stdio
JSON-RPC. **Every safety primitive is inherited verbatim** — MCP only
wraps the existing CLI surface; it cannot invent new actions or bypass
the policy guard.

## Install

```powershell
pip install "mcp>=1.6,<2.0"
# or, if you have LocalFlow installed in dev mode:
pip install -e ".[mcp]"
```

## Start manually (smoke test)

```powershell
python -m app.cli mcp-serve
```

The server listens on stdin/stdout and exits when stdin closes
(Ctrl+C also works). Logs go to stderr — stdout is reserved for
JSON-RPC frames.

## Wire it into Claude Code / Claude Desktop

Add to your MCP client config (e.g., Claude Code's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "localflow": {
      "command": "python",
      "args": ["-m", "app.cli", "mcp-serve"],
      "cwd": "C:\\Users\\13513\\Desktop\\XIANGMU\\localflow"
    }
  }
}
```

Adjust `cwd` to your local clone. Restart the client. The tools below
appear under the `localflow:` namespace.

## Available tools (15)

### Read-only

| Tool | Wraps | Purpose |
|---|---|---|
| `inspect_workspace` | `control_loop.run_inspect` | Scan a directory, return file metadata + previews. |
| `list_skills` | `get_default_registry()` | List built-in + external skills (Phase 4.1). |
| `list_tools_catalog` | `get_default_tool_registry()` | List the Phase 4.2 Tool Registry. |
| `list_runs` | iterate `~/.localflow/runs/` | Per-run completion summary for every prior task. |
| `read_run` | RunStore + JSON loads | Load all artifacts for one task_id. |
| `read_memory_prefs` | `MemoryStore().load()` | Read persisted user preferences. |
| `read_memory_audit` | `MemoryStore().read_audit()` | Read memory mutation audit log. |

### State-changing (always through harness)

| Tool | Wraps | Pre-conditions |
|---|---|---|
| `create_plan` | RunStore.create + inspect + skill.plan + risk_check | rule planner only; LLM planning is CLI-only |
| `dry_run` | `control_loop.run_dry_run` | requires an existing `task_id` |
| `execute_plan` | `control_loop.run_execute` + `run_verify` | **must pass `approved: true`** (matches CLI `--yes`) |
| `rollback_run` | `Rollback.run()` | requires existing rollback manifest |

### Memory mutations

| Tool | Wraps |
|---|---|
| `memory_forbid_path` | `MemoryStore().add_forbidden_path(path)` |
| `memory_unforbid_path` | `MemoryStore().remove_forbidden_path(path)` |
| `memory_set_naming_style` | `MemoryStore().set_naming_style(value)` |
| `memory_unset_naming_style` | `MemoryStore().clear_naming_style()` |

## Typical client workflow

1. `localflow:inspect_workspace(path=…)` — see what's there.
2. `localflow:create_plan(workspace=…, goal="…", skill="folder_organizer")` — get a `task_id`.
3. `localflow:dry_run(task_id=…)` — preview the markdown.
4. `localflow:execute_plan(task_id=…, approved=true)` — commit. Verifier runs automatically.
5. `localflow:rollback_run(task_id=…)` — undo if anything looks wrong.

## Safety contracts

1. **No new actions.** Every MCP tool is a thin wrapper around an
   existing `control_loop.*` function or `MemoryStore` method. The
   kernel's safety primitives (workspace containment, `forbidden_paths`,
   `forbidden_actions`, dry-run, verify, rollback) are inherited unchanged.
2. **No interactive prompts.** `execute_plan` requires `approved: true`
   as an explicit argument — matching the CLI's `--yes` flag.
3. **Stdio purity.** stdout carries only JSON-RPC frames; all logs and
   incidental output go to stderr.
4. **Errors are values, not exceptions.** A failing tool call returns
   `{"error": "..."}` as a JSON payload — the protocol stays healthy.

## Caveats

- **LLM planning is CLI-only.** It streams to a Rich Live display
  which can't traverse stdio. Workaround: `localflow plan … --planner
  llm` first, then drive `execute_plan / verify / rollback` via MCP.
- **No HTTP/SSE transport.** stdio only for MVP. Open an issue if you
  need HTTP.
- **`forbidden_paths` from memory** automatically propagates to every
  `create_plan` call — clients see them in the response's
  `applied_preferences` field.
