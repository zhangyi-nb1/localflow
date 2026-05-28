# `AgentServer` — long-lived HTTP backend for the Workspace Protocol

**Status**: shipped Phase 32 (v0.30.0)
**Audience**: developers wiring LocalFlow against a long-lived runtime
(in-container daemon, ssh-tunnelled remote agent, future LAN service).

`AgentServer` is the building block for a perf-optimised Workspace
backend. The existing `DockerWorkspace` and `RemoteWorkspace` shell
out one command (`docker exec` / `ssh`) per Workspace op — that's
~100-300 ms per call. With an `AgentServer` running long-lived in
the container / on the remote, the harness opens one HTTP connection
and reuses it for the entire run, dropping per-op latency to
network RTT (~1-5 ms on localhost, ~10-50 ms over LAN).

Phase 32 ships the **building blocks** — protocol + server + client +
the `AgentServerWorkspace` adapter. Wiring it into DockerWorkspace
and RemoteWorkspace (so they get the upgrade) is Phase 33.

---

## TL;DR

```python
from pathlib import Path
from app.tools.agent_server import AgentServer, AgentServerClient
from app.tools.agent_server_workspace import AgentServerWorkspace

with AgentServer(workspace_root=Path("/wkspc")) as server:
    client = AgentServerClient(base_url=server.base_url, token=server.token)
    ws = AgentServerWorkspace(client=client)
    ws.mkdir("sub/")
    ws.write_text("note.md", "hi")
    print(ws.read_text("note.md"))
```

The `with` block:

1. Binds the server to `127.0.0.1:<ephemeral-port>`.
2. Generates a fresh 256-bit bearer token.
3. Spawns a daemon thread that serves HTTP requests.
4. On exit, shuts the server down + joins the thread.

---

## Protocol overview

### Endpoints (all under `http://127.0.0.1:<port>`)

| Method + Path | Auth | Body | Returns |
|---|---|---|---|
| `GET /healthz`         | none   | —                       | `{"status":"ok","version":"..."}` |
| `GET /workspace_root`  | bearer | —                       | `{"root":"..."}` |
| `POST /exists`         | bearer | `{"path":"..."}`        | `{"exists":bool}` |
| `POST /stat`           | bearer | `{"path":"..."}`        | `{"stat":{...}\|null}` |
| `POST /sha256`         | bearer | `{"path":"..."}`        | `{"sha256":"hex"\|null}` |
| `POST /list_dir`       | bearer | `{"path":""}`           | `{"entries":["..."]}` |
| `POST /read_bytes`     | bearer | `{"path":"..."}`        | `{"content_b64":"..."}` |
| `POST /mkdir`          | bearer | `{"path":"..."}`        | `{"created":bool}` |
| `POST /move`           | bearer | `{"src":"...","dst":"..."}` | `{"path":"..."}` |
| `POST /copy`           | bearer | `{"src":"...","dst":"..."}` | `{"path":"..."}` |
| `POST /write_bytes`    | bearer | `{"path":"...","content_b64":"..."}` | `{"path":"..."}` |
| `POST /safe_target`    | bearer | `{"path":"..."}`        | `{"path":"..."}` |

### Auth — shared-secret bearer token

```
Authorization: Bearer <64-hex-token>
```

- Server generates one fresh token per process (`secrets.token_hex(32)`).
- Token is exposed via `server.token` (in-process) or printed to stdout
  as `AGENT_SERVER_TOKEN=...` when run via `python -m
  app.tools.agent_server.server` (supervised mode for Phase 33+
  Docker / SSH bootstrap).
- Wrong / missing token → `401 Unauthorized`.
- Compared via `secrets.compare_digest` so timing leaks the token's
  length only, never its bytes.

**This is a soft boundary.** A process that can read the server's
memory can steal the token. The real isolation comes from Docker
(container boundary) or SSH (network access control). The token
defends against blind requests from third parties on the same
network, NOT against the supervising process itself.

### Path defence

Every endpoint that accepts a `path` field validates it (`absolute`,
`~`, drive letter, `..` all rejected → `400 Bad Request`). The
defence is the **server-side mirror** of the same defence in
`DockerWorkspace` / `RemoteWorkspace` — clients SHOULD pre-validate
to fail fast, but the server is the authority.

### Error shape

All non-2xx responses share:

```json
{"error":"<short message>", "detail":"<optional context>"}
```

The `AgentServerClient` wraps these in `AgentServerError(message, status, body)`.

---

## Components

```
app/tools/agent_server/
├── __init__.py          # facade re-exporting AgentServer, AgentServerClient, models
├── protocol.py          # Pydantic request/response models + validate_rel_path
├── server.py            # AgentServer (stdlib http.server based)
└── client.py            # AgentServerClient (urllib based)

app/tools/agent_server_workspace.py
                         # Workspace Protocol implementation that
                         # delegates to AgentServerClient
```

Zero non-stdlib dependencies. Server is a `ThreadingHTTPServer` —
one daemon thread per request. Client is `urllib.request`. Both
share `protocol.py` so a type drift between them is a Pydantic
import-time / validation-time error.

