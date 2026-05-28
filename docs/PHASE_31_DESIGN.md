# Phase 31 — RemoteWorkspace (SSH-backed)

**Status**: design locked 2026-05-27, ready to ship as a 2-slice spike
**Predecessor**: Phase 30 (`localflow_kernel` package) shipped 2026-05-27
**Tracking goal alignment**: completes the Workspace facade triplet
promised in `README.md` ("Local + Docker shipped; Remote planned")

---

## 1. Why now

Phase 28 introduced the `Workspace` Protocol with the explicit comment
"Phase 30 candidate is `RemoteWorkspace`". Phase 29 shipped
`DockerWorkspace` as the second backend. The README's lifecycle ASCII
art currently states "Local + Docker shipped; Remote planned" — and per
**CLAUDE.md rule F (honesty discipline)**, "planned" cannot live in the
README indefinitely. Phase 31 either ships RemoteWorkspace or we delete
the README line.

We ship it.

---

## 2. Why SSH (not HTTP agent-server, not gRPC)

Three candidate protocols were considered:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **SSH** (`ssh user@host -- sh -c '...'`) | Zero new deps; mirrors DockerWorkspace's `docker exec` pattern; works against any Linux box with sshd; auth via existing user keys / ssh-agent | Per-op latency same as DockerWorkspace (~100-300ms); no streaming optimisations | **Ship this for Phase 31** |
| HTTP agent-server | Long-lived connection avoids per-op handshake; batch ops; ~10x faster on hot paths | Needs to ship + bootstrap an agent binary into the remote; auth + TLS + port mgmt; large new surface; would also need a DockerWorkspace migration to amortise the work | Defer to **Phase 32 candidate** |
| gRPC / Cap'n Proto | Strongly typed protocol; bidirectional streaming | Even more deps + bootstrap; no incremental benefit over HTTP for our use case | Rejected |

**Decision rationale**: SSH is isomorphic to docker exec — same exec-
per-op shape, same coreutils dependence, same error handling. The
class can be ~300 LOC, mirroring `app/tools/docker_workspace.py`'s
~430 LOC. The whole spike fits in two slices.

The performance ceiling is shared with DockerWorkspace and is acceptable
for plan execution (tens of actions). When per-op latency starts to
bite (Phase 32 candidate signal), an HTTP agent-server can lift BOTH
backends in one go.

---

## 3. Protocol shape

### 3.1 Lifecycle

```python
ws = RemoteWorkspace(host="user@example.com", port=22, root="/srv/localflow-ws")
ws.start()         # ssh user@example.com -- mkdir -p /srv/localflow-ws
try:
    ws.mkdir("sub/")            # ssh ... -- mkdir -p /srv/localflow-ws/sub
    ws.write_text("note.md", "hi")  # ssh ... -- sh -c "cat > ...; chmod ..."
finally:
    ws.close()      # no-op (we don't rm the remote dir; user owns it)
```

`close()` is intentionally a no-op for the workspace itself — unlike
DockerWorkspace which removes the container, the remote directory is
user-managed. We only release ssh control-master sockets if we used one.

### 3.2 Auth & host resolution

SSH config drives auth:

- `~/.ssh/config` is honoured — `host` is passed to `ssh` verbatim, so
  users can use `Host` aliases
- Key-based auth assumed (private key loaded into ssh-agent OR
  configured in `~/.ssh/config`)
- **No password auth from LocalFlow**: `ssh` will block on stdin
  prompting and the harness will hang. Document this as a hard
  requirement.
