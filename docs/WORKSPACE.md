# Workspace abstraction (Phase 28)

> Status: shipping in v0.26.0. **Not** a kernel exception — workspace
> is application-layer plumbing. ``policy_guard`` remains the sole
> path-traversal authority; rollback / trace / verifier wiring is
> untouched.

## What it is

Every filesystem write the LocalFlow kernel performs — MKDIR, MOVE,
COPY, RENAME, INDEX, SUMMARIZE, FETCH — now flows through a single
typed interface called ``Workspace``. The interface is declared as a
``runtime_checkable`` ``Protocol`` in
[`app/tools/workspace.py`](../app/tools/workspace.py); the default
implementation ``LocalWorkspace`` runs against the host filesystem,
delegating to the existing ``app.tools.file_ops`` helpers under the
hood.

The motivation isn't local performance. It's a hinge: with the seam
in place, the only thing that has to change to run plans in a Docker
container or against a remote host is **the Workspace implementation
the executor was constructed with**. Everything else — dispatch,
rollback, verifier, trace — keeps working unchanged.

## The contract (read methods)

```python
class Workspace(Protocol):
    @property
    def root(self) -> Path: ...
    def is_local(self) -> bool: ...

    # Reads
    def exists(self, rel_path: str) -> bool: ...
    def stat(self, rel_path: str) -> WorkspaceStat | None: ...
    def sha256(self, rel_path: str) -> str | None: ...
    def list_dir(self, rel_path: str = "") -> list[str]: ...
    def read_bytes(self, rel_path: str) -> bytes: ...
    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str: ...
```

All paths are **workspace-relative**: forward slashes, no ``..``, no
leading ``/``, no drive prefix. Implementations resolve them through
``policy_guard.resolve_inside`` before touching disk so a misbehaving
caller hits the same gate the rest of the harness already enforces.

``stat()`` returns a small typed bundle:

```python
@dataclass(frozen=True)
class WorkspaceStat:
    rel_path: str
    size_bytes: int
    is_file: bool
    is_dir: bool
```

— mirroring the subset of ``os.stat`` the harness actually consumes.
Full ``stat`` semantics are platform-dependent and noisy; the
abstraction stays narrow.

## The contract (write methods)

```python
    def mkdir(self, rel_path: str) -> bool: ...         # True iff a new dir was created
    def move(self, src_rel: str, dst_rel: str) -> Path: ...
    def copy(self, src_rel: str, dst_rel: str) -> Path: ...
    def rename(self, src_rel: str, dst_rel: str) -> Path: ...
    def write_text(self, rel_path: str, content: str) -> Path: ...
    def write_bytes(self, rel_path: str, content: bytes) -> Path: ...
    def safe_target_rel(self, rel_path: str) -> str: ...   # auto-suffix to avoid collision
```

The returned ``Path`` is the host-side absolute path the
implementation wrote to. Callers should NOT rely on it being on the
same filesystem the caller sits on — for ``DockerWorkspace`` or
``RemoteWorkspace`` it will be a container/remote path. Use it only
for log / display strings, not for follow-up filesystem calls.

``safe_target_rel`` is the auto-suffix-on-collision contract MOVE /
COPY / INDEX use to never silently overwrite. ``foo.txt`` → ``foo.txt``
if free, else ``foo (1).txt`` / ``foo (2).txt`` / …

## How the executor uses it

The ``Executor`` constructor accepts an optional ``workspace`` kwarg:

```python
from pathlib import Path
from app.harness.executor import Executor
from app.tools.workspace import LocalWorkspace

ws = LocalWorkspace(Path("./my_workspace"))
ex = Executor(workspace_root=Path("./my_workspace"), run_store=..., workspace=ws)
```

When ``workspace`` is omitted, the executor constructs
``LocalWorkspace(workspace_root)`` automatically — every existing
caller (control_loop, tests, taskgraph_runner) sees zero behaviour
change. Test code uses the injection point to plug in a
``SpyWorkspace`` that records every call without touching real disk
(see [`tests/test_executor_workspace_injection.py`](../tests/test_executor_workspace_injection.py)
for the template).

Phase 28.2 migrated every dispatch site:

| Dispatch | Old | New |
|---|---|---|
| `_do_mkdir` | `file_ops.mkdir(target_abs)` | `self.workspace.mkdir(target_rel)` |
| `_do_move` | `file_ops.move(src_abs, tgt_abs)` | `self.workspace.move(src_rel, tgt_rel)` |
| `_do_copy` | `file_ops.copy(src_abs, tgt_abs)` | `self.workspace.copy(src_rel, tgt_rel)` |
| `_do_index` (text/binary write) | `file_ops.write_text/_bytes(abs, ...)` | `self.workspace.write_text/_bytes(rel, ...)` |
| `_do_fetch` (HTTPS download) | `file_ops.write_bytes(abs, payload)` | `self.workspace.write_bytes(rel, payload)` |

The OVERWRITE-with-backup path inside `_do_index` still calls
``shutil.move`` directly to relocate the existing file into
``run_store.backups_dir`` — that destination lives outside the user
workspace, so it isn't a Workspace concern.

## What does NOT change

- ``policy_guard.resolve_inside`` is still the sole authority on
  "this path is inside the workspace". LocalWorkspace calls it
  before every disk-touching method; future DockerWorkspace will
  call its container-side equivalent.
- ``rollback.RollbackManifest`` is still appended to from the
  executor's per-action dispatch sites. Workspace doesn't touch the
  manifest.
- ``TraceLogger`` emission sites are untouched. Workspace is silent;
  the executor decides what to log.
- The eight iron rules — including "the kernel is the only code
  allowed to touch the user's disk" — still hold. The Workspace
  facade IS the kernel's disk-touching surface; placing it behind a
  Protocol doesn't relax the rule.

## Adding a new implementation

Phase 29 will add ``DockerWorkspace`` (container-mounted root + HTTP
shim to a sidecar agent-server). Phase 30 candidate is
``RemoteWorkspace`` (pure HTTP / SSH). The recipe for any new
implementation:

1. Class implementing every method of the ``Workspace`` Protocol.
   ``runtime_checkable`` means ``isinstance(my_impl, Workspace)`` is
   the smoke test.
2. Parameterise [`tests/test_workspace_local.py`](../tests/test_workspace_local.py)
   across the new implementation. The fixture is intentionally the
   only knob — the assertions are environment-agnostic.
3. Reject path-traversal at the entry of every write method (via
   ``policy_guard.resolve_inside`` for local, container-side
   equivalent for remote).
4. ``is_local()`` returns False for non-local backends — features
   that need fast local IO (PDF text extraction, etc.) gate on this.
5. Wire the implementation into either the executor constructor
   (one-shot test) or a Recipe-level setting (production opt-in).

## Reference

- [docs/PHASE_28_DESIGN.md](PHASE_28_DESIGN.md) — full design + slice
  breakdown.
- [docs/research/OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md)
  §A4 + §C3 — the source-evidence study that informed the design.
- [`app/tools/workspace.py`](../app/tools/workspace.py) — Protocol +
  LocalWorkspace implementation.
- [`tests/test_workspace_local.py`](../tests/test_workspace_local.py)
  — 27-test contract suite, the template for future-impl coverage.
