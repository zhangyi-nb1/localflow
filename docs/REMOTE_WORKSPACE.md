# `RemoteWorkspace` — SSH-backed Workspace

**Status**: shipped Phase 31 (v0.29.0)
**Audience**: users / operators driving LocalFlow plans against a
remote Linux box (lab VM, build server, edge node).

`RemoteWorkspace` is the third concrete `Workspace` Protocol
implementation, joining `LocalWorkspace` (Phase 28) and
`DockerWorkspace` (Phase 29). It routes every filesystem operation
through `ssh <host> -- <cmd>`, so the kernel sees the same Protocol
it sees for the other two backends.

---

## TL;DR

```bash
# 1. Make sure passwordless SSH works to your remote.
ssh -o BatchMode=yes user@example.com true   # must exit 0

# 2. Drive LocalFlow against that remote.
localflow execute task-2026-05-27-001 \
    --workspace ssh:user@example.com:/srv/localflow-ws
```

The remote workspace directory (`/srv/localflow-ws` here) is created
on first use and **not removed** on close — it's a regular directory
on a machine you own.

---

## Spec grammar

`--workspace` accepts three shapes:

| Spec | Backend | Notes |
|---|---|---|
| `local` *(default)* | `LocalWorkspace` | Plain host filesystem |
| `docker:<image>` | `DockerWorkspace` | Container-isolated, see [`docs/DOCKER_WORKSPACE.md`](DOCKER_WORKSPACE.md) |
| `ssh:<host>[:<port>][:<root>]` | `RemoteWorkspace` | This document |

### `ssh:` grammar in detail

```
ssh:<host>[:<port>][:<root>]
```

- **`<host>`** — required. Passed to `ssh` verbatim, so `~/.ssh/config`
  aliases work. Format: `[user@]hostname` or an `~/.ssh/config` `Host`
  alias.
- **`<port>`** — optional integer. Defaults to 22.
- **`<root>`** — optional. Must start with `/` so the parser can
  disambiguate it from `<port>`. Defaults to `/tmp/localflow-ws`.

### Examples

```bash
# Minimal — uses ~/.ssh/config alias "build-vm"
localflow execute T --workspace ssh:build-vm

# Explicit user + host
localflow execute T --workspace ssh:bob@lab.example.com

# Custom port (e.g. tunnel on 2222)
localflow execute T --workspace ssh:bob@example.com:2222

# Custom workspace root
localflow execute T --workspace ssh:bob@example.com:/data/wkspc

# Full grammar
localflow execute T --workspace ssh:bob@example.com:2222:/data/wkspc
```

---

## SSH requirements on the remote

RemoteWorkspace targets stock Linux machines. The remote must have:

| Required | Why |
|---|---|
| `sshd` accepting your client | Obvious |
| **Key-based auth** for your user | `BatchMode=yes` refuses interactive prompts; password auth would hang the harness |
| **Your host key in your client's `~/.ssh/known_hosts`** | First connection should be done manually so you can vet the host fingerprint |
| `sh`, `mkdir`, `mv`, `cp`, `cat`, `test`, `stat`, `sha256sum`, `ls`, `find` | All GNU coreutils. Available on any Debian / Ubuntu / RHEL / Alpine. |
| Writable path at `<root>` | The first op creates it via `mkdir -p` |

LocalFlow ships **zero** opinion on your remote's `sshd_config`,
firewall, or fail2ban — that's your call.

### Hard rules (honesty discipline)

- **Password auth is NOT supported**, and never will be from the
  harness side. If your remote insists on passwords, set up keys
  or fall back to `LocalWorkspace`.
- **`StrictHostKeyChecking` is NOT relaxed**. The harness uses your
  OpenSSH client's default policy. If you haven't already accepted
  the remote's host key into `known_hosts`, the connection will
  fail (with a clear error). This is intentional — `RemoteWorkspace`
  is "ship code to remote machines you trust", not "yolo any IP".
- **The remote directory is NOT removed on close.** Unlike
  `DockerWorkspace` (where the container is teardown-on-close),
  the remote is user-managed. Clean up with `ssh remote -- rm -rf <root>`
  yourself if you need to.

---

## Lifecycle

```python
from app.tools.remote_workspace import RemoteWorkspace

ws = RemoteWorkspace(
    host="bob@build.example.com",
    port=2222,
    workspace_root_remote="/srv/wkspc",
)
ws.start()         # ssh ... -- mkdir -p /srv/wkspc
try:
    ws.mkdir("sub/")
    ws.write_text("note.md", "hi")
    print(ws.read_text("note.md"))
finally:
    ws.close()     # releases ssh resources; remote dir stays
```

Or as a context manager:

```python
with RemoteWorkspace(host="bob@x", workspace_root_remote="/srv/wkspc") as ws:
    ws.mkdir("sub/")
```

When wired through the CLI (`--workspace ssh:...`), LocalFlow's
executor handles this lifecycle automatically.

---

## Trade-offs (read this before you ship)

