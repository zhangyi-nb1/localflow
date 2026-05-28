"""Phase 32.1 — agent-server protocol unit tests.

These exercise the Pydantic models + the shared path defence without
spawning any HTTP server. They run on every CI matrix leg.
"""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError

from app.tools.agent_server.protocol import (
    AGENT_SERVER_VERSION,
    ENDPOINTS,
    AgentServerError,
    ErrorResponse,
    ExistsResponse,
    HealthResponse,
    ListDirResponse,
    MkdirRequest,
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
    endpoint_names,
    to_json_dict,
    validate_rel_path,
)


class TestValidateRelPath:
    """Server-side mirror of the path defence in remote_workspace +
    docker_workspace. Same 9 cases — if any of these slip, the wire
    becomes a path-traversal vector."""

    def test_simple_rel_path_accepted(self):
        assert validate_rel_path("foo.txt") == "foo.txt"

    def test_nested_rel_path_accepted(self):
        assert validate_rel_path("sub/dir/file.md") == "sub/dir/file.md"

    def test_empty_path_returns_empty(self):
        assert validate_rel_path("") == ""

    def test_none_path_returns_empty(self):
        assert validate_rel_path(None) == ""  # type: ignore[arg-type]

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="absolute or home"):
            validate_rel_path("/etc/passwd")

    def test_home_shorthand_rejected(self):
        with pytest.raises(ValueError, match="absolute or home"):
            validate_rel_path("~/secrets")

    def test_drive_letter_rejected(self):
        with pytest.raises(ValueError, match="drive-letter"):
            validate_rel_path("C:/Users/bob")

    def test_parent_traversal_rejected(self):
        with pytest.raises(ValueError, match="parent-directory"):
            validate_rel_path("sub/../escape")

    def test_backslash_normalised(self):
        assert validate_rel_path("sub\\file.md") == "sub/file.md"


class TestPydanticRoundTrip:
    """JSON ↔ object round-trips for every wire model — the actual
    on-wire test. Catches schema drift between client and server."""

    def test_health_response(self):
        msg = HealthResponse()
        roundtrip = HealthResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.status == "ok"
        assert roundtrip.version == AGENT_SERVER_VERSION

    def test_path_request(self):
        msg = PathRequest(path="sub/file.md")
        roundtrip = PathRequest.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.path == "sub/file.md"

    def test_move_request(self):
        msg = MoveRequest(src="a.md", dst="sub/b.md")
        roundtrip = MoveRequest.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.src == "a.md"
        assert roundtrip.dst == "sub/b.md"

    def test_write_bytes_request_base64(self):
        raw = b"\x00\x01\xff"
        b64 = base64.b64encode(raw).decode("ascii")
        msg = WriteBytesRequest(path="f.bin", content_b64=b64)
        roundtrip = WriteBytesRequest.model_validate(json.loads(msg.model_dump_json()))
        assert base64.b64decode(roundtrip.content_b64) == raw

    def test_stat_response_with_payload(self):
        payload = _StatPayload(rel_path="x.txt", size_bytes=42, is_file=True, is_dir=False)
        msg = StatResponse(stat=payload)
        roundtrip = StatResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.stat is not None
        assert roundtrip.stat.size_bytes == 42
        assert roundtrip.stat.is_file is True

    def test_stat_response_with_null(self):
        msg = StatResponse(stat=None)
        roundtrip = StatResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.stat is None

    def test_list_dir_response_sorts_on_read(self):
        msg = ListDirResponse(entries=["b.txt", "a.txt"])
        # The model doesn't sort — the server is expected to sort.
        # This verifies that we preserve order on the wire so the
        # server's sort is the authoritative one.
        roundtrip = ListDirResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.entries == ["b.txt", "a.txt"]

    def test_read_bytes_response_base64(self):
        raw = b"hello\xff"
        msg = ReadBytesResponse(content_b64=base64.b64encode(raw).decode("ascii"))
        roundtrip = ReadBytesResponse.model_validate(json.loads(msg.model_dump_json()))
        assert base64.b64decode(roundtrip.content_b64) == raw

    def test_mkdir_response(self):
        msg = MkdirResponse(created=True)
        roundtrip = MkdirResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.created is True

    def test_path_response(self):
        msg = PathResponse(path="/workspace/sub/note.md")
        roundtrip = PathResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.path == "/workspace/sub/note.md"

    def test_workspace_root_response(self):
        msg = WorkspaceRootResponse(root="/workspace")
        roundtrip = WorkspaceRootResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.root == "/workspace"

    def test_error_response(self):
        msg = ErrorResponse(error="invalid path", detail="absolute not allowed")
        roundtrip = ErrorResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.error == "invalid path"
        assert roundtrip.detail == "absolute not allowed"

    def test_exists_response(self):
        msg = ExistsResponse(exists=True)
        roundtrip = ExistsResponse.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.exists is True

    def test_sha256_response_with_hex(self):
        msg = Sha256Response(sha256="a" * 64)
        roundtrip = Sha256Response.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.sha256 == "a" * 64

    def test_sha256_response_null(self):
        msg = Sha256Response(sha256=None)
        roundtrip = Sha256Response.model_validate(json.loads(msg.model_dump_json()))
        assert roundtrip.sha256 is None


