# Phase 33 — Docker + Remote integration with agent-server

**Status**: design locked 2026-05-28, four-slice spike
**Predecessor**: Phase 32 (HTTP agent-server building blocks) shipped 2026-05-28
**Tracking goal alignment**: tangible **10× per-op throughput** on
hot paths; closes the "Phase 32 builds engine, no car attached"
gap CLAUDE.md §5 audit flagged.

---

## 1. Why now

Phase 32 shipped a working `AgentServer` + `AgentServerClient` +
`AgentServerWorkspace`. They pass 64 tests in isolation, but the
existing `DockerWorkspace` and `RemoteWorkspace` still pay the
~100-300 ms per-op cost of `docker exec` / `ssh` round-trips. From
the user's perspective, Phase 32 currently sits dormant.

Phase 33 wires the agent-server into both existing backends so the
perf upgrade is on by default. Same Workspace Protocol, same kernel
contract; users see lower latency without changing their code.

---

## 2. Distribution strategy

### 2.1 Decision: `sh -c "python3 -c '...'"` stdin injection

We need the agent-server's Python code to run **inside** the
container / on the remote host. Three options were considered:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Baked into a published image** (`localflow/agent-server:0.30.0`) | Fastest startup; immutable + signed | Requires publishing OCI images + tagging discipline; user can't override Python version | Defer to Phase 34+ when CI publishes signed images |
| **B. `docker cp` / `scp` the package** | No image dep; works on any base image / any remote | Two round-trips before first op; needs to know remote tmp path; ssh adds key dance | Reasonable but slower bootstrap |
| **C. `sh -c "python3 -c '...'"` stdin injection** | One round-trip; no files left on disk; works against any image / any sshd | Code base64-encoded → larger argv; harder to debug | **Ship this for Phase 33** |

**Rationale**: Option C is the simplest contract:

```
docker exec -i <container> sh -c 'python3 -c "<encoded server.py>"' &
```

The container's `python3` (always present in `python:3.12-slim` and
any sshd-equipped Linux box) reads the agent-server source from a
single `-c` argument, picks an ephemeral port + token, and prints
them to stdout. The supervisor (host process) reads those, opens a
TCP tunnel, and starts shipping HTTP requests through it.

When the container / remote shell ends, the Python process dies
cleanly — no files left, no cleanup hooks needed.

### 2.2 Bootstrap script: one Python module, no extra deps

The agent-server code is in a single package (`app/tools/agent_server/`)
that the harness already builds. We need a way to bundle protocol +
server + (subset of) `app.tools.workspace` (just `WorkspaceStat`) +
(subset of) `app.tools.hash_ops` (just `sha256_file`) into a **single
self-contained string** runnable via `python3 -c`.

**Implementation**: build-time bundling.

```python
# app/tools/agent_server/bundle.py  (new in Phase 33.1)
def build_bundle() -> str:
    """Return a self-contained Python source string that:
       1. Defines WorkspaceStat (copied verbatim).
       2. Defines sha256_file (copied verbatim).
       3. Embeds protocol.py + server.py contents.
       4. At the end, calls _main() which prints port/token to stdout
          and serves until SIGTERM.
    """
```

Each kernel-pure module the agent-server depends on is small
(WorkspaceStat ~10 LOC, sha256_file ~10 LOC, protocol.py ~200 LOC,
server.py ~400 LOC). Total bundle: ~700 LOC, ~25 KB. Well within
shell argv limits.

The bundle is deterministic (same harness version → same bundle), so
unit tests can compare hashes.

### 2.3 Port forwarding

**DockerWorkspace**: start the container with `-P` (random ports
forwarded). The agent-server picks an ephemeral container-side port
on startup and prints it; the supervisor calls `docker port
<container> <port>` to learn the host-side mapping. Client targets
`http://127.0.0.1:<host-port>`.

**RemoteWorkspace**: use SSH local port forwarding via the existing
SSH session. Start the agent-server on the remote with an ephemeral
remote-side port; supervisor uses `ssh -L <host-port>:127.0.0.1:<remote-port>`
to tunnel. Client targets `http://127.0.0.1:<host-port>`.

In both cases the wire stays loopback from the client's view — no
need for TLS in Phase 33.

### 2.4 Fallback: keep `docker exec` / `ssh exec` as backup

**Default behaviour** (Phase 33.1 onward):

