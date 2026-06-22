"""R5 — trace digest injected into the repair planner's user_hint.

The recipe repair loop already feeds the verifier's curated
``suggested_hint`` to the re-planner. R5 additionally appends an
execution-trace digest (failed verifier checks + recent action
observations) so the agent re-plans against what actually happened.
KB ch11 §第一阶段 "可观测性被放大" (L182): the trace's consumer is now
the agent, not just the human.

These tests pin (a) the digest builder and (b) the injection wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.eval.recipe_verifiers import RecipeVerification, RecipeVerifierVerdict
from app.harness.recipe_repair import _build_trace_digest, run_recipe_repair
from app.schemas import RecipeSpec


def _write_trace(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_digest_empty_when_no_trace(tmp_path: Path) -> None:
    assert _build_trace_digest(tmp_path / "missing.jsonl") == ""


def test_digest_extracts_failed_checks_and_actions(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    _write_trace(
        trace,
        [
            {"event": "verifier.check", "payload": {"status": "ok", "detail": "x: ok"}},
            {
                "event": "verifier.check",
                "payload": {"status": "fail", "detail": "claim_grounding: 2 ungrounded"},
            },
            {
                "event": "action.end",
                "payload": {
                    "status": "ok",
                    "observation": {"action_type": "move", "target": "text/a.txt"},
                },
            },
            {
                "event": "action.end",
                "payload": {
                    "status": "fail",
                    "observation": {"action_type": "mkdir", "target": "images"},
                },
            },
        ],
    )
    digest = _build_trace_digest(trace)
    assert "Failed verifier checks" in digest
    assert "claim_grounding: 2 ungrounded" in digest
    # passing check is NOT included
    assert "x: ok" not in digest
    assert "move text/a.txt [ok]" in digest
    assert "mkdir images [fail]" in digest


def test_digest_truncates_to_max_chars(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    rows = [
        {
            "event": "action.end",
            "payload": {"status": "ok", "observation": {"action_type": "move", "target": f"f{i}"}},
        }
        for i in range(100)
    ]
    _write_trace(trace, rows)
    digest = _build_trace_digest(trace, max_actions=8, max_chars=300)
    assert len(digest) <= 300
    # only the last max_actions are shown
    assert "last 8" in digest


def test_digest_tolerates_malformed_lines(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        'not json\n{"event": "action.end", "payload": '
        '{"status": "ok", "observation": {"action_type": "copy", "target": "z"}}}\n',
        encoding="utf-8",
    )
    digest = _build_trace_digest(trace)
    assert "copy z [ok]" in digest


# ───────────────────────────── injection into the repair hint


class _StoreWithTrace:
    """Minimal RunStore stub whose trace_path points at a real file."""

    task_id = "t-r5"

    def __init__(self, trace_path: Path) -> None:
        self._trace_path = trace_path

    @property
    def trace_path(self) -> Path:
        return self._trace_path

    @property
    def stages_root(self) -> Path:
        return Path("/dev/null")

    @property
    def rollback_path(self) -> Path:
        return Path("/dev/null")


def _recipe() -> RecipeSpec:
    return RecipeSpec.model_validate(
        {
            "name": "demo",
            "title": "demo",
            "description": "t",
            "stages": [
                {"stage_id": "s1", "title": "x", "skill": "folder_organizer"},
                {"stage_id": "s2", "title": "y", "skill": "agent", "planner": "llm"},
            ],
            "verifiers": ["cov"],
            "repair_policy": {"enabled": True, "max_rounds": 1},
        }
    )


def test_trace_digest_is_appended_to_stage_hint(tmp_path: Path) -> None:
    """The graph handed to replay_from_stage must carry the verifier hint
    PLUS the trace digest in stage_hints, and the attempt records it."""
    trace = tmp_path / "trace.jsonl"
    _write_trace(
        trace,
        [
            {
                "event": "verifier.check",
                "payload": {"status": "fail", "detail": "cov: missing section"},
            },
            {
                "event": "action.end",
                "payload": {
                    "status": "ok",
                    "observation": {"action_type": "summarize", "target": "review.md"},
                },
            },
        ],
    )
    recipe = _recipe()
    initial = RecipeVerification.from_verdicts(
        run_id="t-r5",
        recipe_name="demo",
        verdicts=[
            RecipeVerifierVerdict(
                name="cov", passed=False, detail="failed", suggested_hint="add the missing section"
            )
        ],
    )
    captured = {}

    def _fake_replay(*, graph, run_store, from_stage, trace=None):
        captured["graph"] = graph
        return None

    with (
        patch("app.harness.recipe_repair.replay_from_stage", _fake_replay),
        patch(
            "app.harness.recipe_repair.run_all",
            return_value=[RecipeVerifierVerdict(name="cov", passed=True, detail="ok")],
        ),
        patch("app.harness.recipe_repair._aggregate_moves", return_value={}),
        patch("app.harness.recipe_repair._aggregate_snapshot_inputs", return_value=[]),
    ):
        result = run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=_StoreWithTrace(trace),
            initial_verification=initial,
        )

    hint = captured["graph"].stage_hints["s2"]
    assert "add the missing section" in hint  # the verifier hint
    assert "Execution-trace evidence" in hint  # the digest header
    assert "cov: missing section" in hint  # failed check from the trace
    assert "summarize review.md [ok]" in hint  # action observation
    # the attempt records the digest separately from the bare hint
    attempt = result.attempts[0]
    assert attempt.suggested_hint == "add the missing section"
    assert "summarize review.md [ok]" in attempt.trace_digest