---

## Lifecycle modes

### A — in-process (Phase 32, today)

```python
with AgentServer(workspace_root=Path("/wkspc")) as server:
    client = AgentServerClient(base_url=server.base_url, token=server.token)
    ws = AgentServerWorkspace(client=client)
    ...
```

Useful for **tests**, integration demos, and as the reference
implementation for the protocol.

### B — module-as-entrypoint (Phase 32, supervised)

```bash
AGENT_SERVER_WORKSPACE=/workspace AGENT_SERVER_PORT=8765 \
    python -m app.tools.agent_server.server
```

Prints to stdout:

```
AGENT_SERVER_PORT=8765
AGENT_SERVER_TOKEN=<hex>
AGENT_SERVER_WORKSPACE=/workspace
```

The supervising process (Phase 33: DockerWorkspace startup, ssh
forward) reads those lines, hands the port + token to the client,
and forwards/tunnels the TCP port back to the harness.

`SIGTERM` / `SIGINT` trigger a graceful shutdown.

### C — Docker / SSH integration (Phase 33)

Not yet shipped. Phase 33 will:

* DockerWorkspace startup: install agent_server into the container,
  start it as background process, forward the bound port back to the
  host. Per-op latency: 1-5 ms (RTT to localhost-forwarded port).
* RemoteWorkspace startup: scp the agent_server package, start it
  over ssh, tunnel its port via `ssh -L`. Per-op latency: 10-50 ms.

Both backends will keep their current exec-per-op fallbacks for
operators who prefer simplicity.

---

## §10.7 boundary

Both `app/tools/agent_server/` and `app/tools/agent_server_workspace.py`
live in the **tools layer**:

- Zero imports from `app/harness/`
- Zero imports from `app/schemas/`
- Only `WorkspaceStat` re-used from `app/tools/workspace.py` (the
  kernel-tier Protocol surface)

Phase 30.2's kernel boundary lint (`tests/test_kernel_boundary.py`)
stays green. The agent-server is an application-layer tool that
plugs into the Workspace Protocol — same tier as `DockerWorkspace`
and `RemoteWorkspace`.

---

## Trade-offs (read this before shipping to production)

| Property | Local | Docker | Remote (SSH) | AgentServer (Phase 32+) |
|---|---|---|---|---|
| Per-op latency | ~10 μs | ~100-300 ms | ~100-300 ms + RTT | ~1-5 ms localhost / 10-50 ms LAN |
| Setup cost | none | image pull | key auth setup | server bootstrap + token exchange |
| Isolation | none | container | network | depends on transport |
| Persistence | persistent | wiped | persistent | depends on host |
| Concurrency | single | single | single | single-tenant (token) |
| TLS | n/a | n/a | n/a | not yet — assumes loopback / SSH tunnel |

Phase 32 ships single-tenant + plain HTTP. Both are intentional:

- **Single-tenant**: one token, one workspace, one client. Multi-
  tenant would need per-token workspaces + a session manager;
  defer until evidence (a multi-tenant ask) makes it the next
  step.
- **Plain HTTP**: safe over loopback (Docker port-forward) and
  inside SSH tunnels (Remote). Phase 34+ candidate adds TLS or
  Unix-domain-socket transport for bare-network deployments.

---

## Troubleshooting

### `AgentServerError: missing bearer token`

Add `Authorization: Bearer <token>` to your request. The token is
on the server's `.token` attribute, or in the stdout banner when
run via `python -m`.

### `AgentServerError: invalid path: absolute or home-shorthand path not allowed`

The server rejected your path because it's absolute / contains
`~` / starts with a drive letter / includes `..`. Use a workspace-
relative path (e.g. `sub/file.md`, not `/wkspc/sub/file.md`).

### `AgentServerError: network error: Connection refused`

The server isn't running, or you pointed the client at the wrong
port. Verify with:

```bash
curl http://127.0.0.1:<port>/healthz
```

### Performance is bad

Phase 32's `urllib.request` opens a fresh TCP connection per call —
~1ms localhost, but adds up over hundreds of ops. If this becomes
the bottleneck, swap urllib for an HTTP library that keeps the
connection alive (Phase 34+ candidate).

---

## Future direction

| Phase | Step | Status |
| --- | --- | --- |
| 32.0 | Design doc                                  | ✅ shipped 2026-05-28 |
| 32.1 | Protocol + server                           | ✅ shipped 2026-05-28 |
| 32.2 | Client + AgentServerWorkspace               | ✅ shipped 2026-05-28 |
| 32.3 | User docs + ledger                          | ✅ shipped 2026-05-28 |
| 33 (candidate) | DockerWorkspace integration — agent-server in container, port forwarded back | not committed |
| 33.x (candidate) | RemoteWorkspace integration — agent-server on remote, ssh tunnel | not committed |
| 34 (candidate) | Keep-alive HTTP client / Unix-domain-socket transport | not committed |
| 34.x (candidate) | TLS + mutual auth for bare-network deployments | not committed |

Each unlocks when evidence (a perf benchmark, an integration ask)
makes it the obvious next step.
