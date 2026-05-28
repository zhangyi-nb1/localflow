"""Phase 29.0 — DockerWorkspace.

A ``Workspace`` implementation that runs the user workspace INSIDE a
Docker container and routes every filesystem operation through
``docker exec``. The container's filesystem is isolated from the
host — no bind mount by default — so a plan that does something
unexpected can't reach the user's real files. This is the strong-
isolation backend Phase 23's PYTHON_COMPUTE always wanted.

Trade-offs (documented honestly):
  - Latency: each operation costs one ``docker exec`` round-trip
    (~100-300ms). Acceptable for plan execution (tens of actions);
    Phase 29.x can move to an HTTP agent-server for hot paths.
  - Persistence: the container is ephemeral. Outputs you want to
    keep must be promoted via a separate stage to a LocalWorkspace
    (mirror of Phase 23's scratch-to-workspace pattern).
  - Bootstrap: first run pulls the image (default ``python:3.12-slim``,
    ~50 MB). CI / dev should pre-pull.

§10.7 invariant: this is an application-layer Workspace implementation.
``policy_guard.resolve_inside`` still authorises every path on the
host side BEFORE docker exec; rollback / trace / verifier all run
through the same kernel surfaces with no special-casing.

Lifecycle:
  ws = DockerWorkspace(image="python:3.12-slim")
  ws.start()          # docker run -d ...
  try:
      ws.mkdir("sub/")
      ...
  finally:
      ws.close()      # docker rm -f

Or context-manager: ``with DockerWorkspace(...) as ws: ...``.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import shlex
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.tools.workspace import WorkspaceStat

# Default OCI image. Lightweight; ships sh + standard coreutils + python3
# which is everything DockerWorkspace's operations need.
DEFAULT_IMAGE = "python:3.12-slim"

# In-container path the host-side ``rel_path`` maps onto. Fixed (not
# configurable) because the kernel layer assumes a single workspace
# root per Workspace instance and the abstraction layer below is
# rel_path-only.
CONTAINER_WORKSPACE_ROOT = "/workspace"

# Per-op exec timeout — defends against a hung sub-process inside the
# container (or a stuck Docker daemon) blocking the harness forever.
DEFAULT_EXEC_TIMEOUT_SEC = 60


class DockerUnavailable(RuntimeError):
    """Raised when Docker CLI / daemon is not reachable.

    Callers catch this and fall back to LocalWorkspace, or raise it
    to the user with a clear "install Docker to use --workspace
    docker:..." message. The kernel never sees it — DockerWorkspace
    constructor / start() is where the check lives."""


class DockerWorkspaceError(RuntimeError):
    """Raised when a docker exec call fails. Carries stdout + stderr
    so callers can surface a useful diagnostic."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _docker_available() -> bool:
    """Probe whether ``docker`` CLI exists AND the daemon answers.

    Additionally returns False when the daemon is in **Windows
    containers mode** — DockerWorkspace only ships Linux container
    images (``python:3.12-slim`` etc.), and a Windows-mode daemon
    will fail every ``docker pull`` with "no matching manifest for
    windows(...)". Detect early so tests / CLI fall back / skip
    cleanly rather than fail per-operation."""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    # Probe daemon OSType — "linux" for Linux containers mode,
    # "windows" for Windows containers mode. DockerWorkspace requires
    # the linux mode; surface "not available" otherwise so callers
    # don't waste time pulling images that have no matching manifest.
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if info.returncode != 0:
        return False
    os_type = info.stdout.strip().lower()
    return os_type == "linux"


# Same shape as policy_guard's defence but reimplemented here so the
# DockerWorkspace doesn't have to import the kernel module (keeps the
# layer separation clean — kernel imports tools, not the other way).
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:[/\\]?")


