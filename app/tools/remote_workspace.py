"""Phase 31.1 — RemoteWorkspace (SSH-backed).

A ``Workspace`` implementation that routes every filesystem operation
through ``ssh <host> -- sh -c '...'`` to a Linux box reachable over the
network. Isomorphic to ``DockerWorkspace``: same Workspace Protocol,
same exec-per-op shape, same path defence — only the command prefix
changes (``ssh ...`` instead of ``docker exec ...``).

Use cases:
  - Run plans on a beefier remote (build server, lab VM).
  - Drive a sandbox VM you spun up specifically for a risky workflow.
  - Test the Workspace abstraction against a third backend (closes
    Phase 28's "Phase 30 candidate is RemoteWorkspace" comment).

Trade-offs (documented honestly):
  - Latency: each operation costs one ssh round-trip (~100-300ms,
    same as DockerWorkspace). Acceptable for plan execution; HTTP
    agent-server (Phase 32 candidate) can lift this if it bites.
  - Auth: SSH config + key-based auth only. Password auth blocks on
    stdin and the harness will hang — explicit BatchMode=yes refuses
    interactive prompts.
  - Persistence: unlike DockerWorkspace, the remote directory is NOT
    auto-cleaned on ``close()``. The remote is user-managed.

§10.7 invariant: this is an application-layer Workspace implementation.
The host-side ``_validate_rel_path`` mirrors policy_guard's defence so
no kernel module is imported. Rollback / trace / verifier all run
through the same kernel surfaces with no special-casing.

Lifecycle:
  ws = RemoteWorkspace(host="user@example.com", port=22, root="/srv/wkspc")
  ws.start()         # ssh ... -- mkdir -p /srv/wkspc
  try:
      ws.mkdir("sub/")
      ws.write_text("note.md", "hi")
  finally:
      ws.close()      # no-op for the remote dir; releases ssh masters

Or context-manager: ``with RemoteWorkspace(...) as ws: ...``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.tools.workspace import WorkspaceStat

# Default remote workspace root. Lives under /tmp by default so the
# user's permanent home isn't littered; override via constructor for a
# durable location.
DEFAULT_REMOTE_ROOT = "/tmp/localflow-ws"

# Default ssh port (standard).
DEFAULT_SSH_PORT = 22

# Per-op exec timeout — defends against a hung remote process or stuck
# network blocking the harness forever.
DEFAULT_EXEC_TIMEOUT_SEC = 60

# Default SSH options. ``BatchMode=yes`` is critical — refuses any
# interactive prompt (password, host key acceptance) so we never hang
# silently. Users must set up ~/.ssh/known_hosts + key-based auth.
DEFAULT_SSH_OPTIONS: tuple[str, ...] = (
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=3",
)


class RemoteWorkspaceError(RuntimeError):
    """Raised when an ssh exec call fails. Carries stdout + stderr so
    callers can surface a useful diagnostic."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class RemoteUnavailable(RuntimeError):
    """Raised when the ssh CLI is missing or the remote refuses every
    BatchMode connection. Callers can fall back to LocalWorkspace, or
    surface the message to the user."""


