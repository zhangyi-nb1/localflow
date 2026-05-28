"""Phase 28.0 — Workspace abstraction.

The harness currently couples file-system mutation directly to
``app.tools.file_ops`` — every action_type dispatch site builds its
own ``Path`` and calls ``shutil``. That works for the local case
but blocks the "swap to a Docker / Remote runtime" extension axis
that ``docs/research/OPENHANDS_HARNESS_STUDY.md`` §A4 + §C3 calls
out as the next move.

This module is the seam. ``Workspace`` is a Protocol; ``LocalWorkspace``
is the in-process implementation that delegates to the existing
``file_ops`` helpers. Phase 29 will add ``DockerWorkspace``; Phase 30
candidate is ``RemoteWorkspace``. All three honour the same path
contract (relative-only, no '..', resolved through
``policy_guard.resolve_inside``) so callers don't need to special-case
their target environment.

§10.7 invariant: this is application-layer plumbing. ``policy_guard``
is still the only path-traversal authority; the ``Workspace.*`` write
methods call ``resolve_inside`` before touching disk. The kernel's
trace + rollback + verifier wiring is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.harness.policy_guard import PolicyViolation, resolve_inside
from app.tools import file_ops
from app.tools.hash_ops import sha256_file


@dataclass(frozen=True)
class WorkspaceStat:
    """Small typed bundle for ``Workspace.stat()``. Mirrors the subset
    of ``os.stat`` semantics the harness actually uses — full ``stat()``
    semantics are platform-dependent and noisy."""

    rel_path: str
    size_bytes: int
    is_file: bool
    is_dir: bool


@runtime_checkable
class Workspace(Protocol):
    """File-system facade for every kernel write.

    All paths are workspace-relative (forward slashes, no ``..``, no
    drive prefix, no leading ``/``). Implementations resolve them
    through ``policy_guard.resolve_inside`` so a misbehaving caller
    is rejected at the same layer policy_guard already enforces.

    Implementations:
      * ``LocalWorkspace``  — direct host filesystem
      * ``DockerWorkspace`` — Phase 29 (deferred)
      * ``RemoteWorkspace`` — Phase 30 (deferred)
    """

    @property
    def root(self) -> Path:
        """Absolute path of the workspace root on the host the
        implementation operates against. ``LocalWorkspace.root`` is the
        user's directory; ``DockerWorkspace.root`` will be the
        container-side path. Tests should NOT assume ``root`` lives on
        the same filesystem as the caller."""

    def is_local(self) -> bool:
        """True iff ``root`` is reachable via the host's filesystem
        without going through an RPC. Used by features that need fast
        local IO (e.g. PDF text extraction)."""

    # ── reads ────────────────────────────────────────────────────────

    def exists(self, rel_path: str) -> bool: ...

    def stat(self, rel_path: str) -> WorkspaceStat | None: ...

    def sha256(self, rel_path: str) -> str | None: ...

    def list_dir(self, rel_path: str = "") -> list[str]:
        """Return sorted list of immediate children's basenames.
        Empty ``rel_path`` lists the workspace root."""

    def read_bytes(self, rel_path: str) -> bytes: ...

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str: ...

    # ── writes ───────────────────────────────────────────────────────

    def mkdir(self, rel_path: str) -> bool:
        """Create directory (parents=True, exist_ok=True). Returns True
        iff a new directory was created."""

    def move(self, src_rel: str, dst_rel: str) -> Path:
        """Move file or directory. Returns the resolved absolute target."""

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        """Copy file. Returns the resolved absolute target."""

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        """Same as ``move`` but the contract is that ``dst_rel`` lives
        in the same directory as ``src_rel``. Phase-1 implementations
        do not enforce that — the executor's dispatch layer already
        validates the shape."""

    def write_text(self, rel_path: str, content: str) -> Path: ...

    def write_bytes(self, rel_path: str, content: bytes) -> Path: ...

    def safe_target_rel(self, rel_path: str) -> str:
        """Return a workspace-relative path that does NOT collide with
        an existing file. ``foo.txt`` → ``foo.txt`` if free, else
        ``foo (1).txt`` / ``foo (2).txt`` / ...

        Used by the executor's MOVE / COPY dispatch to auto-suffix
        instead of silently overwriting. Implementations MAY scope the
        existence check to a remote runtime."""


# --------------------------------------------------------------------- LocalWorkspace


class LocalWorkspace:
    """In-process Workspace implementation backed by the host
    filesystem. Wraps ``app.tools.file_ops`` so the existing kernel
    callers can migrate to the abstraction one site at a time with no
    behaviour change.

    Constructor takes the workspace root; every rel_path passed to a
    method gets validated through ``policy_guard.resolve_inside``
    before touching disk. A path that escapes the workspace raises
    ``PolicyViolation`` — the same exception type the existing
    callers already catch.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def is_local(self) -> bool:
        return True

    # ── reads ────────────────────────────────────────────────────────

    def _abs(self, rel_path: str) -> Path:
        """Resolve a relative path. Empty path = workspace root."""
        if not rel_path:
            return self._root
        return resolve_inside(self._root, rel_path)

    def exists(self, rel_path: str) -> bool:
        try:
            return self._abs(rel_path).exists()
        except PolicyViolation:
            return False

    def stat(self, rel_path: str) -> WorkspaceStat | None:
        try:
            abs_path = self._abs(rel_path)
        except PolicyViolation:
            return None
        if not abs_path.exists():
            return None
        st = abs_path.stat()
        return WorkspaceStat(
            rel_path=rel_path,
            size_bytes=st.st_size,
            is_file=abs_path.is_file(),
            is_dir=abs_path.is_dir(),
        )

    def sha256(self, rel_path: str) -> str | None:
        try:
            abs_path = self._abs(rel_path)
        except PolicyViolation:
            return None
        if not abs_path.is_file():
            return None
        return sha256_file(abs_path)

    def list_dir(self, rel_path: str = "") -> list[str]:
        abs_path = self._abs(rel_path)
        if not abs_path.is_dir():
            return []
        return sorted(p.name for p in abs_path.iterdir())

    def read_bytes(self, rel_path: str) -> bytes:
        return self._abs(rel_path).read_bytes()

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        return self._abs(rel_path).read_text(encoding=encoding)

    # ── writes ───────────────────────────────────────────────────────

    def mkdir(self, rel_path: str) -> bool:
        target = self._abs(rel_path)
        return file_ops.mkdir(target)

    def move(self, src_rel: str, dst_rel: str) -> Path:
        return file_ops.move(self._abs(src_rel), self._abs(dst_rel))

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        return file_ops.copy(self._abs(src_rel), self._abs(dst_rel))

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        return file_ops.rename(self._abs(src_rel), self._abs(dst_rel))

    def write_text(self, rel_path: str, content: str) -> Path:
        return file_ops.write_text(self._abs(rel_path), content)

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        return file_ops.write_bytes(self._abs(rel_path), content)

    def safe_target_rel(self, rel_path: str) -> str:
        """Auto-suffix a colliding rel_path. Delegates to
        ``file_ops.safe_target`` for the actual name logic, then
        converts the absolute result back to a workspace-relative
        path string. The relpath conversion is best-effort — if the
        result somehow falls outside the workspace (shouldn't happen
        on LocalWorkspace), the original ``rel_path`` is returned to
        let downstream validation reject it cleanly."""
        abs_path = self._abs(rel_path)
        chosen = file_ops.safe_target(abs_path)
        try:
            return chosen.relative_to(self._root).as_posix()
        except ValueError:
            return rel_path


