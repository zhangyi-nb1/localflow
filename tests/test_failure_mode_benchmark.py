"""Phase 37 — failure-mode ablation benchmark tests.

Deterministic (no LLM key). Asserts each mode's guard-on / guard-off
outcome, so the README numbers can't silently drift.
"""

from __future__ import annotations

import pytest

from app.eval.failure_modes import render_markdown_table, run_benchmark
from app.eval.failure_modes.schema import (
    STATUS_GAP,
    STATUS_MITIGATED,
    STATUS_PROCESS,
)


@pytest.fixture(autouse=True)
def _force_no_llm(monkeypatch):
    """The false_completion scenario picks a judge from the env; pin the
    deterministic lexical path so the benchmark is reproducible even if a
    client is resolvable in the environment."""
    # grounding's ground_review is called with an explicit LexicalClaimJudge
    # inside the benchmark, so no patch is strictly needed there — but guard
    # the recipe-verifier path used elsewhere just in case.
    monkeypatch.setattr(
        "app.eval.recipe_verifiers.grounding.get_default_client_or_none",
        lambda: None,
        raising=False,
    )


def _by_mode(reports):
    return {r.mode: r for r in reports}


def test_six_modes_present():
    reports = run_benchmark()
    assert len(reports) == 6
    assert [r.feishu_id for r in reports] == [1, 2, 3, 4, 5, 6]


def test_goal_drift_budget_caps_deviation():
    r = _by_mode(run_benchmark())["goal_drift"]
    assert r.status == STATUS_MITIGATED
    assert r.unguarded_failed is True
    assert r.guarded_failed is False
    assert r.guard_helps


def test_false_completion_gate_catches_hallucination():
    r = _by_mode(run_benchmark())["false_completion"]
    assert r.status == STATUS_MITIGATED
    assert r.unguarded_failed is True  # no gate → fabricated claim ships
    assert r.guarded_failed is False  # gate fails → not shipped
    assert r.guard_helps


def test_tool_runaway_policy_guard_blocks_escape():
    r = _by_mode(run_benchmark())["tool_runaway"]
    assert r.status == STATUS_MITIGATED
    assert r.unguarded_failed is True
    assert r.guarded_failed is False
    assert r.guard_helps


def test_quality_entropy_verifier_flags_missing_deliverable():
    r = _by_mode(run_benchmark())["quality_entropy"]
    assert r.status == STATUS_MITIGATED
    assert r.unguarded_failed is True
    assert r.guarded_failed is False
    assert r.guard_helps


def test_context_rot_is_honest_gap():
    r = _by_mode(run_benchmark())["context_rot"]
    assert r.status == STATUS_GAP
    # Honest: the guard does NOT help — both modes fail.
    assert r.guarded_failed is True
    assert r.unguarded_failed is True
    assert not r.guard_helps


def test_harness_self_is_process_control():
    r = _by_mode(run_benchmark())["harness_self"]
    assert r.status == STATUS_PROCESS
    assert r.guarded_failed is None
    assert r.unguarded_failed is None


def test_harness_self_ledger_ratio_matches_docs():
    """Guard the integrity row against ledger drift (rule E).

    The harness_self detail quotes the §10.7 kernel-touch ratio. As of
    v0.35.0 the ledger (docs/PHASES.md) + README say 44 deliveries. This
    is the project's honesty showpiece, so a stale number here is the
    worst place to have one — pin it so it can never silently drift."""
    r = _by_mode(run_benchmark())["harness_self"]
    assert "4 deliberate exceptions / 44 deliveries" in r.detail
    # The old stale value must never reappear.
    assert "43 deliveries" not in r.detail


def test_exactly_four_runtime_mitigations():
    reports = run_benchmark()
    mitigated = [r for r in reports if r.guard_helps]
    assert len(mitigated) == 4
    runtime = [r for r in reports if r.status == STATUS_MITIGATED]
    assert len(runtime) == 4


def test_render_table_includes_gap_and_process_rows():
    table = render_markdown_table(run_benchmark())
    assert "context_rot" in table
    assert "gap" in table
    assert "process" in table
    assert "4/4" in table  # the honest headline
    # Honesty: the table must NOT claim 6/6.
    assert "6/6" not in table
