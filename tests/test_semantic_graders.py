"""Phase 13 — semantic grader unit tests.

These tests exercise the three starter graders in
``app/eval/graders/semantic.py`` against synthetic GraderContexts.
The LLM call is patched out (no API traffic) so the assertions
focus on each grader's branching logic + hint shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.agent.judge import JudgeVerdict
from app.eval.graders import semantic
from app.eval.schema import EvalTask, GraderContext
from app.schemas import (
    Action,
    ActionPlan,
    RollbackManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)


def _build_ctx(
    *,
    workspace: Path,
    goal: str,
    expected_outputs: list[str],
    plan_actions: list[Action] | None = None,
) -> GraderContext:
    task_spec = TaskSpec(
        task_id="t-1",
        user_goal=goal,
        workspace_root=str(workspace),
        skill="agent",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )
    plan = ActionPlan(
        plan_id="plan-1",
        task_id="t-1",
        summary="seed",
        actions=plan_actions or [],
        expected_outputs=expected_outputs,
        risk_summary="low",
    )
    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root=str(workspace),
        files=[],
        total_files=0,
        total_size_bytes=0,
    )
    eval_task = EvalTask.model_construct(
        task_id="t-1",
        title=goal[:40],
        goal=goal,
        skill="agent",
        planner="rule",
        expected_outputs=expected_outputs,
        workspace_seed=[],
        graders=[],
        must_pass=[],
        stages=None,
    )
    verification = VerificationResult(
        task_id="t-1",
        run_id="t-1",
        passed=True,
        checks=[VerificationCheck(name="x", passed=True)],
        failed_checks=[],
        summary="ok",
        created_at=datetime.now(timezone.utc),
    )
    return GraderContext(
        task=eval_task,
        task_spec=task_spec,
        plan=plan,
        snapshot_before=snap,
        snapshot_after=None,
        execution_records=[],
        manifest=RollbackManifest(task_id="t-1", run_id="t-1", entries=[], file_hashes_before={}),
        verification=verification,
        trace_events=[],
        workspace_path=workspace,
        seed_hashes={},
    )


# ─────────────────────────── output_addresses_goal


def test_output_addresses_goal_skips_when_no_llm(tmp_path: Path) -> None:
    """No LLM client → grader returns passed=True with a 'skipped'
    detail. CI environments without API keys must not fail."""
    ctx = _build_ctx(workspace=tmp_path, goal="analyze the data", expected_outputs=["report.md"])
    (tmp_path / "report.md").write_text("real content", encoding="utf-8")
    with patch("app.eval.graders.semantic.get_default_client_or_none", return_value=None):
        v = semantic.output_addresses_goal(ctx)
    assert v.passed is True
    assert "skipped" in v.detail.lower()


def test_output_addresses_goal_calls_judge_when_text_outputs_present(tmp_path: Path) -> None:
    """When an LLM client + text outputs are present, the grader
    calls judge() and propagates the verdict."""
    ctx = _build_ctx(
        workspace=tmp_path, goal="summarize the workspace", expected_outputs=["index.md"]
    )
    (tmp_path / "index.md").write_text("# Index\n\n- file_a.txt\n- file_b.txt", encoding="utf-8")
    fake_verdict = JudgeVerdict(
        verdict=False,
        reason="generic boilerplate",
        suggested_hint="re-plan with file-specific descriptions",
        token_usage={"input": 100, "output": 20},
    )
    with (
        patch(
            "app.eval.graders.semantic.get_default_client_or_none",
            return_value=object(),  # truthy stub — actual client not called
        ),
        patch("app.eval.graders.semantic.judge", return_value=fake_verdict),
    ):
        v = semantic.output_addresses_goal(ctx)
    assert v.passed is False
    assert "generic" in v.detail


def test_output_addresses_goal_skips_when_no_text_outputs(tmp_path: Path) -> None:
    """expected_outputs lists only binary files → grader skips
    (passed=True) because it can't read content to judge."""
    ctx = _build_ctx(workspace=tmp_path, goal="render charts", expected_outputs=["chart.png"])
    (tmp_path / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with patch(
        "app.eval.graders.semantic.get_default_client_or_none",
        return_value=object(),
    ):
        v = semantic.output_addresses_goal(ctx)
    assert v.passed is True
    assert "no text outputs" in v.detail.lower()


# ─────────────────────────── summary_grounded


def test_summary_grounded_skips_when_no_summary_file(tmp_path: Path) -> None:
    """No matching index.md / summary.md / analysis_report.md in
    expected_outputs → grader skips (passed=True)."""
    ctx = _build_ctx(workspace=tmp_path, goal="anything", expected_outputs=["data.csv"])
    v = semantic.summary_grounded(ctx)
    assert v.passed is True


def test_summary_grounded_reads_index_md(tmp_path: Path) -> None:
    """Renders the workspace listing + index.md, then calls judge()."""
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "attention.pdf").write_bytes(b"%PDF")
    (tmp_path / "index.md").write_text("# Papers\n\n- attention.pdf", encoding="utf-8")
    ctx = _build_ctx(workspace=tmp_path, goal="organize", expected_outputs=["index.md"])
    fake = JudgeVerdict(
        verdict=True, reason="references actual files", suggested_hint="", token_usage={}
    )
    with (
        patch("app.eval.graders.semantic.get_default_client_or_none", return_value=object()),
        patch("app.eval.graders.semantic.judge", return_value=fake),
    ):
        v = semantic.summary_grounded(ctx)
    assert v.passed is True


