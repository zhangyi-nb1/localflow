# DockerWorkspace (Phase 29)

> Status: shipping in v0.27.0. **Not** a kernel exception — Phase 28
> already added `Workspace` as an injection seam; DockerWorkspace is
> just another implementation. `policy_guard.resolve_inside` remains
> the path-traversal authority; rollback / trace / verifier wiring
> is untouched.

## What it is

A Phase 28 `Workspace` Protocol implementation that runs the **user
workspace inside a Docker container** and routes every filesystem
operation through `docker exec`. The container's filesystem is
isolated from the host — **no bind mount by default** — so a plan
that does something unexpected can't reach your real files. This is
the strong-isolation backend Phase 23's `PYTHON_COMPUTE` promise
("isolation, best-effort") always pointed toward.

## When to use it

- Running a plan you didn't author yourself
- Demoing LocalFlow against a workspace you don't fully trust the LLM with
- CI / eval runs that need fresh, reproducible state
- Scripts / `PYTHON_COMPUTE` actions whose blast radius you want to bound

## When NOT to use it

- Day-to-day local automation against your own files — LocalWorkspace
  is faster and the host filesystem IS the workspace by definition
- Workflows that need the host's installed software (only the
  container's image's tools are reachable)
- Performance-sensitive workloads — each fs op costs one `docker exec`
  round-trip (~100-300 ms)

## Turning it on

Two opt-in paths:

### 1. CLI per-run

```bash
localflow execute --task-id <id> --yes --workspace docker:python:3.12-slim
```

First-time pulls the image (~50 MB for `python:3.12-slim`); cached
afterwards. The first line of output reads:

```
workspace=docker:python:3.12-slim  (workspace runs inside a Docker container; see docs/DOCKER_WORKSPACE.md)
```

The container is created, the plan runs against it, then the container
is removed at the end of execute — even on exception. **Nothing
persists on the host** (no bind mount; outputs you need to keep
require a separate promote stage to a LocalWorkspace, mirror of how
Phase 23 promotes scratch artefacts to the workspace).

### 2. Python API

```python
from app.tools.docker_workspace import DockerWorkspace
from app.harness.executor import Executor

with DockerWorkspace(image="python:3.12-slim") as ws:
    ex = Executor(
        workspace_root=Path("./local-workspace-root"),  # used only for resolve_inside metadata
        run_store=...,
        workspace=ws,                                    # the real fs backend
    )
    outcome = ex.execute(plan, approved=True)
```

The context-manager handles `start()` / `close()`; equivalent to the
CLI's automatic lifecycle.

## How it works

1. `start()` calls `docker pull <image>` (idempotent — cached after
   first run) then `docker run -d --name <unique> --workdir /workspace
   <image> sh -c "mkdir -p /workspace && sleep infinity"`. The
   container stays alive, idle, until `close()`.
2. Every read / write method on the `Workspace` Protocol becomes one
   `docker exec <name> <coreutils-command>` invocation:
   `mkdir / mv / cp / cat / stat / sha256sum / ls`. No custom image
   required; everything is in `python:3.12-slim`'s coreutils.
3. `close()` runs `docker rm -f <name>` — even on exception, via the
   CLI's `try ... finally` wrap. Nothing leaks.

## Path-traversal defence

Every workspace-relative path passes through `_validate_rel_path` on
the **host side** BEFORE any `docker exec`. Rejects:

- Absolute paths (`/etc/passwd`)
- Home shorthand (`~/secrets`)
- Windows drive letters (`C:\Windows\cmd.exe`)
- UNC paths (`\\server\share`)
- Any `..` segment

Defence in depth: the host check stops the abuse at the API boundary;
even if a clever attacker crafted a path that got past, the container
runs in its own namespace with no host fs visibility.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `DockerUnavailable: Docker CLI / daemon not reachable` | Docker not installed / daemon not running | Install Docker Desktop / start `dockerd`; or omit `--workspace` to use LocalWorkspace |
| `failed to pull image '<image>'` | Network down, registry rate-limit, or typo in image name | Check `docker pull <image>` manually; common cause: typo or no auth for a private registry |
| `DockerWorkspaceError: docker exec timed out` | Container hung / daemon stuck | Increase `exec_timeout_sec` via Python API; CLI default 60s |
| Slow first run, fast subsequent runs | First-time image pull (~50 MB at network speed) | Pre-pull: `docker pull python:3.12-slim` |

The CLI exits with code 2 + a human-readable diagnostic on any
DockerUnavailable / start failure, so automation can detect "Docker
needed, not present" and either fall back or bail.

## Performance

Each `docker exec` round-trip is ~100-300 ms (subprocess fork + docker
client → daemon RPC → container exec → response parse). A 40-action
plan with mostly mkdir / move / index operations runs in 5-15 seconds
under DockerWorkspace, vs ~1 second under LocalWorkspace.

Phase 29.x can replace the per-op exec with an HTTP agent-server
running inside the container (mirroring OpenHands' `agent-server`
image) for ~10× speedup. Deferred until the latency actually bites.

## Limitations (deliberate)

- **No bind mount** — host workspace_root is NOT shared into the
  container. This is the isolation promise; opting into bind mount
  would defeat the point. If you want shared fs use LocalWorkspace.
- **No host file import** — to seed the container workspace with
  existing files, your plan must include explicit `INDEX` / `FETCH`
  / `WRITE` actions; nothing is "already there".
- **No multi-container orchestration** — one container per Executor
  instance. Multi-stage pipelines that need cross-container state
  must stage via host or use a single long-lived container.
- **Linux containers only** — the default image is a Linux image;
  Windows containers (different daemon mode) are not supported in
  v0.27.0.

## Reference

- [docs/PHASE_29_DESIGN.md](PHASE_29_DESIGN.md) — full design + slice
  breakdown + risks
- [docs/WORKSPACE.md](WORKSPACE.md) — Phase 28 abstraction this is
  built on
- [`app/tools/docker_workspace.py`](../app/tools/docker_workspace.py)
  — implementation
- [`tests/test_workspace_docker.py`](../tests/test_workspace_docker.py)
  — contract suite (skips when Docker unavailable; runs on CI Linux
  + Windows)
- [`tests/test_executor_docker_workspace.py`](../tests/test_executor_docker_workspace.py)
  — Executor injection integration tests