| Property | Local | Docker | Remote (SSH) | Remote + agent-server |
|---|---|---|---|---|
| Per-op latency | ~10 μs | ~100-300 ms | ~100-300 ms + network RTT | ~5-20 ms + RTT |
| Filesystem isolation from host | none | container (full) | network (full) | network (full) |
| Persistence after teardown | persistent | wiped | persistent | persistent |
| Bootstrap cost | none | image pull (~50 MB) | manual key setup | + ssh -L tunnel + agent spawn |
| Failure mode | OS errors | docker daemon | ssh / network / sshd | + agent crash → ssh fallback |
| Best for | dev loops | risky / experimental | dedicated remote workers | latency-sensitive remote runs |

## Phase 33.2 — agent-server mode (opt-in)

For latency-sensitive workloads against a remote, opt into the
agent-server tunnel mode:

```python
ws = RemoteWorkspace(
    host="bob@example.com",
    use_agent_server=True,   # ← Phase 33.2 opt-in
)
ws.start()
```

What it does:

1. After the standard `mkdir -p` connectivity probe succeeds, a free
   local host port is picked.
2. `ssh -L <host_port>:127.0.0.1:8765 <host> -- env ... python3 -`
   opens an SSH tunnel + streams the bundled agent-server (~26 KB)
   over stdin to `python3 -` on the remote.
3. The remote agent binds to 127.0.0.1:8765, prints
   `AGENT_SERVER_PORT/TOKEN/WORKSPACE` to the ssh subprocess stdout.
4. RemoteWorkspace reads those, opens an `AgentServerClient` pointed
   at the local tunnel head, and routes every subsequent op via HTTP.
5. **Fallback**: any startup failure (no `python3` on remote, port
   conflict on local host, tunnel timeout) → warning to stderr, ops
   fall through to ssh-per-op.
6. `close()` terminates the ssh process, which collapses the tunnel
   AND kills the remote agent (sshd reaps orphaned children).

Per-op latency drops from "ssh-per-op (~100-300 ms + RTT)" to "HTTP
over tunnel (~5-20 ms + RTT)". Most of the saving is the avoided
fresh-ssh-connection overhead.

### Requirements (in addition to the SSH ones above)

- Remote has `python3` on PATH (any modern Linux distro).
- Remote can install pydantic (or already has it via a venv).
  The bundle errors out cleanly with "agent-server bundle requires
  pydantic" if missing — fallback then kicks in.

### Why opt-in (not default)

Same reasoning as DockerWorkspace's agent-server mode: existing
scripts depending on Phase 31 ssh-exec semantics keep working;
operators see the fallback warning if startup fails (no silent
perf regression).

---

## Troubleshooting

### `RemoteUnavailable: ssh CLI not reachable`

You don't have OpenSSH client installed. Install `openssh-client`
(Linux), `OpenSSH for Windows` (Win), or use macOS's bundled `ssh`.

### `RemoteUnavailable: ssh probe to '<host>' failed: ... Permission denied (publickey).`

Key-based auth is not set up for this user/host. Fix:

```bash
ssh-copy-id user@host
# or manually:
cat ~/.ssh/id_rsa.pub | ssh user@host 'cat >> ~/.ssh/authorized_keys'
```

Then verify:

```bash
ssh -o BatchMode=yes user@host true   # must exit 0
```

### `Host key verification failed.`

The remote's host key isn't in your `~/.ssh/known_hosts`. Do one
manual connection first:

```bash
ssh user@host    # accept the fingerprint
```

Never silently bypass this with `StrictHostKeyChecking=no` —
LocalFlow doesn't, and you shouldn't either.

### Operations are slow

Expected. Every Workspace op = one `ssh exec` (~100-300 ms + network
RTT). For a plan with N actions, expect N×RTT overhead. Phase 32
candidate (HTTP agent-server) can amortise this to a single
connection.

---

## Boundary guarantee

RemoteWorkspace lives in `app/tools/` and never imports from
`app/harness/`. The host-side `_validate_rel_path` defence is a
1-to-1 mirror of `DockerWorkspace`'s same-named function — both
mirror `policy_guard.resolve_inside`. The kernel never sees a
RemoteWorkspace-specific code path.

The Phase 30.2 boundary lint (`tests/test_kernel_boundary.py`)
enforces this rule for the whole `localflow_kernel.*` graph; this
file in `app/tools/` is application-layer by design and excluded
from the kernel package.

---

## Future direction

| Phase | Step | Status |
| --- | --- | --- |
| 31.0 | Design doc                                  | ✅ shipped 2026-05-27 |
| 31.1 | Implementation + mock-subprocess tests      | ✅ shipped 2026-05-27 |
| 31.2 | User docs + ledger + commit                 | ✅ shipped 2026-05-27 |
| 32 (candidate) | HTTP agent-server — shared by Docker + Remote, amortises per-op latency | not committed |
| 33 (candidate) | Multiplexed SSH (ControlMaster) — same ssh process for multiple ops | not committed |

Each unlocks once evidence (a perf-sensitive workload, an integration
ask, a CI demand) makes it the obvious next step.