class TestExtraForbid:
    """Every wire model has ``extra='forbid'`` — a typo on either side
    is a loud import-time / validation-time error."""

    def test_path_request_rejects_extra_key(self):
        with pytest.raises(ValidationError):
            PathRequest.model_validate({"path": "x", "extra": "bad"})

    def test_move_request_rejects_extra_key(self):
        with pytest.raises(ValidationError):
            MoveRequest.model_validate({"src": "a", "dst": "b", "extra": "bad"})

    def test_write_bytes_request_rejects_extra_key(self):
        with pytest.raises(ValidationError):
            WriteBytesRequest.model_validate({"path": "x", "content_b64": "", "extra": "bad"})

    def test_stat_response_rejects_extra_key(self):
        with pytest.raises(ValidationError):
            StatResponse.model_validate({"stat": None, "extra": "bad"})

    def test_mkdir_response_rejects_extra_key(self):
        with pytest.raises(ValidationError):
            MkdirResponse.model_validate({"created": True, "extra": "bad"})


class TestEndpointTable:
    """``ENDPOINTS`` is the single source of truth for valid paths;
    the server's dispatch + client + tests all read from it."""

    def test_endpoint_names_lists_every_route(self):
        names = endpoint_names()
        # Health + workspace_root are GET; rest are POST.
        assert "/healthz" in names
        assert "/workspace_root" in names
        # Every Workspace Protocol op.
        for op in (
            "/exists",
            "/stat",
            "/sha256",
            "/list_dir",
            "/read_bytes",
            "/mkdir",
            "/move",
            "/copy",
            "/write_bytes",
            "/safe_target",
        ):
            assert op in names

    def test_endpoints_tuple_is_immutable(self):
        # Defensive — tests catch accidental list-isation.
        assert isinstance(ENDPOINTS, tuple)


class TestHelpers:
    def test_to_json_dict_round_trips(self):
        msg = HealthResponse()
        data = to_json_dict(msg)
        assert data == {"status": "ok", "version": AGENT_SERVER_VERSION}

    def test_agent_server_error_carries_status_and_body(self):
        err = AgentServerError("boom", status=500, body="srv error")
        assert err.status == 500
        assert err.body == "srv error"
        assert str(err) == "boom"


class TestMkdirRequestAlias:
    """``MkdirRequest`` is re-exported through the package facade so
    consumers can write ``MkdirRequest(path=...)`` without thinking
    about which underlying alias to use. The current ``__init__``
    re-exports ``PathRequest`` for /mkdir; this test pins that
    decision so a future refactor doesn't break consumers."""

    def test_mkdir_request_is_path_request(self):
        # MkdirRequest is exported as an alias of PathRequest because
        # the /mkdir endpoint accepts the same shape. If a future
        # change splits them, update this test alongside.
        from app.tools.agent_server import MkdirRequest as Imported

        assert Imported is MkdirRequest
        # And MkdirRequest itself is currently aliased to PathRequest.
        # If you change this, also update the dispatch table in server.py.
        assert MkdirRequest is PathRequest