# --------------------------------------------------------------------- factory


def parse_workspace_spec(spec: str, *, workspace_root: Path) -> "Workspace":
    """Phase 29.2 / 31.1 — turn a CLI / Recipe workspace string into a
    concrete ``Workspace`` instance.

    Supported specs:
      - ``"local"`` (default) → ``LocalWorkspace(workspace_root)``
      - ``"docker:<image>"`` → ``DockerWorkspace(image=<image>)``;
        caller is responsible for calling ``.start()`` / ``.close()``
        (the CLI wraps execution in a context manager).
      - ``"ssh:<host>[:<port>][:<root>]"`` → ``RemoteWorkspace(...)``.
        Grammar:
          * ``<host>`` — required; passed to ssh verbatim (so
            ``~/.ssh/config`` aliases work). May include user@host.
          * ``<port>`` — optional integer; defaults 22.
          * ``<root>`` — optional absolute path on the remote;
            defaults ``/tmp/localflow-ws``. Must start with ``/`` so
            the parser can disambiguate it from ``<port>``.

    Raises ``ValueError`` on an unrecognised prefix or malformed
    ssh spec so the CLI can surface a clear error before any action
    runs.
    """
    if spec in ("", "local"):
        return LocalWorkspace(workspace_root)
    if spec.startswith("docker:"):
        from app.tools.docker_workspace import DockerWorkspace

        image = spec[len("docker:") :].strip()
        if not image:
            raise ValueError(
                "docker workspace spec missing image after 'docker:' — "
                "try 'docker:python:3.12-slim'"
            )
        return DockerWorkspace(image=image)
    if spec.startswith("ssh:"):
        from app.tools.remote_workspace import (
            DEFAULT_REMOTE_ROOT,
            DEFAULT_SSH_PORT,
            RemoteWorkspace,
        )

        body = spec[len("ssh:") :].strip()
        if not body:
            raise ValueError(
                "ssh workspace spec missing host after 'ssh:' — "
                "try 'ssh:user@example.com' or 'ssh:user@example.com:22:/srv/wkspc'"
            )
        host, port, remote_root = _parse_ssh_spec_body(body)
        return RemoteWorkspace(
            host=host,
            port=port or DEFAULT_SSH_PORT,
            workspace_root_remote=remote_root or DEFAULT_REMOTE_ROOT,
        )
    raise ValueError(
        f"unrecognised workspace spec {spec!r}; supported: 'local' (default), "
        "'docker:<image>', 'ssh:<host>[:<port>][:<root>]'"
    )