def _ssh_available() -> bool:
    """Cheap probe — does ``ssh -V`` succeed? We can't check whether a
    specific host is reachable without trying it, but absence of the
    ssh binary is a fast fail."""
    try:
        result = subprocess.run(
            ["ssh", "-V"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# Same path defence as docker_workspace, reimplemented here so
# RemoteWorkspace doesn't have to import the kernel module (clean
# layer separation — kernel imports tools, not the other way).
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:[/\\]?")


def _validate_rel_path(rel_path: str) -> str:
    """Reject absolute / drive-letter / UNC / parent-traversal paths.

    Returns the normalised forward-slash path on success; raises
    ``RemoteWorkspaceError`` on rejection. Mirrors LocalWorkspace's
    ``resolve_inside`` defence but in a way that produces a string
    safe to interpolate into an ssh exec argument.
    """
    if rel_path is None or rel_path == "":
        return ""
    if rel_path.startswith(("/", "\\", "~")):
        raise RemoteWorkspaceError(f"absolute or home-shorthand path not allowed: {rel_path!r}")
    if _DRIVE_LETTER.match(rel_path):
        raise RemoteWorkspaceError(f"Windows drive-letter path not allowed: {rel_path!r}")
    normalised = rel_path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p]
    if any(p == ".." for p in parts):
        raise RemoteWorkspaceError(f"parent-directory traversal not allowed: {rel_path!r}")
    return "/".join(parts)


@dataclass
class RemoteWorkspace:
    """Workspace backed by an SSH-reachable Linux host.

    The remote workspace directory is created on ``start()`` (or the
    context-manager ``__enter__``) and left in place on ``close()``
    (the remote is user-managed; the workspace is a regular directory,
    not a container). Operations route to the remote via ``ssh``. The
    kernel sees the same Workspace Protocol it sees for LocalWorkspace.
    """

    host: str
    """SSH-resolvable host — ``user@hostname`` or a ~/.ssh/config alias."""

    port: int = DEFAULT_SSH_PORT
    workspace_root_remote: str = DEFAULT_REMOTE_ROOT
    """Absolute path on the remote machine. Must be writable by the
    ssh login user."""

    exec_timeout_sec: int = DEFAULT_EXEC_TIMEOUT_SEC
    ssh_options: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SSH_OPTIONS)
    _started: bool = False

    @classmethod
    def is_available(cls) -> bool:
        """Cheap probe — checks whether the ssh CLI is reachable. Does
        NOT verify a specific host is up; that happens lazily on
        ``start()``."""
        return _ssh_available()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Verify ssh works against the remote and ensure the
        workspace dir exists. Idempotent."""
        if self._started:
            return
        if not _ssh_available():
            raise RemoteUnavailable(
                "ssh CLI not reachable. Install OpenSSH client, or fall back "
                "to LocalWorkspace / DockerWorkspace."
            )

        # First call doubles as a connectivity probe: if BatchMode auth
        # fails or the network is unreachable, this raises with a useful
        # diagnostic. mkdir -p is idempotent — safe to re-run on every
        # start().
        cmd = ["mkdir", "-p", shlex.quote(self.workspace_root_remote)]
        # Phase 31.1 — flip _started BEFORE the exec call so the
        # in-method _exec dispatch passes _require_started. Mirrors
        # the Phase 29.0 fix in DockerWorkspace.start().
        self._started = True
        try:
            self._exec(cmd, allow_root_relative=True)
        except RemoteWorkspaceError as exc:
            # Roll back the flag so a retry can start fresh.
            self._started = False
            raise RemoteUnavailable(
                f"ssh probe to {self.host!r} failed: {exc}\n"
                "Check ~/.ssh/config, key-based auth, and that the host accepts BatchMode=yes."
            ) from exc

    def close(self) -> None:
        """Release ssh resources. The remote directory is NOT removed;
        the remote is user-managed."""
        # Currently a no-op; future control-master / connection sharing
        # cleanup hooks here.
        self._started = False

    def __enter__(self) -> "RemoteWorkspace":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ── Workspace Protocol: properties ───────────────────────────────

    @property
    def root(self) -> Path:
        """The REMOTE-side root, not a host path. Callers should treat
        it as opaque — for display only. RemoteWorkspace deliberately
        never exposes a host-equivalent because no such thing exists
        for an unmounted remote filesystem."""
        return Path(self.workspace_root_remote)

    def is_local(self) -> bool:
        return False

    # ── helpers ──────────────────────────────────────────────────────

    def _require_started(self) -> None:
        if not self._started:
            raise RemoteWorkspaceError(
                "RemoteWorkspace not started — call .start() or use the "
                "``with RemoteWorkspace(...) as ws:`` context manager."
            )

    def _remote_path(self, rel_path: str) -> str:
        """Compose the remote-side absolute path from a validated
        rel_path. Caller must call ``_validate_rel_path`` first."""
        if not rel_path:
            return self.workspace_root_remote
        return f"{self.workspace_root_remote}/{rel_path}"

    def _ssh_prefix(self) -> list[str]:
        """The argv prefix common to every ssh invocation."""
        argv = ["ssh", *self.ssh_options]
        if self.port != DEFAULT_SSH_PORT:
            argv.extend(["-p", str(self.port)])
        argv.append(self.host)
        # The ``--`` terminator defends against ``host`` accidentally
        # parsing as a flag.
        argv.append("--")
        return argv

    def _exec(
        self,
        cmd: list[str],
        *,
        stdin_bytes: bytes | None = None,
        check: bool = True,
        allow_root_relative: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``ssh <host> -- <cmd>``. Returns the CompletedProcess
        (with text-decoded stdout / stderr); raises RemoteWorkspaceError
        on non-zero return when ``check=True``.

        ``cmd`` is a list of remote argv tokens. They are joined with
        spaces and quoted appropriately for the remote sh — ssh forwards
        the argv as a single shell command on the remote.
        ``allow_root_relative`` is used by ``start()`` so the very first
        call (which creates the workspace root) doesn't require the
        directory to exist yet.
        """
        if not allow_root_relative:
            self._require_started()
        argv = self._ssh_prefix() + cmd
        try:
            result = subprocess.run(
                argv,
                input=stdin_bytes,
                capture_output=True,
                timeout=self.exec_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteWorkspaceError(f"ssh timed out: {' '.join(cmd)!r}") from exc

        stdout_text = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        if check and result.returncode != 0:
            raise RemoteWorkspaceError(
                f"ssh exec failed (rc={result.returncode}): {' '.join(cmd)!r}\n"
                f"stderr: {stderr_text.strip()}",
                stdout=stdout_text,
                stderr=stderr_text,
            )
        return subprocess.CompletedProcess(
            args=argv,
            returncode=result.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    def _exec_bytes(self, cmd: list[str]) -> bytes:
        """Variant of _exec that returns raw stdout bytes (for read_bytes)."""
        self._require_started()
        argv = self._ssh_prefix() + cmd
        try:
            result = subprocess.run(argv, capture_output=True, timeout=self.exec_timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise RemoteWorkspaceError(f"ssh timed out: {' '.join(cmd)!r}") from exc
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RemoteWorkspaceError(
                f"ssh exec failed (rc={result.returncode}): {' '.join(cmd)!r}\n"
                f"stderr: {stderr_text.strip()}",
                stderr=stderr_text,
            )
        return result.stdout or b""

    # ── Workspace Protocol: reads ────────────────────────────────────

    def exists(self, rel_path: str) -> bool:
        try:
            rel = _validate_rel_path(rel_path)
        except RemoteWorkspaceError:
            return False
        result = self._exec(["test", "-e", shlex.quote(self._remote_path(rel))], check=False)
        return result.returncode == 0

    def stat(self, rel_path: str) -> WorkspaceStat | None:
        try:
            rel = _validate_rel_path(rel_path)
        except RemoteWorkspaceError:
            return None
        path = shlex.quote(self._remote_path(rel))
        # GNU stat (Linux): "%s %F" → "<size> <kind>". Same shape as
        # DockerWorkspace.stat — coreutils is universal on Linux remotes.
        result = self._exec(["stat", "-c", "'%s %F'", path], check=False)
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        if not text:
            return None
        size_str, _, kind = text.partition(" ")
        try:
            size_bytes = int(size_str)
        except ValueError:
            return None
        return WorkspaceStat(
            rel_path=rel_path,
            size_bytes=size_bytes,
            is_file="regular" in kind,
            is_dir=kind == "directory",
        )

    def sha256(self, rel_path: str) -> str | None:
        try:
            rel = _validate_rel_path(rel_path)
        except RemoteWorkspaceError:
            return None
        path = shlex.quote(self._remote_path(rel))
        # Same pre-check as DockerWorkspace: only files have sha256.
        st = self.stat(rel_path)
        if st is None or not st.is_file:
            return None
        result = self._exec(["sha256sum", path], check=False)
        if result.returncode != 0:
            return None
        # sha256sum output: "<hex>  /path\n"
        return result.stdout.split()[0] if result.stdout else None

    def list_dir(self, rel_path: str = "") -> list[str]:
        try:
            rel = _validate_rel_path(rel_path)
        except RemoteWorkspaceError:
            return []
        path = shlex.quote(self._remote_path(rel))
        # ``ls -1A`` — one entry per line, include dot-files (exclude
        # "." and ".."). ``2>/dev/null`` suppresses error noise when
        # the dir doesn't exist; we fall through to returncode check.
        result = self._exec(["sh", "-c", f"'ls -1A {path} 2>/dev/null'"], check=False)
        if result.returncode != 0:
            return []
        return sorted(line for line in result.stdout.splitlines() if line)

    def read_bytes(self, rel_path: str) -> bytes:
        rel = _validate_rel_path(rel_path)
        return self._exec_bytes(["cat", shlex.quote(self._remote_path(rel))])

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(rel_path).decode(encoding)

    # ── Workspace Protocol: writes ───────────────────────────────────

    def mkdir(self, rel_path: str) -> bool:
        rel = _validate_rel_path(rel_path)
        path = self._remote_path(rel)
        # Pre-check existence to return False on idempotent re-create —
        # mirrors LocalWorkspace.mkdir contract.
        if self.exists(rel):
            return False
        self._exec(["mkdir", "-p", shlex.quote(path)])
        return True

    def move(self, src_rel: str, dst_rel: str) -> Path:
        src = _validate_rel_path(src_rel)
        dst = _validate_rel_path(dst_rel)
        dst_path = self._remote_path(dst)
        # Ensure parent exists (mirrors LocalWorkspace + DockerWorkspace).
        parent = os.path.dirname(dst_path)
        if parent and parent != self.workspace_root_remote:
            self._exec(["mkdir", "-p", shlex.quote(parent)])
        self._exec(
            ["mv", shlex.quote(self._remote_path(src)), shlex.quote(dst_path)],
        )
        return Path(dst_path)

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        src = _validate_rel_path(src_rel)
        dst = _validate_rel_path(dst_rel)
        dst_path = self._remote_path(dst)
        parent = os.path.dirname(dst_path)
        if parent and parent != self.workspace_root_remote:
            self._exec(["mkdir", "-p", shlex.quote(parent)])
        # ``cp -R`` mirrors DockerWorkspace for dir-aware copies.
        self._exec(
            ["cp", "-R", shlex.quote(self._remote_path(src)), shlex.quote(dst_path)],
        )
        return Path(dst_path)

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        # Identical to move at the remote shell level (mv handles
        # rename + cross-dir moves identically).
        return self.move(src_rel, dst_rel)

    def write_text(self, rel_path: str, content: str) -> Path:
        return self.write_bytes(rel_path, content.encode("utf-8"))

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        rel = _validate_rel_path(rel_path)
        path = self._remote_path(rel)
        parent = os.path.dirname(path)
        if parent and parent != self.workspace_root_remote:
            self._exec(["mkdir", "-p", shlex.quote(parent)])
        # Pipe content into the remote via ``sh -c "cat > path"``.
        # SSH forwards stdin to the remote process; ``cat`` writes
        # those bytes to the target path.
        self._exec(
            ["sh", "-c", f"'cat > {shlex.quote(path)}'"],
            stdin_bytes=content,
        )
        return Path(path)

    def safe_target_rel(self, rel_path: str) -> str:
        """Auto-suffix on collision. Like LocalWorkspace but the
        existence probe goes over ssh."""
        rel = _validate_rel_path(rel_path)
        if not self.exists(rel):
            return rel
        path = Path(rel)
        stem = path.stem
        suffix = path.suffix
        parent = str(path.parent)
        if parent == ".":
            parent = ""
        idx = 1
        while True:
            candidate_name = f"{stem} ({idx}){suffix}"
            candidate = f"{parent}/{candidate_name}" if parent else candidate_name
            if not self.exists(candidate):
                return candidate
            idx += 1
            if idx > 1000:
                raise RemoteWorkspaceError(f"could not find free name for {rel!r} (1000 attempts)")

    # ── debugging convenience ────────────────────────────────────────

    def _dump_state(self) -> str:
        """Helper for tests / debugging — list everything in the remote
        workspace as a flat string."""
        try:
            result = self._exec(
                [
                    "find",
                    shlex.quote(self.workspace_root_remote),
                    "-maxdepth",
                    "5",
                ],
                check=False,
            )
        except RemoteWorkspaceError as exc:
            return f"<dump failed: {exc}>"
        return result.stdout
