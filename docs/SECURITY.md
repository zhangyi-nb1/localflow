# LocalFlow — Security Model

This document is the honest, explicit description of what LocalFlow's
safety machinery DOES and DOES NOT defend against. The project is a
research / personal-automation harness — not a hardened production
security boundary. Read this before exposing LocalFlow to untrusted
input or running external skills.

---

## What the harness DOES enforce

### Workspace containment
Every action's `source_path` and `target_path` must resolve to a path
inside the task's `workspace_root`. Parent-dir traversal (`..`),
absolute paths, and symlinks pointing outside the workspace are
rejected by `policy_guard.resolve_inside`.

### Forbidden paths (Phase 5)
The kernel reads `task.forbidden_paths` (populated from Memory) and
rejects any action whose source or target is at-or-under a forbidden
entry. **This check lives in `policy_guard`, not in any Skill**, so a
buggy or malicious Skill cannot bypass it by simply forgetting to
implement the check.

### Pre-flight + execute-time policy check
`policy_guard.evaluate_action` runs twice:
1. At plan time — informs the risk assessment
2. At execute time — defense in depth, in case state changed in between

Both passes share the same `forbidden_actions` and `forbidden_paths`
inputs from the TaskSpec.

### Independent verifier
The verifier ([app/harness/verifier.py](../app/harness/verifier.py))
runs *after* execution and checks the actual filesystem state against
the plan + rollback manifest. It NEVER asks the model "did it work?"
— success is determined by deterministic rules.

### Rollback
Every successful write produces a `RollbackEntry`. A failed run or a
user-initiated rollback replays the manifest in reverse, restoring
backups where overwrites happened.

### Audit
Every action, every plan, every memory mutation, every external skill
load attempt writes to a JSONL log under `~/.localflow/` (or
`$LOCALFLOW_HOME`). There is no path through the system that performs
side effects without leaving a record.

---

## MCP server hardening (Phase 7)

### Approval tokens — `execute_plan` is no longer a free function call

**Before Phase 7**: an MCP client could call
`execute_plan(task_id, approved=true)` directly, skipping `dry_run`
entirely. The `approved=true` arg was just a string in the JSON-RPC
message — there was no enforcement that the human had ever seen the
plan.

**After Phase 7**: `execute_plan` requires an `approval_token` minted
by a prior `dry_run` call. The token is:

| Property | Detail |
|---|---|
| **Bound to** | (`task_id`, `plan_hash`, `dry_run_hash`, `workspace_root`) |
| **TTL** | 10 minutes from `dry_run` |
| **Single-use** | Deleted on successful execute |
| **Drift-sensitive** | If `plan.json` or `dry_run.md` is modified after the token is minted, the token becomes invalid |
| **Storage** | `<run_dir>/approval_token.json`, atomic write |

The full flow:
1. `create_plan(...)` → `task_id`
2. `dry_run(task_id)` → mints token, returns `{markdown, approval_token, approval_expires_at}`
3. *Client / human reviews the markdown*
4. `execute_plan(task_id, approval_token)` → validates token, executes if green, deletes token on success
5. Second `execute_plan` with the same token → rejected (token consumed)

CLI's `execute --yes` does NOT use tokens — a human at the keyboard
typing `--yes` is the approval gate. Tokens defend against the
*external-client* threat model only.

See [app/mcp/approval.py](../app/mcp/approval.py) for the
implementation, [tests/test_mcp_tools.py](../tests/test_mcp_tools.py)
"approval tokens" section for the assertion suite.

### Dangerous tools hidden by default

`memory_unforbid_path` **removes** a user-set safety boundary. If
exposed unconditionally to MCP clients, a buggy or hostile client
could:

1. Call `memory_unforbid_path("private/secrets")`
2. Call `create_plan(...)` (now without the forbidden_paths protection)
3. Call `dry_run` + `execute_plan` (with the token from step 3)
4. → secrets touched

To prevent this, `memory_unforbid_path` is marked `dangerous=True` in
the `ToolDef` table. By default the MCP server hides it from the
advertised tool list entirely — clients see it as an unknown tool.

**To opt in** (for trusted, local-only setups), set the env var in
the MCP server's environment:

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

CLI `localflow memory unforbid` is always available to the local user
regardless of this flag — the CLI requires shell access, which is
already a stronger trust signal than an MCP message.

Tools currently marked dangerous:
- `memory_unforbid_path` — removes a forbidden_paths entry

---

## What the harness DOES NOT defend against

### ⚠️ External skills are TRUSTED Python code

**This is the single largest caveat in the security model.**

