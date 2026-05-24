"""Phase 25.5 — RunStore trace.jsonl view methods.

Read-side filter views that present trace.jsonl rows in the shape
historically delivered by execution_log.jsonl / audit.jsonl. Phase
25.5 ships these methods so new consumers can already migrate; the
physical files keep being written until a later cleanup phase
rewires the producers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.storage.run_store import RunStore


def _seed_trace(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore.create(home=tmp_path / ".localflow")


class TestReadTraceEvents:
    def test_missing_trace_returns_empty_list(self, store: RunStore):
        assert store.read_trace_events() == []

    def test_empty_trace_returns_empty_list(self, store: RunStore):
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_text("", encoding="utf-8")
        assert store.read_trace_events() == []

    def test_malformed_lines_are_skipped(self, store: RunStore):
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_text(
            '{"event": "ok", "payload": {}}\n'
            "this is not json\n"
            '{"event": "also_ok", "payload": {}}\n',
            encoding="utf-8",
        )
        rows = store.read_trace_events()
        assert len(rows) == 2
        assert rows[0]["event"] == "ok"
        assert rows[1]["event"] == "also_ok"


class TestExecutionLogView:
    def test_filters_to_action_lifecycle_only(self, store: RunStore):
        _seed_trace(
            store.trace_path,
            [
                {"ts": "t", "event": "action.start", "payload": {}},
                {"ts": "t", "event": "action.end", "payload": {}},
                {"ts": "t", "event": "policy.check", "payload": {}},
                {"ts": "t", "event": "rollback.entry", "payload": {}},
                # Events that should be FILTERED OUT — they belong in
                # the audit view, not execution_log_view.
                {"ts": "t", "event": "llm.call.end", "payload": {}},
                {"ts": "t", "event": "repair.triggered", "payload": {}},
            ],
        )
        rows = store.execution_log_view()
        events = [r["event"] for r in rows]
        assert events == [
            "action.start",
            "action.end",
            "policy.check",
            "rollback.entry",
        ]

    def test_empty_trace_returns_empty_view(self, store: RunStore):
        assert store.execution_log_view() == []

    def test_preserves_payload(self, store: RunStore):
        """The view passes payloads through untouched — Phase 25.1's
        observation field must reach consumers via the view."""
        _seed_trace(
            store.trace_path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-1",
                        "status": "ok",
                        "observation": {"action_type": "mkdir", "target": "x/"},
                    },
                }
            ],
        )
        rows = store.execution_log_view()
        assert len(rows) == 1
        obs = rows[0]["payload"]["observation"]
        assert obs["action_type"] == "mkdir"
        assert obs["target"] == "x/"


class TestAuditView:
    def test_filters_to_orchestration_events(self, store: RunStore):
        _seed_trace(
            store.trace_path,
            [
                {"ts": "t", "event": "llm.call.start", "payload": {}},
                {"ts": "t", "event": "llm.call.end", "payload": {}},
                {"ts": "t", "event": "repair.triggered", "payload": {}},
                {"ts": "t", "event": "plan.revised", "payload": {}},
                {"ts": "t", "event": "token.minted", "payload": {}},
                {"ts": "t", "event": "compute.action.start", "payload": {}},
                # Filtered out — execution_log territory.
                {"ts": "t", "event": "action.start", "payload": {}},
                {"ts": "t", "event": "policy.check", "payload": {}},
            ],
        )
        rows = store.audit_view()
        events = [r["event"] for r in rows]
        assert events == [
            "llm.call.start",
            "llm.call.end",
            "repair.triggered",
            "plan.revised",
            "token.minted",
            "compute.action.start",
        ]

    def test_compute_action_lifecycle_in_audit_view(self, store: RunStore):
        """ComputeAction events (Phase 23) belong in the audit view —
        they are user-facing orchestration moments, not low-level
        action dispatch."""
        _seed_trace(
            store.trace_path,
            [
                {"ts": "t", "event": "compute.action.start", "payload": {}},
                {"ts": "t", "event": "compute.action.end", "payload": {}},
                {"ts": "t", "event": "compute.sandbox.timeout", "payload": {}},
                {"ts": "t", "event": "compute.output.verified", "payload": {}},
            ],
        )
        rows = store.audit_view()
        assert len(rows) == 4
        events = {r["event"] for r in rows}
        assert events == {
            "compute.action.start",
            "compute.action.end",
            "compute.sandbox.timeout",
            "compute.output.verified",
        }


class TestViewsArePartitionsNotOverlapping:
    """Sanity: an event_type belongs to AT MOST one view. A reader
    iterating both views must not see the same row twice."""

    def test_action_end_is_in_execution_log_not_audit(self, store: RunStore):
        _seed_trace(
            store.trace_path,
            [{"ts": "t", "event": "action.end", "payload": {}}],
        )
        assert len(store.execution_log_view()) == 1
        assert len(store.audit_view()) == 0

    def test_llm_call_end_is_in_audit_not_execution_log(self, store: RunStore):
        _seed_trace(
            store.trace_path,
            [{"ts": "t", "event": "llm.call.end", "payload": {}}],
        )
        assert len(store.execution_log_view()) == 0
        assert len(store.audit_view()) == 1

    def test_verifier_check_in_neither_view(self, store: RunStore):
        """``verifier.check`` is a third axis — neither low-level
        execution nor user-orchestration. It stays available via
        ``read_trace_events()`` but isn't in either filtered view."""
        _seed_trace(
            store.trace_path,
            [{"ts": "t", "event": "verifier.check", "payload": {}}],
        )
        assert len(store.execution_log_view()) == 0
        assert len(store.audit_view()) == 0
        # But the raw reader sees it.
        assert len(store.read_trace_events()) == 1
