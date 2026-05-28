"""Phase 32.2 — HTTP agent-server client.

stdlib-only urllib-based client. Sends Pydantic models, parses
Pydantic models, never touches raw JSON outside this module.

Connection reuse: stdlib's ``urllib.request`` opens a fresh TCP
connection per call. That's still 10-100x cheaper than ``docker
exec`` or ``ssh`` because there's no fork+exec, no shell, no
authentication handshake. If TCP setup itself becomes the
bottleneck (Phase 34+ candidate signal), swap urllib for a
keepalive-aware library — the client surface is small enough.
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from app.tools.agent_server.protocol import (
    AgentServerError,
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
)

DEFAULT_TIMEOUT_SEC = 30.0


class AgentServerClient:
    """Talks HTTP to an ``AgentServer``.

    The client doesn't know anything about the server's transport
    (docker exec → localhost forwarded port, ssh → tunnelled port,
    plain localhost) — it just needs ``base_url`` + ``token``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        # Strip trailing slash so paths concat cleanly.
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_sec = timeout_sec

    # ── transport helpers ─────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        require_auth: bool = True,
    ) -> bytes:
        """Issue an HTTP request and return the response body. Raises
        ``AgentServerError`` on transport failure OR non-2xx status."""
        url = f"{self.base_url}{path}"
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if require_auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url=url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read() if hasattr(exc, "read") else b""
            text = payload.decode("utf-8", errors="replace")
            # Try to parse as ErrorResponse for a nicer message.
            try:
                err = ErrorResponse.model_validate(json.loads(text))
                msg = f"{err.error}: {err.detail}" if err.detail else err.error
            except Exception:
                msg = text.strip() or f"HTTP {exc.code}"
            raise AgentServerError(msg, status=exc.code, body=text) from exc
        except urllib.error.URLError as exc:
            raise AgentServerError(f"network error: {exc.reason}", status=0) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise AgentServerError(f"timeout after {self.timeout_sec}s", status=0) from exc

    def _post_json(self, path: str, payload: dict) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, body=body)

    # ── high-level API ────────────────────────────────────────────────

    def healthz(self) -> HealthResponse:
        raw = self._request("GET", "/healthz", require_auth=False)
        return HealthResponse.model_validate(json.loads(raw))

    def workspace_root(self) -> Path:
        raw = self._request("GET", "/workspace_root")
        return Path(WorkspaceRootResponse.model_validate(json.loads(raw)).root)

    def exists(self, rel_path: str) -> bool:
        raw = self._post_json("/exists", PathRequest(path=rel_path).model_dump())
        return ExistsResponse.model_validate(json.loads(raw)).exists

    def stat(self, rel_path: str) -> _StatPayload | None:
        raw = self._post_json("/stat", PathRequest(path=rel_path).model_dump())
        return StatResponse.model_validate(json.loads(raw)).stat

    def sha256(self, rel_path: str) -> str | None:
        raw = self._post_json("/sha256", PathRequest(path=rel_path).model_dump())
        return Sha256Response.model_validate(json.loads(raw)).sha256

    def list_dir(self, rel_path: str = "") -> list[str]:
        raw = self._post_json("/list_dir", PathRequest(path=rel_path).model_dump())
        return ListDirResponse.model_validate(json.loads(raw)).entries

    def read_bytes(self, rel_path: str) -> bytes:
        raw = self._post_json("/read_bytes", PathRequest(path=rel_path).model_dump())
        return base64.b64decode(ReadBytesResponse.model_validate(json.loads(raw)).content_b64)

    def mkdir(self, rel_path: str) -> bool:
        raw = self._post_json("/mkdir", PathRequest(path=rel_path).model_dump())
        return MkdirResponse.model_validate(json.loads(raw)).created

    def move(self, src_rel: str, dst_rel: str) -> Path:
        raw = self._post_json("/move", MoveRequest(src=src_rel, dst=dst_rel).model_dump())
        return Path(PathResponse.model_validate(json.loads(raw)).path)

    def copy(self, src_rel: str, dst_rel: str) -> Path:
        raw = self._post_json("/copy", MoveRequest(src=src_rel, dst=dst_rel).model_dump())
        return Path(PathResponse.model_validate(json.loads(raw)).path)

    def write_bytes(self, rel_path: str, content: bytes) -> Path:
        b64 = base64.b64encode(content).decode("ascii")
        raw = self._post_json(
            "/write_bytes",
            WriteBytesRequest(path=rel_path, content_b64=b64).model_dump(),
        )
        return Path(PathResponse.model_validate(json.loads(raw)).path)

    def safe_target(self, rel_path: str) -> str:
        raw = self._post_json("/safe_target", PathRequest(path=rel_path).model_dump())
        return PathResponse.model_validate(json.loads(raw)).path


__all__ = ["AgentServerClient", "DEFAULT_TIMEOUT_SEC"]