def _parse_ssh_spec_body(body: str) -> tuple[str, int | None, str | None]:
    """Parse ``<host>[:<port>][:<root>]`` (sans ``ssh:`` prefix).

    Grammar is right-to-left: any trailing segment starting with ``/``
    is the remote root; any other trailing integer segment is the
    port. Everything before is the host. This handles:

      - ``user@host``                 → (user@host, None, None)
      - ``host:2222``                 → (host, 2222, None)
      - ``host:/srv/ws``              → (host, None, /srv/ws)
      - ``host:2222:/srv/ws``         → (host, 2222, /srv/ws)
      - ``user@host:2222:/srv/ws``    → (user@host, 2222, /srv/ws)
    """
    remote_root: str | None = None
    port: int | None = None

    # 1. Trailing ``/<path>`` is the remote root.
    if ":/" in body:
        head, _, root_part = body.rpartition(":/")
        remote_root = "/" + root_part
        body = head

    # 2. Trailing integer after a colon is the port.
    if ":" in body:
        head, _, last = body.rpartition(":")
        try:
            port = int(last)
            body = head
        except ValueError:
            # last segment isn't an integer — it's part of the host
            # (e.g. an IPv6 literal). Leave body as-is.
            pass

    host = body.strip()
    if not host:
        raise ValueError(
            "ssh workspace spec missing host; try 'ssh:user@example.com' "
            "or 'ssh:user@example.com:22:/srv/wkspc'"
        )
    return host, port, remote_root
