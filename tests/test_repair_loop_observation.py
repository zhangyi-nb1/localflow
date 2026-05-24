"""Phase 25.6 — repair loop reads ActionTraceEvent observation.

The Phase 25.1 executor writes one structured ``observation`` dict
per ACTION_END row. Phase 25.6 exercises that data by enriching the
repair loop's user_hint to the LLM: when the previous execution had
failed actions, their action_type / source / target / error get
appended to the verifier's suggested_hint as a structured bullet
list. The LLM revising the plan then sees WHY each action failed,
not just the high-level grader text.

These tests focus on the helper ``_format_failed_action_context`` in
isolation — driving the full repair_loop with a stubbed skill would
duplicate test_repair_loop.py's existing scaffolding without adding
incremental coverage. The integration-level guarantee (the hint is
actually threaded through) is mechanical: ``hint = f'{hint}\\n\\n
{failure_ctx}'.strip()`` between the read and the call to
``control_loop.run_revise(hint, ...)``.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.harness.repair_loop import _format_failed_action_context


def _write_trace(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestEmptyAndMissingCases:
    def test_missing_trace_file_returns_empty_string(self, tmp_path: Path) -> None:
        assert _format_failed_action_context(tmp_path / "nope.jsonl") == ""

    def test_empty_trace_returns_empty_string(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        path.write_text("", encoding="utf-8")
        assert _format_failed_action_context(path) == ""

    def test_trace_with_only_passing_actions_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "2026-05-24T00:00:00Z",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-001",
                        "status": "ok",
                        "observation": {"action_type": "mkdir", "target": "sub/"},
                    },
                }
            ],
        )
        assert _format_failed_action_context(path) == ""

    def test_non_action_rows_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "llm.call.end",
                    "payload": {"status": "fail", "detail": "model timeout"},
                },
                {
                    "ts": "t",
                    "event": "policy.check",
                    "payload": {"status": "blocked", "detail": "forbidden"},
                },
            ],
        )
        # Only ``action.end`` with ``status="fail"`` should surface;
        # other event types are someone else's concern.
        assert _format_failed_action_context(path) == ""


class TestFormatting:
    def test_single_failed_action_appears_in_output(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-007",
                        "status": "fail",
                        "detail": "mkdir failed: permission denied",
                        "observation": {
                            "action_type": "mkdir",
                            "target": "restricted/sub/",
                            "error": "PermissionError: restricted/sub/",
                        },
                    },
                }
            ],
        )
        out = _format_failed_action_context(path)
        assert "a-007" in out
        assert "mkdir" in out
        assert "restricted/sub/" in out
        assert "PermissionError" in out
        assert out.startswith("Prior execution had these failed actions")

    def test_multiple_failures_each_get_a_bullet(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-001",
                        "status": "fail",
                        "observation": {
                            "action_type": "move",
                            "source": "in.csv",
                            "target": "out/in.csv",
                            "error": "FileNotFoundError: in.csv",
                        },
                    },
                },
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-002",
                        "status": "fail",
                        "observation": {
                            "action_type": "index",
                            "target": "summary.md",
                            "error": "RuntimeError: empty body",
                        },
                    },
                },
            ],
        )
        out = _format_failed_action_context(path)
        # Both action_ids surface.
        assert "a-001" in out
        assert "a-002" in out
        # Two bullet lines + 1 header line + 2 error lines = 5 lines.
        assert out.count("\n- ") == 2

    def test_passing_actions_filtered_out_of_failure_block(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-good",
                        "status": "ok",
                        "observation": {"action_type": "mkdir", "target": "x/"},
                    },
                },
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-bad",
                        "status": "fail",
                        "observation": {
                            "action_type": "index",
                            "target": "report.md",
                            "error": "boom",
                        },
                    },
                },
            ],
        )
        out = _format_failed_action_context(path)
        assert "a-bad" in out
        assert "a-good" not in out

    def test_long_error_truncated_to_400_chars(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        long_err = "X" * 1000
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-1",
                        "status": "fail",
                        "observation": {"action_type": "move", "error": long_err},
                    },
                }
            ],
        )
        out = _format_failed_action_context(path)
        # The error line itself must be capped — token budget matters
        # when this gets prepended to an LLM prompt.
        for line in out.splitlines():
            if line.lstrip().startswith("error:"):
                assert len(line) <= 410, f"error line not truncated: {len(line)} chars"


class TestPrePhase25_1CompatibilityFallback:
    """Trace rows written by a pre-Phase-25.1 kernel will not have an
    ``observation`` field. The helper should still produce a bullet
    (using ``detail`` as the fallback) so old traces aren't silently
    invisible to the repair loop."""

    def test_no_observation_falls_back_to_detail(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "t",
                    "event": "action.end",
                    "payload": {
                        "action_id": "a-old",
                        "status": "fail",
                        "detail": "RuntimeError: old kernel error",
                        # NOTE: no "observation" key — pre-Phase-25.1.
                    },
                }
            ],
        )
        out = _format_failed_action_context(path)
        assert "a-old" in out
        # action_type / paths are missing — bullet still gets emitted,
        # the error line falls back to ``detail``.
        assert "RuntimeError: old kernel error" in out