- `DockerWorkspace(use_agent_server=True)` is the new default.
- If agent-server startup fails (image missing python3, port forward
  fails, server doesn't respond within 5 seconds), the backend logs
  a warning and **falls back to the old `docker exec` per-op mode**.
- Users who explicitly want the old behaviour pass `use_agent_server=False`.

This preserves backward compatibility — existing CI / scripts that
rely on `docker exec`-only behaviour keep working. The fallback path
is tested.

---

## 3. Component shape

```
app/tools/agent_server/
├── bundle.py            (new) — assembles standalone Python script

app/tools/docker_workspace.py
                         (modified) — new use_agent_server kwarg;
                         start_agent_server() helper; ops dispatch
                         via AgentServerClient when active; fallback
                         to docker exec on agent_server unavailable

app/tools/remote_workspace.py
                         (modified) — same as docker_workspace.py
                         but ssh-based startup + ssh -L tunnel

tests/test_agent_server_bundle.py
                         (new) — bundle round-trip: build + exec via
                         `python3 -c` + verify it serves health

tests/test_docker_workspace_agent.py
                         (new) — integration: DockerWorkspace with
                         use_agent_server=True drives a plan; skipif
                         no Docker

tests/test_remote_workspace_agent.py
                         (new) — same shape for RemoteWorkspace;
                         skipif no ssh-localhost
```

---

## 4. Test strategy

Three layers (Phase 31/32 pattern):

1. **Bundle unit tests** (`tests/test_agent_server_bundle.py`)
   - Hash-stable across runs
   - Includes every required symbol (`WorkspaceStat`, `sha256_file`,
     all protocol models, `AgentServer`)
   - When passed to `python3 -c` and invoked, starts a server that
     answers `/healthz`
   - Runs everywhere (just needs `python3` in PATH; we already need
     it for the test runner)

2. **DockerWorkspace integration tests** (`tests/test_docker_workspace_agent.py`)
   - `use_agent_server=True` → starts container + agent + forwards
     port + drives ops via HTTP client
   - `use_agent_server=True` + simulated failure → falls back to
     `docker exec`
   - skipif `_docker_available()` → no Docker, no tests
   - Same overall shape as the existing `tests/test_workspace_docker.py`

3. **RemoteWorkspace integration tests** (`tests/test_remote_workspace_agent.py`)
   - `use_agent_server=True` → starts agent via ssh + tunnel + drives
     ops via HTTP client
   - skipif no `ssh -o BatchMode=yes localhost true` → no SSH

Layer 1 carries the bulk; layers 2/3 are opportunistic on CI.

---

## 5. Slice plan

### Phase 33.0 — design doc (this file)

- Distribution strategy + bundle plan + port forwarding + fallback
- 0 code changes

### Phase 33.1 — bundle + DockerWorkspace integration

- `app/tools/agent_server/bundle.py` — emits self-contained Python source
- `app/tools/docker_workspace.py` — `use_agent_server` flag + bootstrap
- `tests/test_agent_server_bundle.py` — bundle unit tests
- `tests/test_docker_workspace_agent.py` — Docker+agent integration tests
- Smoke: `DockerWorkspace(use_agent_server=True).mkdir("foo")` exits
  with the directory present in the container

### Phase 33.2 — RemoteWorkspace integration

- `app/tools/remote_workspace.py` — same `use_agent_server` flag +
  ssh-based bootstrap with `ssh -L` tunnel
- `tests/test_remote_workspace_agent.py` — Remote+agent integration tests

### Phase 33.3 — docs + ledger + commit

- Update `docs/DOCKER_WORKSPACE.md` — agent-server mode section
  (defaults, opt-out, perf numbers)
- Update `docs/REMOTE_WORKSPACE.md` — same
- `docs/PHASES.md` ledger row for Phase 33
- `CHANGELOG.md` v0.31.0 entry
- `README.md` perf footnote update
- `CLAUDE.md` §5 Phase 33 → done, Phase 34+ candidates update

### Phase 33 done = green CI

- All slices committed
- ruff + format + pytest green on all matrix legs
- New tests pass; old tests unaffected
- Tag candidate v0.31.0 cuts when ready

---

## 6. §10.7 ledger

**0 kernel touches.** Phase 33 modifies `app/tools/docker_workspace.py`
+ `app/tools/remote_workspace.py` (existing application-layer files)
and adds `app/tools/agent_server/bundle.py`. Zero imports from
`app/harness/` or `app/schemas/`. Phase 30.2 kernel boundary lint
stays green.

Ledger row: 0 kernel touch; ledger now reads 4 deliberate / 40
deliveries / 36 zero-kernel-touch (after Phase 33 lands).

---

## 7. Honesty discipline (rule F)

Per CLAUDE.md rule F, the perf numbers shipped in user docs must be
**measured**, not aspirational. Phase 33.3 must include actual
benchmark output for one canonical plan:

```bash
# Workspace pack (38 actions) on python:3.12-slim
$ time localflow execute T --workspace docker:python:3.12-slim  # exec-per-op
$ time localflow execute T --workspace docker:python:3.12-slim --use-agent-server
```

The expected delta is "10× on hot paths" — but if it turns out
"3× on the realistic plan", the docs say 3×. No marketing claims.

---

## 8. Future direction this unlocks

Phase 33 closes the Workspace facade story:

| Phase | Step | Status |
| --- | --- | --- |
| 33.0 | Design doc                                  | ✅ shipping |
| 33.1 | DockerWorkspace agent-server integration    | pending |
| 33.2 | RemoteWorkspace agent-server integration    | pending |
| 33.3 | Docs + ledger + benchmark                   | pending |
| 34 (candidate) | Keep-alive HTTP client / Unix socket transport | not committed |
| 34.x (candidate) | TLS + multi-tenancy for bare-network deploys | not committed |
| 35 (candidate) | Physical relocation of `app/harness/*` → `localflow_kernel/` | not committed |
| 35.x (candidate) | PyPI distribution of `localflow_kernel`   | not committed |

Each unlocks once evidence (benchmark, integration ask, downstream
consumer) makes it the obvious step.
