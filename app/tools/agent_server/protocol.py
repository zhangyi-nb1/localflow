"""Phase 32.1 — HTTP agent-server wire protocol.

Pydantic models for every request / response shape. The server
validates incoming JSON against the request models and produces JSON
matching the response models; the client does the inverse. Both sides
share this module so any drift between them is a type error at
import time, not a runtime mystery.

The wire is **bytes-only** for content payloads — text is encoded /
decoded client-side. base64 wrapping is symmetric (request +
response). Path defence is enforced on both sides via the shared
``validate_rel_path`` function.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Pinned at the kernel-package version that introduced this protocol
# so client + server can cheaply assert compatibility.
AGENT_SERVER_VERSION = "0.30.0.dev0"


class AgentServerError(RuntimeError):
    """Raised by the client when the server returns a non-2xx status
    or an unparseable payload. Carries the HTTP status + body for
    diagnostics."""

    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------- path defence

# Same shape as docker_workspace + remote_workspace; reimplemented here
# so the agent-server has no dependency on those modules. The kernel
# layer rules still hold — policy_guard.resolve_inside remains the
# single authority on host-side path-traversal defence; this is the
# agent-side mirror for paths arriving over the wire.
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:[/\\]?")


def validate_rel_path(rel_path: str | None) -> str:
    """Reject absolute / drive-letter / parent-traversal paths.

    Returns the normalised forward-slash path on success; raises
    ``ValueError`` on rejection so the server can turn it into a
    400 response.
    """
    if rel_path is None or rel_path == "":
        return ""
    if rel_path.startswith(("/", "\\", "~")):
        raise ValueError(f"absolute or home-shorthand path not allowed: {rel_path!r}")
    if _DRIVE_LETTER.match(rel_path):
        raise ValueError(f"Windows drive-letter path not allowed: {rel_path!r}")
    normalised = rel_path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p]
    if any(p == ".." for p in parts):
        raise ValueError(f"parent-directory traversal not allowed: {rel_path!r}")
    return "/".join(parts)


# ---------------------------------------------------------------- request models


class _StrictModel(BaseModel):
    """Base for every wire model — ``extra='forbid'`` so a typo in a
    field name on either side is caught loudly. Mirrors the discipline
    every kernel schema applies."""

    model_config = ConfigDict(extra="forbid")


class PathRequest(_StrictModel):
    """Shared shape for endpoints that take a single rel_path:
    ``exists`` / ``stat`` / ``sha256`` / ``list_dir`` / ``read_bytes``
    / ``mkdir`` / ``safe_target``."""

    path: str = ""


# Friendly alias so client code reads ``MkdirRequest(path=...)``
# without having to look up that ``/mkdir`` reuses PathRequest. If
# this ever needs to diverge from PathRequest (different fields), do
# it as a separate class and update server.py's dispatch table.
MkdirRequest = PathRequest


class MoveRequest(_StrictModel):
    """``move`` / ``copy`` — two rel_paths."""

    src: str
    dst: str


class WriteBytesRequest(_StrictModel):
    """``write_bytes`` — rel_path + base64-encoded content."""

    path: str
    content_b64: str
    """The bytes to write, base64-encoded (ASCII-safe)."""


# ---------------------------------------------------------------- response models


class HealthResponse(_StrictModel):
    """``GET /healthz`` — liveness + protocol version."""

    status: str = "ok"
    version: str = AGENT_SERVER_VERSION


class PathResponse(_StrictModel):
    """``move`` / ``copy`` / ``write_bytes`` — server returns the
    absolute server-side path of the operation's target (for debug /
    logging; the client treats it as opaque)."""

    path: str


class _StatPayload(_StrictModel):
    """In-memory shape of ``WorkspaceStat`` — duplicated here so this
    module stays free of imports from ``app.tools.workspace``. Client
    + server convert to/from the canonical ``WorkspaceStat`` at the
    boundary."""

    rel_path: str
    size_bytes: int
    is_file: bool
    is_dir: bool


class StatResponse(_StrictModel):
    """``stat`` — ``None`` when the path doesn't exist."""

    stat: _StatPayload | None = None


class ExistsResponse(_StrictModel):
    """``exists`` — boolean."""

    exists: bool


class Sha256Response(_StrictModel):
    """``sha256`` — hex digest or ``None`` (directory / missing)."""

    sha256: str | None = None


class ListDirResponse(_StrictModel):
    """``list_dir`` — sorted list of immediate-child basenames."""

    entries: list[str] = Field(default_factory=list)


class ReadBytesResponse(_StrictModel):
    """``read_bytes`` — base64-encoded content."""

    content_b64: str


class MkdirResponse(_StrictModel):
    """``mkdir`` — ``True`` iff a new directory was created (matches
    LocalWorkspace.mkdir contract)."""

    created: bool


class WorkspaceRootResponse(_StrictModel):
    """``GET /workspace_root`` — absolute path the server is rooted at."""

    root: str


class ErrorResponse(_StrictModel):
    """Shared shape for every non-2xx body. Lets clients parse the
    failure without sniffing the status alone."""

    error: str
    detail: str = ""


# ---------------------------------------------------------------- endpoint map


# Single source of truth for valid endpoints. Helps the server's
# dispatch table + the client tests stay in sync. Tuple of
# (METHOD, path, request_model_or_None).
ENDPOINTS: tuple[tuple[str, str, type[BaseModel] | None], ...] = (
    ("GET", "/healthz", None),
    ("GET", "/workspace_root", None),
    ("POST", "/exists", PathRequest),
    ("POST", "/stat", PathRequest),
    ("POST", "/sha256", PathRequest),
    ("POST", "/list_dir", PathRequest),
    ("POST", "/read_bytes", PathRequest),
    ("POST", "/mkdir", PathRequest),
    ("POST", "/move", MoveRequest),
    ("POST", "/copy", MoveRequest),
    ("POST", "/write_bytes", WriteBytesRequest),
    ("POST", "/safe_target", PathRequest),
)


def endpoint_names() -> list[str]:
    """Helper for tests + introspection — the path component of every
    registered endpoint."""
    return [path for _, path, _ in ENDPOINTS]


# ---------------------------------------------------------------- request / response helpers


def to_json_dict(model: BaseModel) -> dict[str, Any]:
    """Serialise a Pydantic model to a JSON-safe dict. Used by server
    handlers when composing responses."""
    return model.model_dump(mode="json")