def _validate_rel_path(rel_path: str) -> str:
    """Reject absolute / drive-letter / UNC / parent-traversal paths.

    Returns the normalised forward-slash path on success; raises
    ``DockerWorkspaceError`` on rejection. Mirrors LocalWorkspace's
    ``resolve_inside`` defence but in a way that produces a string
    safe to interpolate into a docker exec argument.
    """
    if rel_path is None or rel_path == "":
        # Empty is valid only for "list the root" — caller decides
        # whether to treat it as workspace-root or not.
        return ""
    if rel_path.startswith(("/", "\\", "~")):
        raise DockerWorkspaceError(f"absolute or home-shorthand path not allowed: {rel_path!r}")
    if _DRIVE_LETTER.match(rel_path):
        raise DockerWorkspaceError(f"Windows drive-letter path not allowed: {rel_path!r}")
    # Normalise backslashes → forward slashes, then split-and-check.
    normalised = rel_path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p]
    if any(p == ".." for p in parts):
        raise DockerWorkspaceError(f"parent-directory traversal not allowed: {rel_path!r}")
    return "/".join(parts)


def _pick_free_local_port() -> int:
    """Phase 33.1 — pick a free 127.0.0.1 port the kernel can later
    forward via ``docker run -p``. We bind to ``0`` (kernel-assigned),
    read the port, then close — there's a tiny window where someone
    else could grab it, but ports are 64K so collisions are rare.
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _read_agent_handshake(proc: "subprocess.Popen", *, timeout_sec: float = 10.0) -> dict[str, str]:
    """Phase 33.1 — parse the three ``AGENT_SERVER_*`` lines the
    bundle writes to stdout on startup.

    Raises ``TimeoutError`` if the handshake doesn't complete within
    ``timeout_sec`` or ``RuntimeError`` if the subprocess dies first.
    """
    info: dict[str, str] = {}
    deadline = time.time() + timeout_sec
    while len(info) < 3:
        if time.time() > deadline:
            raise TimeoutError(f"agent-server handshake timed out after {timeout_sec}s")
        if proc.poll() is not None:
            stderr = ""
            if proc.stderr is not None:
                try:
                    stderr = proc.stderr.read() or ""
                except Exception:
                    pass
            raise RuntimeError(
                f"agent-server subprocess exited early (rc={proc.returncode}). stderr: {stderr.strip()}"
            )
        # readline blocks; the dispatch above relies on the bundle
        # writing exactly three lines back-to-back at startup. If it
        # doesn't, the deadline cap catches it.
        if proc.stdout is None:
            raise RuntimeError("agent-server subprocess has no stdout")
        line = proc.stdout.readline()
        if not line:
            # EOF before all three lines arrived.
            time.sleep(0.05)
            continue
        line = line.strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        info[k] = v
    return info


@dataclass
class DockerWorkspace:
    """Workspace backed by a Docker container.

    The container is started by ``start()`` (or the context-manager
    ``__enter__``) and torn down by ``close()`` (or ``__exit__``).
    Operations between those bookends route to the container via
    ``docker exec``. The kernel sees the same Workspace Protocol it
    sees for LocalWorkspace.

    Phase 33.1: ``use_agent_server`` makes ops go through a long-lived
    HTTP daemon inside the container instead of one ``docker exec`` per
    op. Defaults to False to keep Phase 29's behaviour stable; opt in
    via ``DockerWorkspace(use_agent_server=True)`` or via the
    ``localflow execute --workspace docker:<img>:agent`` CLI shorthand
    (added in 33.2). If the agent-server fails to start, the backend
    logs a warning and **falls back to docker exec** automatically —
    callers never see partial state.
    """

    image: str = DEFAULT_IMAGE
    container_name: str | None = None
    workspace_root_inside: str = CONTAINER_WORKSPACE_ROOT
    exec_timeout_sec: int = DEFAULT_EXEC_TIMEOUT_SEC
    container_id: str | None = None
    use_agent_server: bool = False
    """Phase 33.1 opt-in: route ops through an in-container HTTP daemon
    (`AgentServer`) instead of `docker exec` per op. Promotes Workspace
    op latency from ~100-300 ms to ~5-20 ms on hot paths."""

    agent_server_port_in_container: int = 8765
    """Container-side port the agent-server binds to. We forward a
    random host port to this fixed container port so each
    DockerWorkspace gets its own port mapping."""

    _started: bool = False
    _agent_proc: "subprocess.Popen | None" = None
    """The ``docker exec`` subprocess that keeps the agent-server
    alive. ``close()`` terminates it. None when use_agent_server=False
    or when the agent failed to start (graceful fallback)."""

    _agent_client: "object | None" = None
    """``AgentServerClient`` instance when agent-server mode is active.
    Type-erased to ``object`` here to avoid a top-level import cycle —
    actual type is restored at the use site."""

    _agent_host_port: int = 0
    """The host-side port the agent-server's container port is
    forwarded to. 0 when no agent in flight."""

    @classmethod
    def is_available(cls) -> bool:
        """Cheap probe for callers that want to gracefully degrade
        (e.g. CLI ``--workspace docker:...`` → LocalWorkspace fallback)."""
        return _docker_available()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Pull (if needed) and start the container. Idempotent."""
        if self._started:
            return
        if not _docker_available():
            raise DockerUnavailable(
                "Docker CLI / daemon not reachable. Install Docker Desktop / "
                "Docker Engine, or fall back to LocalWorkspace."
            )

        # Phase 29.0 fix — Windows Docker daemon (and some restricted
        # configs) do NOT auto-pull missing images on ``docker run``.
        # Pre-pulling with an extended timeout makes container start
        # behaviour identical across CI platforms.
        pull_timeout = max(self.exec_timeout_sec, 180)
        pull_result = subprocess.run(
            ["docker", "pull", self.image],
            capture_output=True,
            text=True,
            timeout=pull_timeout,
        )
        if pull_result.returncode != 0:
            raise DockerWorkspaceError(
                f"failed to pull image {self.image!r}: {pull_result.stderr.strip()}",
                stdout=pull_result.stdout,
                stderr=pull_result.stderr,
            )

        # Generate a unique container name so concurrent runs do not
        # collide on the host-side namespace.
        name = self.container_name or f"localflow-ws-{uuid.uuid4().hex[:8]}"
        self.container_name = name
        # ``sleep infinity`` keeps the container alive so subsequent
        # ``docker exec`` calls land on the same fs. ``-d`` detaches.
        # The inline ``mkdir -p`` ensures the workspace root exists
        # before any later exec — no need for a second mkdir below.
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
        ]
        # Phase 33.1: if agent-server mode is requested, pick a free
        # host port + forward it to the fixed container-side port the
        # agent-server will bind to. Picking the host port up front
        # (via SO_REUSEADDR socket) is more reliable than asking docker
        # for an ephemeral assignment + querying ``docker port`` after.
        if self.use_agent_server:
            self._agent_host_port = _pick_free_local_port()
            cmd.extend(
                [
                    "-p",
                    f"127.0.0.1:{self._agent_host_port}:{self.agent_server_port_in_container}",
                ]
            )
        cmd.extend(
            [
                "--workdir",
                self.workspace_root_inside,
                self.image,
                "sh",
                "-c",
                f"mkdir -p {shlex.quote(self.workspace_root_inside)} && sleep infinity",
            ]
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.exec_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerWorkspaceError(f"timed out starting container {name!r}") from exc
        if result.returncode != 0:
            raise DockerWorkspaceError(
                f"failed to start container {name!r}: {result.stderr.strip()}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        self.container_id = result.stdout.strip()
        # Phase 29.0 fix — set _started BEFORE any further _exec call.
        # Previous order called _exec(["mkdir", ...]) here which
        # _require_started rejected because _started was still False.
        # The container's sh -c command above already created
        # /workspace, so the extra mkdir was redundant anyway.
        self._started = True

        # Phase 33.1: spawn the in-container agent-server. Best-effort —
        # on any failure we log + leave ``_agent_client = None`` so
        # method dispatch falls through to docker exec.
        if self.use_agent_server:
            self._spawn_agent_server()

    def _spawn_agent_server(self) -> None:
        """Phase 33.1 — start the bundled agent-server inside the
        container via ``docker exec``. Reads the 3 ``AGENT_SERVER_*``
        handshake lines from stdout, then leaves the subprocess
        running until ``close()``.

        Best-effort: any failure (image missing python3, port already
        bound, handshake timeout) logs a diagnostic to stderr and
        leaves ``_agent_client = None`` so the Workspace dispatch
        falls back to ``docker exec`` per op.
        """
        # Lazy import to keep agent_server out of the import graph when
        # users only use the docker exec mode.
        from app.tools.agent_server.bundle import build_bundle
        from app.tools.agent_server.client import AgentServerClient

        bundle = build_bundle()
        token = secrets.token_hex(32)
        env_args = [
            "-e",
            f"AGENT_SERVER_WORKSPACE={self.workspace_root_inside}",
            "-e",
            f"AGENT_SERVER_PORT={self.agent_server_port_in_container}",
            "-e",
            f"AGENT_SERVER_TOKEN={token}",
            # Bind to 0.0.0.0 so docker's port-forward sees the listener.
            # Defence still holds — the host-side mapping pinned the
            # interface to 127.0.0.1 via ``-p 127.0.0.1:<host>:<ctr>``.
            "-e",
            "AGENT_SERVER_HOST=0.0.0.0",
        ]
        proc = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                *env_args,
                self.container_name or "",
                "python3",
                "-c",
                bundle,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Read handshake lines with a per-line timeout so a stuck
        # container can't hang the whole start() call.
        try:
            handshake = _read_agent_handshake(proc, timeout_sec=10.0)
        except (TimeoutError, RuntimeError) as exc:
            # Bundle failed to bind / image missing python3 / etc.
            # Tear the dangling subprocess down and fall back to exec.
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._agent_proc = None
            self._agent_client = None
            # Surface a diagnostic but don't raise — Workspace ops
            # will use docker exec.
            import sys as _sys

            _sys.stderr.write(
                f"DockerWorkspace agent-server start failed: {exc}; "
                f"falling back to ``docker exec`` per op.\n"
            )
            return

        actual_token = handshake.get("AGENT_SERVER_TOKEN")
        if actual_token != token:
            # Shouldn't happen — bundle honours AGENT_SERVER_TOKEN env.
            # But if it did, treat as a startup failure.
            proc.terminate()
            self._agent_proc = None
            self._agent_client = None
            return

        self._agent_proc = proc
        self._agent_client = AgentServerClient(
            base_url=f"http://127.0.0.1:{self._agent_host_port}",
            token=actual_token,
        )

    def close(self) -> None:
        """Stop and remove the container. Idempotent."""
        if not self._started or self.container_name is None:
            return
        # Phase 33.1: shut the agent-server subprocess down first so
        # the docker rm -f doesn't race with a half-alive exec.
        if self._agent_proc is not None:
            try:
                self._agent_proc.terminate()
                self._agent_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._agent_proc.kill()
            except OSError:
                pass
            self._agent_proc = None
        self._agent_client = None
        self._agent_host_port = 0
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
            timeout=self.exec_timeout_sec,
        )
        self._started = False
        self.container_id = None

    def __enter__(self) -> "DockerWorkspace":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ── Workspace Protocol: properties ───────────────────────────────

    @property
    def root(self) -> Path:
        """The CONTAINER-side root, not a host path. Callers should
        treat it as opaque — for display only. DockerWorkspace
        deliberately never exposes a host-equivalent because no
        such thing exists for an isolated container."""
        return Path(self.workspace_root_inside)

    def is_local(self) -> bool:
        return False

    # ── helpers ──────────────────────────────────────────────────────

    def _require_started(self) -> None:
        if not self._started:
            raise DockerWorkspaceError(
                "DockerWorkspace not started — call .start() or use the "
                "``with DockerWorkspace(...) as ws:`` context manager."
            )

    def _container_path(self, rel_path: str) -> str:
        """Compose the container-side absolute path from a validated
        rel_path. Caller is responsible for calling ``_validate_rel_path``
        first."""
        if not rel_path:
            return self.workspace_root_inside
        return f"{self.workspace_root_inside}/{rel_path}"

    def _exec(
        self,
        cmd: list[str],
        *,
        stdin_bytes: bytes | None = None,
        capture: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``docker exec <container_name> <cmd>``. Returns the
        CompletedProcess; raises DockerWorkspaceError on non-zero
        return when ``check=True``."""
        self._require_started()
        full = ["docker", "exec"]
        if stdin_bytes is not None:
            full.append("-i")
        full.append(self.container_name or "")
        full.extend(cmd)
        try:
            result = subprocess.run(
                full,
                input=stdin_bytes,
                capture_output=capture,
                timeout=self.exec_timeout_sec,
                # Use bytes-mode so binary writes/reads work; decode
                # text-mode results below.
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerWorkspaceError(f"docker exec timed out: {' '.join(cmd)!r}") from exc
        stdout_text = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        # Repackage to a text CompletedProcess for caller convenience —
        # binary callers (read_bytes) access raw .stdout via stdin_bytes
        # path's separate code path below.
        if check and result.returncode != 0:
            raise DockerWorkspaceError(
                f"docker exec failed (rc={result.returncode}): {' '.join(cmd)!r}\n"
                f"stderr: {stderr_text.strip()}",
                stdout=stdout_text,
                stderr=stderr_text,
            )
        # Stash bytes in stdout for callers that need them.
        return subprocess.CompletedProcess(
            args=full,
            returncode=result.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
        )

    def _exec_bytes(self, cmd: list[str]) -> bytes:
        """Variant of _exec that returns raw stdout bytes (for read_bytes)."""
        self._require_started()
        full = ["docker", "exec", self.container_name or "", *cmd]
        try:
            result = subprocess.run(full, capture_output=True, timeout=self.exec_timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise DockerWorkspaceError(f"docker exec timed out: {' '.join(cmd)!r}") from exc
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise DockerWorkspaceError(
                f"docker exec failed (rc={result.returncode}): {' '.join(cmd)!r}\n"
                f"stderr: {stderr_text.strip()}",
                stderr=stderr_text,
            )
        return result.stdout or b""

    # ── Workspace Protocol: reads ────────────────────────────────────

    # Phase 33.1: helper that wraps each method body so when
    # ``_agent_client`` is active, the call delegates to HTTP and never
    # spawns a ``docker exec``. Each Workspace method below tries the
    # delegate first; if not active OR the call raises, the existing
    # ``docker exec`` body runs as fallback.

    def _delegate_to_agent(self):
        """Return the AgentServerClient if active, else None."""
        return self._agent_client

    def exists(self, rel_path: str) -> bool:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.exists(rel_path)
            except Exception:
                pass  # fall through to docker exec
        try:
            rel = _validate_rel_path(rel_path)
        except DockerWorkspaceError:
            return False
        result = self._exec(["test", "-e", self._container_path(rel)], check=False)
        return result.returncode == 0

    def stat(self, rel_path: str) -> WorkspaceStat | None:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                payload = agent.stat(rel_path)
                if payload is None:
                    return None
                return WorkspaceStat(
                    rel_path=payload.rel_path,
                    size_bytes=payload.size_bytes,
                    is_file=payload.is_file,
                    is_dir=payload.is_dir,
                )
            except Exception:
                pass
        try:
            rel = _validate_rel_path(rel_path)
        except DockerWorkspaceError:
            return None
        path = self._container_path(rel)
        # ``stat -c '%s %F'`` works on Linux containers — outputs e.g.
        # "1234 regular file" or "4096 directory". (GNU stat; available
        # in python:3.12-slim via debian coreutils.)
        result = self._exec(["stat", "-c", "%s %F", path], check=False)
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
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.sha256(rel_path)
            except Exception:
                pass
        try:
            rel = _validate_rel_path(rel_path)
        except DockerWorkspaceError:
            return None
        path = self._container_path(rel)
        # Pre-check: only files have sha256; dirs return None for
        # parity with LocalWorkspace.
        stat = self.stat(rel_path)
        if stat is None or not stat.is_file:
            return None
        result = self._exec(["sha256sum", path], check=False)
        if result.returncode != 0:
            return None
        # sha256sum output: "<hex>  /workspace/<path>\n"
        return result.stdout.split()[0] if result.stdout else None

    def list_dir(self, rel_path: str = "") -> list[str]:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.list_dir(rel_path)
            except Exception:
                pass
        try:
            rel = _validate_rel_path(rel_path)
        except DockerWorkspaceError:
            return []
        path = self._container_path(rel)
        result = self._exec(["sh", "-c", f"ls -1A {shlex.quote(path)} 2>/dev/null"], check=False)
        if result.returncode != 0:
            return []
        return sorted(line for line in result.stdout.splitlines() if line)

    def read_bytes(self, rel_path: str) -> bytes:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.read_bytes(rel_path)
            except Exception:
                pass
        rel = _validate_rel_path(rel_path)
        return self._exec_bytes(["cat", self._container_path(rel)])

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(rel_path).decode(encoding)

    # ── Workspace Protocol: writes ───────────────────────────────────

    def mkdir(self, rel_path: str) -> bool:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.mkdir(rel_path)
            except Exception:
                pass
        rel = _validate_rel_path(rel_path)
        path = self._container_path(rel)
        # Check existence first so we return False on idempotent re-create
        # (matching LocalWorkspace.mkdir contract).
        if self.exists(rel):
            return False
        self._exec(["mkdir", "-p", path])
        return True

    def move(self, src_rel: str, dst_rel: str) -> Path:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.move(src_rel, dst_rel)
            except Exception:
                pass
        src = _validate_rel_path(src_rel)
        dst = _validate_rel_path(dst_rel)
        dst_path = self._container_path(dst)
        # Ensure parent exists (mirrors LocalWorkspace/file_ops behaviour).
        parent = os.path.dirname(dst_path)
        if parent and parent != self.workspace_root_inside:
            self._exec(["mkdir", "-p", parent])
        self._exec(["mv", self._container_path(src), dst_path])
        return Path(dst_path)

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.copy(src_rel, dst_rel)
            except Exception:
                pass
        src = _validate_rel_path(src_rel)
        dst = _validate_rel_path(dst_rel)
        dst_path = self._container_path(dst)
        parent = os.path.dirname(dst_path)
        if parent and parent != self.workspace_root_inside:
            self._exec(["mkdir", "-p", parent])
        self._exec(["cp", self._container_path(src), dst_path])
        return Path(dst_path)

    def rename(self, src_rel: str, dst_rel: str) -> Path:
        # Identical to move at the container level (mv handles rename
        # within a dir + cross-dir moves identically).
        return self.move(src_rel, dst_rel)

    def write_text(self, rel_path: str, content: str) -> Path:
        return self.write_bytes(rel_path, content.encode("utf-8"))

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        agent = self._delegate_to_agent()
        if agent is not None:
            try:
                return agent.write_bytes(rel_path, content)
            except Exception:
                pass
        rel = _validate_rel_path(rel_path)
        path = self._container_path(rel)
        parent = os.path.dirname(path)
        if parent and parent != self.workspace_root_inside:
            self._exec(["mkdir", "-p", parent])
        # Pipe content into the container via ``sh -c "cat > path"``.
        # docker exec -i forwards stdin into the container's exec'd
        # process; cat writes those bytes to the target path.
        self._exec(
            ["sh", "-c", f"cat > {shlex.quote(path)}"],
            stdin_bytes=content,
        )
        return Path(path)

    def safe_target_rel(self, rel_path: str) -> str:
        """Auto-suffix on collision. Like LocalWorkspace but executed
        via stat probes inside the container."""
        rel = _validate_rel_path(rel_path)
        if not self.exists(rel):
            return rel
        # Split into stem + suffix(es).
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
            if idx > 1000:  # paranoid upper bound
                raise DockerWorkspaceError(f"could not find free name for {rel!r} (1000 attempts)")

    # ── debugging convenience ────────────────────────────────────────

    def _dump_state(self) -> str:
        """Helper for tests / debugging — list everything in the
        container workspace as a flat string."""
        try:
            result = self._exec(
                ["find", self.workspace_root_inside, "-maxdepth", "5"],
                check=False,
            )
            return result.stdout
        except DockerWorkspaceError:
            return ""


# Compatibility shim — earlier drafts used DockerWorkspaceError when
# Docker was unavailable. Keep a re-export for clarity.
__all__ = [
    "DockerWorkspace",
    "DockerUnavailable",
    "DockerWorkspaceError",
    "DEFAULT_IMAGE",
    "CONTAINER_WORKSPACE_ROOT",
]


# Tests of Phase 29.0 deliberately exercise the path-defence logic
# without spinning up a real container — that's the cheap layer that
# doesn't need docker. Container-actual operations skip when
# ``DockerWorkspace.is_available()`` returns False.
_ = io  # quiet unused-import lint in environments where io isn't needed
