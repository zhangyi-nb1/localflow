# Phase 32 — HTTP agent-server (protocol + server + client skeleton)

**Status**: design locked 2026-05-28, three-slice spike
**Predecessor**: Phase 31 (RemoteWorkspace via SSH) shipped 2026-05-27
**Tracking goal alignment**: README's "Workspace facade decoupled from
the kernel" — agent-server is the perf upgrade path that benefits BOTH
DockerWorkspace + RemoteWorkspace simultaneously.

---

## 1. Why now

DockerWorkspace and RemoteWorkspace share the same perf ceiling: each
operation shells out one command (`docker exec` or `ssh`) over a fresh
session. Measured cost: ~100-300 ms/op. For a plan with 38 actions
(typical Workspace pack run), that's 4-12 seconds of pure RPC overhead
on top of the LLM round-trips.

The fix is a **long-lived agent process** inside the container / on the
remote machine that speaks HTTP. The harness opens ONE connection and
reuses it for the entire run. Per-op latency drops to network RTT
(~1-5 ms on localhost, ~10-50 ms over LAN). This is the OpenHands /
runtime-API pattern.

Phase 32 ships the **building blocks** — protocol + server + client.
Wiring it into Docker (replace `docker exec` per op) + Remote (ssh
tunnel to the agent) is deferred to Phase 33 because each integration
needs its own packaging story (how to ship the agent binary into the
container / onto the remote).

---

## 2. Protocol design

### 2.1 Transport: HTTP/1.1 + JSON

| Choice | Rationale |
|---|---|
| **HTTP over TCP** | Universal client support, plays well with TLS / proxies / port-forwards, debuggable with curl |
| **JSON body** | Pydantic models on both sides, base64-encoded bytes for binary payloads |
| **No gRPC / WebSocket / Cap'n Proto** | Adds a build-system dep + protoc step + bumped first-run cost. The kernel has zero non-stdlib server dependencies today; we keep it that way |
| **stdlib `http.server` (server side)** | Zero new deps; multi-threaded; sufficient for 1 client / N requests; trivial to swap to FastAPI in Phase 34+ if eval / multi-tenancy demands it |
| **stdlib `urllib.request` (client side)** | Zero new deps; consistent with the project's "no httpx" rule (`pyproject.toml` already pins minimal deps) |

We are **NOT** building a generic JSON-RPC server. The endpoint set is
the closed Workspace Protocol surface; the server validates each
request against an explicit Pydantic schema.

### 2.2 Auth: shared-secret bearer token

```
GET /sha256?path=note.md HTTP/1.1
Host: 127.0.0.1:8765
Authorization: Bearer <random-64-hex>
```

- Server generates a fresh 256-bit token on startup (`secrets.token_hex(32)`).
- Token is written to stdout in a well-known marker (`AGENT_SERVER_TOKEN=...`)
  so the supervising process (Phase 33 docker/ssh wiring) can pipe it
  back to the client.
- Every endpoint requires the header. Missing / wrong → `401`.
- The token never appears in URL query strings, log lines, or stack
  traces. It's compared via `secrets.compare_digest`.

This is a **soft** boundary — the agent runs as the host user inside
the container / on the remote, so a process that can read `/proc` can
steal the token. We don't pretend otherwise. The point is to defend
against a third party on the same network making blind requests. The
actual isolation guarantee comes from Docker (container boundary) or
SSH (network access control), not from this token.

### 2.3 Endpoints

Mirror of the Workspace Protocol:

| Method + Path | Request body | Response body | Notes |
|---|---|---|---|
| `GET /healthz` | — | `{"status": "ok", "version": "..."}` | Liveness probe; no auth |
| `GET /workspace_root` | — | `{"root": "/workspace"}` | Where the server is rooted |
| `POST /exists` | `{"path": "..."}` | `{"exists": bool}` | |
| `POST /stat` | `{"path": "..."}` | `{"stat": {...} \| null}` | `WorkspaceStat` Pydantic dump |
| `POST /sha256` | `{"path": "..."}` | `{"sha256": "..." \| null}` | |
| `POST /list_dir` | `{"path": ""}` | `{"entries": ["..."]}` | Sorted client-side |
| `POST /read_bytes` | `{"path": "..."}` | `{"content_b64": "..."}` | base64-encoded |
| `POST /mkdir` | `{"path": "..."}` | `{"created": bool}` | False on idempotent re-create |
| `POST /move` | `{"src": "...", "dst": "..."}` | `{"path": "/workspace/..."}` | |
| `POST /copy` | `{"src": "...", "dst": "..."}` | `{"path": "/workspace/..."}` | |
| `POST /write_bytes` | `{"path": "...", "content_b64": "..."}` | `{"path": "/workspace/..."}` | |
| `POST /safe_target` | `{"path": "..."}` | `{"path": "..."}` | Auto-suffix on collision |

`read_text` / `write_text` are client-side encode/decode; the wire
protocol is bytes-only.

### 2.4 Path defence

The same `_validate_rel_path` rule as DockerWorkspace + RemoteWorkspace
applies — server rejects absolute paths, drive letters, `~`, `..`. The
rejection is `400 Bad Request` with `{"error": "..."}`.

This is the **server-side** mirror; clients SHOULD also pre-validate
to fail fast before the network round-trip, but the server is the
authority.

