"""Phase 35.2 — tests for the honest UI ↔ backend bridge.

``describe_ui_backend`` is a pure function (no Streamlit) so it runs
on every CI matrix leg. It encodes the Phase 35.2 honesty decision:
the UI executes locally; docker/ssh are surfaced as a CLI bridge.
"""

from __future__ import annotations

from app.ui._workspace_backend import UIBackendNotice, describe_ui_backend


class TestDescribeUIBackend:
    def test_local_executes_locally_no_cli(self):
        n = describe_ui_backend("local")
        assert isinstance(n, UIBackendNotice)
        assert n.kind == "local"
        assert n.executes_locally is True
        assert n.cli_command is None
        assert "local sandbox" in n.message

    def test_empty_spec_defaults_to_local(self):
        assert describe_ui_backend("").executes_locally is True
        assert describe_ui_backend(None).executes_locally is True

    def test_whitespace_spec_defaults_to_local(self):
        assert describe_ui_backend("  local  ").kind == "local"

    def test_docker_is_cli_bridged(self):
        n = describe_ui_backend("docker:python:3.12-slim", task_id="2026-05-29-001")
        assert n.kind == "docker"
        assert n.executes_locally is False
        assert n.cli_command == (
            "localflow execute --task-id 2026-05-29-001 --workspace docker:python:3.12-slim"
        )
        assert "runs via the CLI" in n.message

    def test_ssh_is_cli_bridged(self):
        n = describe_ui_backend("ssh:bob@host:22:/srv/ws", task_id="T")
        assert n.kind == "ssh"
        assert n.executes_locally is False
        assert n.cli_command == "localflow execute --task-id T --workspace ssh:bob@host:22:/srv/ws"

    def test_task_id_placeholder_when_unknown(self):
        n = describe_ui_backend("docker:img")
        assert "<task-id>" in (n.cli_command or "")

    def test_unknown_prefix_still_bridged_not_crashed(self):
        # Defensive: an unexpected spec should still produce a notice,
        # not raise. (Settings validates on save; this is belt-and-braces.)
        n = describe_ui_backend("weird:thing")
        assert n.kind == "unknown"
        assert n.executes_locally is False
        assert n.cli_command is not None

    def test_never_raises_on_odd_input(self):
        for spec in ("", " ", "local", "docker:", "ssh:", "x"):
            describe_ui_backend(spec)  # must not raise