Phase 4.1 filesystem skill discovery loads external skills via
`importlib.util.spec_from_file_location` — full Python execution at
import time. Once loaded, the skill is a Python module in the same
process. **A skill can `import os; os.unlink(...)` and bypass every
safety primitive listed above.**

The Phase 4.2 Tool Registry validates a Skill's *declared*
`required_tools` against the registry catalog. That validation
catches typos and API drift — but it does NOT prevent the skill from
importing whatever it wants once loaded. The Tool Registry is a
documentation + integration surface, not a sandbox.

The Phase 4.3 contract test confirms a skill is *compatible* with the
LocalFlow lifecycle. It does NOT confirm the skill is *safe*.

**What this means for users**:

| If you... | Risk |
|---|---|
| Write your own Skill | Same trust level as writing your own scripts — i.e., full machine access |
| Install a Skill from a public source | Full machine access, equivalent to `pip install` from that source |
| Run LocalFlow with `$LOCALFLOW_SKILLS_DIR` pointing at an untrusted directory | Code execution risk equivalent to running arbitrary scripts from that directory |

**Mitigations available now**:

1. **Inspect the skill source** before dropping it into
   `~/.localflow/skills/`. Skills are short (~50-200 lines typically).
2. **Don't accept skills from unknown authors**. Same threat model as
   pip packages.
3. **Check the load audit** with `localflow skills` — every load
   attempt appears in the table, including where the file came from.
4. **Use a clean `$LOCALFLOW_SKILLS_DIR`** for testing third-party
   skills instead of the default `~/.localflow/skills/` so you can
   pull the rug out by unsetting the env var.

**Mitigations on the roadmap (not implemented)**:

1. Subprocess isolation — each external skill runs in its own Python
   subprocess; only typed JSON over a pipe crosses the boundary.
2. Static AST scan rejecting dangerous imports (`os.unlink`,
   `subprocess`, `shutil.rmtree`, ...) — best-effort, can be
   sidestepped via `getattr`-style indirection.
3. WASM / RestrictedPython runtime — true sandboxing at performance cost.
4. Declarative Skill manifest where `plan()` is data, not Python.

**None of these are shipped today.** Treat the current external skill
mechanism as "trusted plug-in loader", not as a sandbox.

### Trust boundary for MCP itself

The MCP server runs in the same process and same user account as the
caller (the local user starting `localflow mcp-serve`). It does NOT:
- Authenticate MCP clients (stdio transport — anyone with stdin
  access is the client)
- Encrypt frames (not needed for stdio; would matter for HTTP/SSE)
- Survive an external client passing absolute paths to
  `inspect_workspace` for any directory the local user could read
  (this is intended — the *user* asked to expose LocalFlow this way)

The MCP server is a **convenience surface, not a remote execution
endpoint**. Do not expose it to an untrusted network.

### LLM injection / prompt-level attacks

When a Skill's `plan_with_llm()` is used, the LLM sees workspace file
previews. A document in the workspace could contain prompt-injection
content that tries to manipulate the planner's output. The harness
mitigates by:

1. **Structured output** — the LLM emits a typed `ActionPlan` via
   strict tool call. Prose injected by a document cannot become an
   action; only valid Pydantic shapes are accepted.
2. **policy_guard** — even a "valid" malicious plan can't escape the
   workspace, can't touch `forbidden_paths`, can't use forbidden
   action types.
3. **dry_run + approval** — the user (or approval_token holder) sees
   what's about to run before it runs.

What's NOT mitigated:
- A prompt injection could still nudge the plan toward *legitimate-
  but-undesired* actions (e.g., "move all PDFs to a category named
  'spam'") that survive policy_guard because they're inside the
  workspace and use allowed action types. Dry-run is the user's last
  line of defense here.

---

## Reporting security issues

This is a personal-research project, not a service. If you find a
real security bug:

1. Open an issue on https://github.com/zhangyi-nb1/localflow describing
   the problem (or, for higher-severity issues, contact the author
   directly via the email on the repo profile).
2. Do not publish proof-of-concept code that exercises a real user's
   data.
3. Expect a best-effort response, not an SLA.

---

## Quick safety checklist for users

Before using LocalFlow in earnest:

- [ ] Read this document.
- [ ] Inspect any external skill before dropping it into `~/.localflow/skills/`.
- [ ] Use `localflow memory forbid` for paths you never want touched
      (e.g., `private`, `~/Documents/Important`).
- [ ] Always read the `dry_run` output before approving an `execute`.
- [ ] Keep `LOCALFLOW_MCP_ALLOW_DANGEROUS` *unset* unless you have a
      specific need to expose `memory_unforbid_path` via MCP.
- [ ] Keep run state directory backed up; rollback can restore
      individual run state but not protect against `~/.localflow/`
      being deleted.