- **No `StrictHostKeyChecking=no` by default**: users must accept the
  remote host's key into `~/.ssh/known_hosts` themselves. CLAUDE.md
  rule F (don't oversell isolation) — RemoteWorkspace is "ship code
  to remote machines you trust", not "yolo any IP on the internet".

### 3.3 SSH command shape

Every op spawns: `ssh <opts> <host> -- sh -c '<cmd>'` where `<cmd>` is
the same shell expression we'd run via `docker exec`. The `--` is
critical — defends against `<host>` accidentally being interpreted as a
flag.

Default `<opts>`:

```
-o BatchMode=yes              # fail fast if password is required
-o ConnectTimeout=10
-o ServerAliveInterval=30
-o ServerAliveCountMax=3
-p <port>                     # only when port != 22
```

### 3.4 Path defence

Same `_validate_rel_path` defence as `docker_workspace.py` — relative
paths only, no `..`, no `~`, no drive letters, no leading `/`. The
remote shell never sees an attacker-controlled absolute path.

Inside the workspace, all paths are joined onto `self.root` (which is
an absolute path on the remote machine) and quoted via `shlex.quote`.

### 3.5 Operation mapping

Same as DockerWorkspace (every op shells out one short command); only
the prefix changes (`ssh user@host --` instead of
`docker exec <id>`).

| Workspace op | Remote command |
|---|---|
| `exists(p)`     | `test -e <p>` |
| `stat(p)`       | `stat -c '%s\|%F' <p>` (Linux GNU stat) |
| `sha256(p)`     | `sha256sum <p> \| cut -d' ' -f1` |
| `list_dir(p)`   | `ls -1A <p>` (sorted client-side) |
| `read_bytes(p)` | `cat <p>` (binary stdout) |
| `read_text(p)`  | `cat <p>` + decode |
| `mkdir(p)`      | `mkdir -p <p>` (returns exit-0; "did we create?" inferred from `test -d` pre-check) |
| `move(s,d)`     | `mv <s> <d>` |
| `copy(s,d)`     | `cp -R <s> <d>` (mirror DockerWorkspace's `-R`) |
| `rename(s,d)`   | same as `move` |
| `write_text(p,c)` | `cat > <p>` with `c` piped via ssh stdin |
| `write_bytes(p,b)` | same; bytes passthrough |
| `safe_target_rel(p)` | client-side: probe `exists` in a loop |

This is exactly DockerWorkspace's table with the prefix swapped. The
implementation can lift large blocks of the docker_workspace.py code
unchanged.

---

## 4. Test strategy

Three layers:

1. **Path-defence unit tests** (no SSH needed)
   `_validate_rel_path` is the host-side defence — exactly the same
   shape as docker_workspace's tests. Mirror the 9 test cases.

2. **Protocol unit tests** (mock `subprocess.run`)
   Build a fake subprocess that records every `ssh` invocation; assert
   the command shape. Cover all Workspace ops + lifecycle (start /
   close / context manager). This is the bulk of the suite (~20-25
   tests) and runs everywhere with no real SSH.

3. **SSH-actual contract tests** (skipif no localhost ssh)
   Probe whether `ssh -o BatchMode=yes localhost true` works (some
   dev boxes have it, CI workers don't). If yes, run the same
   container-actual test suite shape as DockerWorkspace did. If no,
   skip — same pattern as `_skip_no_docker`.

CI mostly skips layer 3 (we don't ship a sshd container in the matrix).
The path-defence + protocol layers carry full coverage.

---

## 5. CLI wiring

`parse_workspace_spec` learns a new prefix:

```python
parse_workspace_spec("ssh:user@host", workspace_root=...)
parse_workspace_spec("ssh:user@host:2222", workspace_root=...)
parse_workspace_spec("ssh:user@host:/srv/wkspc", workspace_root=...)
parse_workspace_spec("ssh:user@host:2222:/srv/wkspc", workspace_root=...)
```

Spec grammar: `ssh:<host>[:<port>][:<root>]`

- `<host>` — must contain at least one char; passed through to ssh as-is
- `<port>` — optional integer; defaults 22
- `<root>` — optional absolute path; defaults `/tmp/localflow-ws`

The CLI gains:
- existing `--workspace local | docker:<image>` extended to `ssh:...`

---

## 6. §10.7 ledger

**0 kernel touches.** RemoteWorkspace lives in `app/tools/`, implements
the `Workspace` Protocol (Phase 28 surface), and never imports from
`app/harness/`. Same scope discipline as DockerWorkspace.

Ledger row: zero kernel touches; +13 zero-kernel-touch entries become
+14.

---

## 7. Slice plan

### Phase 31.0 — design doc (this file)

- protocol design + ssh command table
- test strategy
- CLI grammar
- 0 code changes

### Phase 31.1 — implementation + tests

- `app/tools/remote_workspace.py` (~300 LOC; isomorphic to
  docker_workspace.py)
- `app/tools/workspace.py::parse_workspace_spec` learns `ssh:` prefix
- `tests/test_workspace_remote.py` — path-defence + protocol-mock layers
  (~20-25 tests)
- CLI integration in `app/cli.py` (workspace flag already covers it via
  `parse_workspace_spec`)
- `docs/REMOTE_WORKSPACE.md` user manual
- `docs/PHASES.md` ledger row
- README ASCII-art footnote: drop "Remote planned" → "Local + Docker +
  Remote shipped"
- `pytest` green locally + CI

### Phase 31 done = green CI + tag candidate v0.29.0

- design + impl + tests all committed
- mock-subprocess tests pass on all CI matrix legs
- ssh-actual tests skip cleanly when localhost ssh unavailable

---

## 8. Future direction this unlocks

| Phase | Step | Status |
| --- | --- | --- |
| 31.0 | Design doc                                  | this file |
| 31.1 | RemoteWorkspace impl + mock-subprocess tests | pending |
| 32 (candidate) | HTTP agent-server (shared by Docker + Remote backends; ~10x throughput on hot paths) | not committed |
| 33 (candidate) | Physically relocate kernel impl modules from `app/` → `localflow_kernel/`; drop back-compat re-exports | not committed |
| 34 (candidate) | PyPI distribution of `localflow_kernel` | not committed |

Each unlocks once evidence (a perf benchmark, a downstream consumer, an
integration ask) makes it the next obvious step. SSH RemoteWorkspace is
the right today step because it closes the README's "planned" promise
with the lowest-risk path.
