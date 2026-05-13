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
| `dry_run` | `control_loop.run_dry_run` + mints approval_token | requires an existing `task_id`; **response includes `approval_token` (10-min TTL, one-shot)** |
| `execute_plan` | `control_loop.run_execute` + `run_verify` | **requires `approval_token` from a prior `dry_run`** — see [SECURITY.md](SECURITY.md#approval-tokens) |
| `rollback_run` | `Rollback.run()` | requires existing rollback manifest |

### Memory mutations

| Tool | Wraps | Default exposure |
|---|---|---|
| `memory_forbid_path` | `MemoryStore().add_forbidden_path(path)` | visible (adds a restriction; can only make things safer) |
| `memory_set_naming_style` | `MemoryStore().set_naming_style(value)` | visible (no security implication) |
| `memory_unset_naming_style` | `MemoryStore().clear_naming_style()` | visible (resets to default) |
| `memory_unforbid_path` | `MemoryStore().remove_forbidden_path(path)` | **HIDDEN** — see "Dangerous tools" below |

#### Dangerous tools — hidden by default

`memory_unforbid_path` removes a user-set safety boundary. To prevent
a buggy/hostile MCP client from undoing the user's `forbidden_paths`
and then executing against them, this tool is hidden from the MCP
tool list unless explicitly enabled:

```jsonc
// .mcp.json
{
  "mcpServers": {
    "localflow": {
      "command": "...",
      "args": [...],
      "env": {
        "LOCALFLOW_MCP_ALLOW_DANGEROUS": "1"
      }
    }
  }
}
```

The CLI `localflow memory unforbid` works regardless of this flag —
the local user already has shell access. The gate only constrains
the MCP surface.

## Typical client workflow

1. `localflow:inspect_workspace(path=…)` — see what's there.
2. `localflow:create_plan(workspace=…, goal="…", skill="folder_organizer")` — get a `task_id`.
3. `localflow:dry_run(task_id=…)` — preview the markdown AND mint an
   `approval_token` (10-min TTL, single-use, drift-sensitive).
4. `localflow:execute_plan(task_id=…, approval_token=<token>)` — commit.
   Verifier runs automatically. The token is consumed on success — a
   second `execute_plan` with the same token will fail.
5. `localflow:rollback_run(task_id=…)` — undo if anything looks wrong.

If `plan.json` or `dry_run.md` is modified between steps 3 and 4 (e.g.,
re-planning), the token becomes invalid — call `dry_run` again to
issue a fresh one.

## Safety contracts

1. **No new actions.** Every MCP tool is a thin wrapper around an
   existing `control_loop.*` function or `MemoryStore` method. The
   kernel's safety primitives (workspace containment, `forbidden_paths`,
   `forbidden_actions`, dry-run, verify, rollback) are inherited unchanged.
2. **Token-bound execute.** `execute_plan` requires an `approval_token`
   minted by a prior `dry_run` — defends against MCP clients skipping
   the dry-run inspection step. See [SECURITY.md](SECURITY.md#approval-tokens).
3. **Dangerous tools opt-in.** `memory_unforbid_path` is hidden from
   the tool list unless `LOCALFLOW_MCP_ALLOW_DANGEROUS=1`.
4. **Stdio purity.** stdout carries only JSON-RPC frames; all logs and
   incidental output go to stderr.
5. **Errors are values, not exceptions.** A failing tool call returns
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
