"""Phase 32.1 — HTTP agent-server implementation.

stdlib ``http.server`` backed; no new third-party dependencies.
Multi-threaded handler so a single client can pipeline requests
without head-of-line blocking on the server.

The server is **single-tenant** by design: one bearer token, one
workspace root, one client. Multi-tenancy is a Phase 34+ topic.

Spawn:

    server = AgentServer(workspace_root=Path("/workspace"))
    server.start()                  # binds 127.0.0.1:<chosen port>
    print(f"port={server.port}")
    print(f"AGENT_SERVER_TOKEN={server.token}")
    try:
        ...                         # client makes requests
    finally:
        server.stop()

Or as a context manager:

    with AgentServer(workspace_root=Path("/workspace")) as server:
        ...

Concurrency: the handler subclasses ``ThreadingMixIn`` so each request
runs on its own thread. The Workspace operations are filesystem-level
and Linux+macOS are POSIX-thread-safe for our shape (per-path syscalls).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.tools.agent_server.protocol import (
    AGENT_SERVER_VERSION,
    ENDPOINTS,
    ErrorResponse,
    ExistsResponse,
    HealthResponse,
    ListDirResponse,
    MkdirResponse,
    MoveRequest,
    PathRequest,
    PathResponse,
    ReadBytesResponse,
    Sha256Response,
    StatResponse,
    WorkspaceRootResponse,
    WriteBytesRequest,
    _StatPayload,
    to_json_dict,
    validate_rel_path,
)
from app.tools.hash_ops import sha256_file


class AgentServer:
    """Single-tenant HTTP agent-server bound to a workspace root.

    Lifecycle:
      * ``start()`` — bind a TCP socket on 127.0.0.1 and spawn the
        serving thread. Returns when the server is ready to accept
        connections.
      * ``stop()``  — shut down the serving thread + close the
        socket. Idempotent.
      * ``__enter__`` / ``__exit__`` — context-manager wrappers.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        port: int = 0,
        token: str | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        # Resolve absolute so symlink quirks don't surprise callers.
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        # 0 = let the OS pick an ephemeral port; we report the chosen
        # number through self.port after binding.
        self._requested_port = port
        self._host = host
        # Generate a fresh token unless the caller pinned one (rare —
        # mostly tests). 32 bytes → 64 hex chars.
        self.token = token if token is not None else secrets.token_hex(32)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        """The actually-bound port. Available after ``start()``."""
        if self._httpd is None:
            raise RuntimeError("AgentServer not started yet")
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        """Convenience: ``http://127.0.0.1:<port>``."""
        return f"http://{self._host}:{self.port}"

    def start(self) -> None:
        """Bind + start serving. Idempotent."""
        with self._lock:
            if self._httpd is not None:
                return
            handler_factory = _make_handler_factory(self)
            self._httpd = ThreadingHTTPServer((self._host, self._requested_port), handler_factory)
            self._httpd.daemon_threads = True
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="agent-server",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Shut down + close socket. Idempotent."""
        with self._lock:
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> "AgentServer":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # ── path resolution ───────────────────────────────────────────────

    def _abs(self, rel_path: str) -> Path:
        """Return the absolute path for a validated rel_path.

        Caller MUST have called ``validate_rel_path`` first."""
        if not rel_path:
            return self.workspace_root
        return self.workspace_root / rel_path

    # ── op implementations ────────────────────────────────────────────

    def op_exists(self, rel: str) -> bool:
        return self._abs(rel).exists()

    def op_stat(self, rel: str) -> _StatPayload | None:
        abs_path = self._abs(rel)
        if not abs_path.exists():
            return None
        st = abs_path.stat()
        return _StatPayload(
            rel_path=rel,
            size_bytes=st.st_size,
            is_file=abs_path.is_file(),
            is_dir=abs_path.is_dir(),
        )

    def op_sha256(self, rel: str) -> str | None:
        abs_path = self._abs(rel)
        if not abs_path.is_file():
            return None
        return sha256_file(abs_path)

    def op_list_dir(self, rel: str) -> list[str]:
        abs_path = self._abs(rel)
        if not abs_path.is_dir():
            return []
        return sorted(p.name for p in abs_path.iterdir())

    def op_read_bytes(self, rel: str) -> bytes:
        return self._abs(rel).read_bytes()

    def op_mkdir(self, rel: str) -> bool:
        abs_path = self._abs(rel)
        if abs_path.exists():
            # Idempotent: matches LocalWorkspace.mkdir contract.
            return False
        abs_path.mkdir(parents=True, exist_ok=True)
        return True

    def op_move(self, src_rel: str, dst_rel: str) -> Path:
        src = self._abs(src_rel)
        dst = self._abs(dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # shutil.move handles both rename and cross-fs move; matches
        # file_ops.move semantics.
        shutil.move(str(src), str(dst))
        return dst

    def op_copy(self, src_rel: str, dst_rel: str) -> Path:
        src = self._abs(src_rel)
        dst = self._abs(dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=False)
        else:
            shutil.copy2(src, dst)
        return dst

    def op_write_bytes(self, rel: str, content: bytes) -> Path:
        abs_path = self._abs(rel)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return abs_path

    def op_safe_target(self, rel: str) -> str:
        """Auto-suffix on collision. Mirrors LocalWorkspace + DockerWorkspace."""
        abs_path = self._abs(rel)
        if not abs_path.exists():
            return rel
        path = Path(rel)
        stem = path.stem
        suffix = path.suffix
        parent_str = str(path.parent)
        if parent_str == ".":
            parent_str = ""
        idx = 1
        while True:
            candidate_name = f"{stem} ({idx}){suffix}"
            candidate = f"{parent_str}/{candidate_name}" if parent_str else candidate_name
            if not self._abs(candidate).exists():
                return candidate
            idx += 1
            if idx > 1000:
                # Server-side guard mirrors the docker/remote one.
                raise OSError(f"could not find free name for {rel!r} (1000 attempts)")


# ───────────────────────────────────────────────────────── handler factory


def _make_handler_factory(server: AgentServer) -> type[BaseHTTPRequestHandler]:
    """Returns a handler class bound to a specific AgentServer
    instance. ThreadingHTTPServer instantiates this per-request."""

    class _Handler(BaseHTTPRequestHandler):
        # Silence the noisy stdout per-request logging — we don't ship
        # the agent-server as a debug tool; tests can opt-in to logs
        # by overriding this class.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        # ── dispatch helpers

        def _send_json(self, status: HTTPStatus | int, model: BaseModel) -> None:
            body = json.dumps(to_json_dict(model)).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(
            self,
            status: HTTPStatus | int,
            error: str,
            *,
            detail: str = "",
        ) -> None:
            self._send_json(status, ErrorResponse(error=error, detail=detail))

        def _check_auth(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "missing bearer token")
                return False
            client_token = header[len("Bearer ") :].strip()
            # Constant-time compare so timing attacks can't reveal the token.
            if not secrets.compare_digest(client_token, server.token):
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "wrong bearer token")
                return False
            return True

        def _read_body(self) -> bytes | None:
            length_header = self.headers.get("Content-Length", "0")
            try:
                length = int(length_header)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid content-length")
                return None
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _parse_body(self, model_cls: type[BaseModel]) -> BaseModel | None:
            raw = self._read_body()
            if raw is None:
                return None
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid JSON", detail=str(exc))
                return None
            try:
                return model_cls.model_validate(payload)
            except ValidationError as exc:
                self._send_error_json(
                    HTTPStatus.BAD_REQUEST, "invalid request body", detail=str(exc)
                )
                return None

        def _resolve_rel(self, rel_path: str) -> str | None:
            try:
                return validate_rel_path(rel_path)
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid path", detail=str(exc))
                return None

        # ── HTTP method handlers

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                # No auth required for liveness — same pattern as k8s
                # probes; doesn't leak anything sensitive.
                self._send_json(
                    HTTPStatus.OK,
                    HealthResponse(status="ok", version=AGENT_SERVER_VERSION),
                )
                return
            if not self._check_auth():
                return
            if self.path == "/workspace_root":
                self._send_json(
                    HTTPStatus.OK,
                    WorkspaceRootResponse(root=str(server.workspace_root)),
                )
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, f"unknown path: {self.path}")

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            dispatch: dict[
                str,
                tuple[type[BaseModel], Callable[[BaseModel], BaseModel]],
            ] = {
                "/exists": (PathRequest, _do_exists(server)),
                "/stat": (PathRequest, _do_stat(server)),
                "/sha256": (PathRequest, _do_sha256(server)),
                "/list_dir": (PathRequest, _do_list_dir(server)),
                "/read_bytes": (PathRequest, _do_read_bytes(server)),
                "/mkdir": (PathRequest, _do_mkdir(server)),
                "/move": (MoveRequest, _do_move(server)),
                "/copy": (MoveRequest, _do_copy(server)),
                "/write_bytes": (WriteBytesRequest, _do_write_bytes(server)),
                "/safe_target": (PathRequest, _do_safe_target(server)),
            }
            entry = dispatch.get(self.path)
            if entry is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, f"unknown path: {self.path}")
                return
            model_cls, fn = entry
            body = self._parse_body(model_cls)
            if body is None:
                return
            try:
                response = fn(body)
            except ValueError as exc:
                # Path validation failures the op layer raised mid-call
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid path", detail=str(exc))
                return
            except FileNotFoundError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, "file not found", detail=str(exc))
                return
            except OSError as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "io error", detail=str(exc))
                return
            self._send_json(HTTPStatus.OK, response)

    return _Handler


# ───────────────────────────────────────────────────────── op handlers
# Each ``_do_*`` returns a callable that takes the parsed Pydantic
# request and returns a Pydantic response. Errors bubble up to the
# handler's exception-mapping layer.


def _do_exists(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return ExistsResponse(exists=server.op_exists(rel))

    return fn


def _do_stat(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return StatResponse(stat=server.op_stat(rel))

    return fn


def _do_sha256(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return Sha256Response(sha256=server.op_sha256(rel))

    return fn


def _do_list_dir(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return ListDirResponse(entries=server.op_list_dir(rel))

    return fn


def _do_read_bytes(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        data = server.op_read_bytes(rel)
        return ReadBytesResponse(content_b64=base64.b64encode(data).decode("ascii"))

    return fn


def _do_mkdir(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return MkdirResponse(created=server.op_mkdir(rel))

    return fn


def _do_move(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, MoveRequest)
        src = validate_rel_path(req.src)
        dst = validate_rel_path(req.dst)
        path = server.op_move(src, dst)
        return PathResponse(path=str(path))

    return fn


def _do_copy(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, MoveRequest)
        src = validate_rel_path(req.src)
        dst = validate_rel_path(req.dst)
        path = server.op_copy(src, dst)
        return PathResponse(path=str(path))

    return fn


def _do_write_bytes(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, WriteBytesRequest)
        rel = validate_rel_path(req.path)
        try:
            data = base64.b64decode(req.content_b64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise ValueError(f"invalid base64 payload: {exc}") from exc
        path = server.op_write_bytes(rel, data)
        return PathResponse(path=str(path))

    return fn


def _do_safe_target(server: AgentServer) -> Callable[[BaseModel], BaseModel]:
    def fn(req: BaseModel) -> BaseModel:
        assert isinstance(req, PathRequest)
        rel = validate_rel_path(req.path)
        return PathResponse(path=server.op_safe_target(rel))

    return fn


__all__ = [
    "AgentServer",
    "ENDPOINTS",
]


# ───────────────────────────────────────────────────────── module-as-entrypoint


def _main() -> None:  # pragma: no cover - executed only when run as a script
    """``python -m app.tools.agent_server.server``.

    Reads ``AGENT_SERVER_WORKSPACE`` and ``AGENT_SERVER_PORT`` from
    the environment; prints the bound port + bearer token to stdout
    so a supervising process can capture them.
    """
    workspace_root = Path(os.environ.get("AGENT_SERVER_WORKSPACE", "/workspace"))
    port = int(os.environ.get("AGENT_SERVER_PORT", "0"))
    server = AgentServer(workspace_root=workspace_root, port=port)
    server.start()
    print(f"AGENT_SERVER_PORT={server.port}")
    print(f"AGENT_SERVER_TOKEN={server.token}")
    print(f"AGENT_SERVER_WORKSPACE={server.workspace_root}")
    import signal

    def _shutdown(*_: Any) -> None:
        server.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    # Block in the main thread until the daemon thread exits.
    try:
        while server._thread is not None:
            server._thread.join(timeout=1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":  # pragma: no cover
    _main()