# ─────────────────────────── analysis_result_nonempty


def test_analysis_result_nonempty_skips_when_report_missing(tmp_path: Path) -> None:
    """No analysis_report.md → grader skips (data_analyzer didn't run)."""
    ctx = _build_ctx(workspace=tmp_path, goal="anything", expected_outputs=[])
    v = semantic.analysis_result_nonempty(ctx)
    assert v.passed is True
    assert "no analysis_report" in v.detail.lower()


def test_analysis_result_nonempty_rejects_all_empty_report(tmp_path: Path) -> None:
    """Every analysis ended in EMPTY_RESULT / INVALID_SPEC → fail."""
    report = (
        "# Data Analysis Report\n\n"
        '### `data.csv` <a id="data-csv-1"></a>\n\n'
        "**Outcome**: `empty_result`\n\n"
        "_(empty result)_\n\n"
        '### `more.xlsx` <a id="more-xlsx-2"></a>\n\n'
        "**Outcome**: `invalid_spec`\n"
    )
    (tmp_path / "analysis_report.md").write_text(report, encoding="utf-8")
    ctx = _build_ctx(workspace=tmp_path, goal="analyze", expected_outputs=["analysis_report.md"])
    v = semantic.analysis_result_nonempty(ctx)
    assert v.passed is False
    assert "every analysis" in v.detail.lower()


def test_analysis_result_nonempty_accepts_partial_results(tmp_path: Path) -> None:
    """At least one non-empty analysis → passes (with the proportion
    surfaced as score)."""
    report = (
        '### `a.csv` <a id="a-csv-1"></a>\n\n'
        "**Outcome**: `ok`\n\n"
        "**Summary**: 42 rows aggregated.\n\n"
        '### `b.csv` <a id="b-csv-2"></a>\n\n'
        "**Outcome**: `empty_result`\n"
    )
    (tmp_path / "analysis_report.md").write_text(report, encoding="utf-8")
    ctx = _build_ctx(workspace=tmp_path, goal="analyze", expected_outputs=["analysis_report.md"])
    v = semantic.analysis_result_nonempty(ctx)
    assert v.passed is True
    assert v.score is not None and 0 < v.score < 1


# ─────────────────────────── registry sanity


def test_all_three_starter_graders_registered() -> None:
    """The starter set declared in semantic_verifier.SEMANTIC_GRADER_NAMES
    must all be present in the grader registry — guards against an
    accidental import-order regression."""
    from app.eval.graders import get as get_grader
    from app.harness.semantic_verifier import SEMANTIC_GRADER_NAMES

    for name in SEMANTIC_GRADER_NAMES:
        get_grader(name)  # raises KeyError if missing