### 2.5 Error shape

All non-2xx responses share:

```json
{"error": "<short message>", "detail": "<optional context>"}
```

- `400` — path validation failure / bad request shape
- `401` — missing or wrong bearer token
- `404` — file not found (only when the spec says "raises FileNotFoundError")
- `500` — server-side I/O error; carries the OS error message

---

## 3. Component layout

```
app/tools/agent_server/
├── __init__.py          # public API: serve(), AgentServerError
├── protocol.py          # Pydantic request/response models
├── server.py            # stdlib http.server-based implementation
└── client.py            # urllib-based client, returns Pydantic models

app/tools/agent_server_workspace.py
                         # Workspace Protocol implementation that
                         # delegates to AgentServerClient

tests/test_agent_server_protocol.py
                         # Pydantic round-trip + path-defence unit tests
tests/test_agent_server_e2e.py
                         # Start a real server on ephemeral port,
                         # drive it from the client, assert end-to-end
                         # contract. Same shape as DockerWorkspace's
                         # container-actual tests, but uses localhost
                         # instead of a container.
```

Phase 32 ships everything above. Docker / Remote integration (Phase
33) goes on top: container-side bootstrap that copies the
`agent_server` package + `python -m app.tools.agent_server.server` and
forwards the chosen port back to the harness; SSH-side analogue.

---

## 4. Test strategy

Three layers (same shape as Phase 29 + 31):

1. **Protocol unit tests** (`tests/test_agent_server_protocol.py`)
   - Pydantic models: round-trip JSON ↔ object for every request /
     response type
   - `_validate_rel_path` (server-side) — exact same 9 cases as
     RemoteWorkspace's path defence
   - Auth-token compare — wrong token → 401, missing header → 401,
     correct token → pass-through
   - These run without spawning any process.

2. **Server unit tests** (`tests/test_agent_server_e2e.py::TestServer`)
   - Start the server on `127.0.0.1:0` (ephemeral port), point client
     at it, drive every endpoint, assert the on-disk effect inside a
     tmp workspace_root. ~15-20 tests.

3. **AgentServerWorkspace contract tests**
   (`tests/test_agent_server_e2e.py::TestAgentServerWorkspace`)
   - Same shape as `test_workspace_local.py` — wire the
     `AgentServerWorkspace` into a real `Executor` and run an actual
     `ActionPlan`. Proves the abstraction is genuinely drop-in.
   - ~5 tests; runs everywhere (no docker / no ssh).

Total: 30-40 new tests. All run on every CI matrix leg.

---

## 5. §10.7 ledger

**0 kernel touches.** `app/tools/agent_server/` and
`app/tools/agent_server_workspace.py` live in the tools layer; they
implement the existing Workspace Protocol from Phase 28 and never
import from `app/harness/` or `app/schemas/` (except for re-using
`WorkspaceStat` from `app/tools/workspace.py`, which is already
kernel-tier).

The Phase 30.2 kernel boundary lint (`tests/test_kernel_boundary.py`)
stays green.

Ledger row: zero kernel touches.

---

## 6. Slice plan

### Phase 32.0 — design doc (this file)

- Protocol design + auth + endpoint table
- Component layout + test strategy
- 0 code changes

### Phase 32.1 — protocol + server

- `app/tools/agent_server/protocol.py` — Pydantic models
- `app/tools/agent_server/server.py` — stdlib http.server impl
- `tests/test_agent_server_protocol.py` — unit tests
- Smoke: server starts on ephemeral port, healthz returns 200

### Phase 32.2 — client + AgentServerWorkspace

- `app/tools/agent_server/client.py` — urllib client returning
  Pydantic models
- `app/tools/agent_server_workspace.py` — Workspace Protocol impl
  that delegates to the client
- `tests/test_agent_server_e2e.py` — end-to-end: real server +
  client + Executor

### Phase 32.3 — docs + ledger + commit

- `docs/AGENT_SERVER.md` user manual — how to embed the server, how
  the protocol looks, what's coming in Phase 33
- `docs/PHASES.md` ledger row
- README pointer
- CLAUDE.md Phase 33+ candidate list update

### Phase 32 done = green CI

- All slices committed
- ruff + format + pytest green on all matrix legs
- 30-40 new tests passing
- Tag candidate v0.30.0 cuts when ready

---

## 7. What Phase 32 does NOT do (deferred to Phase 33+)

- **Docker integration** — copying the agent_server package into the
  container + starting it + forwarding the port. Needs a packaging
  decision: bake into the image vs `docker cp` vs `--mount`.
- **SSH integration** — `scp` the agent up + start it over ssh +
  tunnel a TCP port back. Needs a per-host bootstrap story.
- **TLS** — current token-based auth is fine for loopback (Docker)
  and SSH-tunnelled connections; bare TCP without TLS needs at
  least mutual auth before exposing to a real network.
- **Persistent connections / keepalive tuning** — stdlib http.server
  with `Connection: keep-alive` should suffice; benchmark before
  optimising.
- **Multi-tenancy** — current server assumes 1 client at a time.
  Multi-tenant would need per-token workspaces.

Each defers behind concrete evidence (a perf benchmark showing
keepalive isn't enough; an integration ask).
