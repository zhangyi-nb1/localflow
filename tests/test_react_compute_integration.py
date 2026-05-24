"""Phase 26.3 — close the v0.23.0 ComputeAction reachability gap.

PHASES.md flagged: ``ActionType.PYTHON_COMPUTE`` is end-to-end
unreachable in v0.23.0 because no production code path emits one.
This test demonstrates the fix: with the v0.24.0 react loop +
allow_new_action_types, an LLM mid-loop decision can INSERT a
PYTHON_COMPUTE action against a plan that originally contained none.

The LLM here is a deterministic stub (no API call). The point is
to prove the dispatch path — schema, policy_guard, executor,
sandbox runtime, manifest, trace — all converge on a successful
ComputeAction even when the planner never proposed one.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from app.agent.client import StructuredResponse
from app.harness.executor import Executor
from app.harness.sandbox import SandboxRuntime
from app.harness.trace import TraceLogger
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RiskLevel,
)
from app.schemas.compute import ArtifactSpec, ComputeAction, SandboxPolicy
from app.storage.run_store import RunStore
from app.tools.scratch import ScratchWorkspace


@dataclass
class _StubLLMClient:
    """Deterministic LLM stub — returns the predetermined decisions in
    order."""

    decisions: list[LoopDecision]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def generate_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> StructuredResponse:
        self.calls.append(
            {
                "user_content": messages[0]["content"] if messages else "",
                "schema_enum": tool_schema["properties"]["replacement_action"]["anyOf"][1][
                    "properties"
                ]["action_type"]["enum"],
            }
        )
        if self._idx >= len(self.decisions):
            raise AssertionError(f"stub LLM exhausted at call {self._idx}; set up more decisions")
        decision = self.decisions[self._idx]
        self._idx += 1
        return StructuredResponse(
            tool_use_id=f"toolu_stub_{self._idx:03d}",
            payload=decision.model_dump(mode="json"),
            raw_assistant_content=[
                {
                    "type": "tool_use",
                    "id": f"toolu_stub_{self._idx:03d}",
                    "name": tool_name,
                    "input": decision.model_dump(mode="json"),
                }
            ],
            usage={"input_tokens": 0, "output_tokens": 0},
            stop_reason="tool_use",
        )


@pytest.fixture
def react_compute_executor(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "input.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        trace=trace,
        scratch_workspace=ScratchWorkspace(home=home),
        sandbox_runtime=SandboxRuntime(),
    )
    return executor, workspace


def _index_action(action_id: str, target: str, content: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.INDEX,
        target_path=target,
        reason="index",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={"content": content},
    )


def _compute_action(action_id: str) -> Action:
    """A PYTHON_COMPUTE action the LLM might propose mid-loop."""
    compute = ComputeAction(
        script=dedent(
            """
            # Sandbox cwd = scratch action root; outputs live in ./outputs/
            import json
            import os
            os.makedirs('outputs', exist_ok=True)
            with open('outputs/normalized.json', 'w') as f:
                json.dump({'cleaned': True}, f)
            """
        ).strip(),
        script_summary="Normalize the irregular CSV into a flat JSON.",
        inputs=[],
        expected_outputs=[
            ArtifactSpec(
                relative_path="outputs/normalized.json",
                description="Normalized CSV converted to JSON.",
            )
        ],
        sandbox_policy=SandboxPolicy(timeout_sec=10),
    )
    return Action(
        action_id=action_id,
        action_type=ActionType.PYTHON_COMPUTE,
        reason=(
            "Phase 26.3 demo — original plan had no compute action; LLM "
            "INSERTed one after observing the first action's output."
        ),
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )


class TestReactLoopReachesComputeAction:
    """The Phase 23 → Phase 26 reachability fix.

    Plan starts with one harmless INDEX action. The LLM, mid-loop,
    INSERTs a PYTHON_COMPUTE before the index. The compute runs
    through the same sandbox path Phase 23 set up, lands a
    DELETE_SCRATCH_DIR rollback entry, and finally the original
    INDEX still runs to completion.
    """

    def test_react_loop_can_insert_python_compute(self, react_compute_executor):
        executor, workspace = react_compute_executor

        # The original plan: one INDEX. The planner never emitted a
        # compute action — same shape as the v0.23.0 reachability gap.
        plan = ActionPlan(
            plan_id="plan-react-compute",
            task_id=executor.run_store.task_id,
            summary="One-index plan with no compute action originally.",
            actions=[
                _index_action(
                    "a-plan-1",
                    "report.md",
                    "# report\nplanned index",
                )
            ],
        )

        # The LLM's mid-loop decisions:
        #   1. INSERT a PYTHON_COMPUTE before the planned INDEX
        #   2. CONTINUE through the planned INDEX once compute landed
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.INSERT,
                    reason="prior step revealed irregular data; need a cleaning step",
                    replacement_action=_compute_action("a-react-compute-1"),
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="compute succeeded; proceed with the planned index",
                ),
            ]
        )

        config = ReactConfig(
            enabled=True,
            max_drift=3,
            allow_new_action_types=True,  # the Recipe-level escape hatch
        )

        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=config,
            llm_client=client,
        )

        # Both actions ran (INSERTed compute first, then the planned index).
        assert outcome.success, [r.error for r in outcome.records]
        action_ids = [r.action_id for r in outcome.records]
        assert action_ids == ["a-react-compute-1", "a-plan-1"]

        # The compute action's report.md is in the workspace.
        assert (workspace / "report.md").exists()

        # The compute landed a DELETE_SCRATCH_DIR entry — the sandbox
        # cleanup contract from Phase 23 still applies inside the
        # react loop.
        manifest = outcome.manifest
        op_types = {entry.op.value for entry in manifest.entries}
        assert "delete_scratch_dir" in op_types

    def test_loop_tool_schema_exposes_python_compute_when_opt_in(self, react_compute_executor):
        """When ``allow_new_action_types=True``, the LLM-facing tool
        schema must include ``python_compute`` in the action_type enum.
        Without this, the v0.23.0 reachability gap would persist —
        the LLM literally could not propose it."""
        executor, _ = react_compute_executor
        plan = ActionPlan(
            plan_id="p",
            task_id=executor.run_store.task_id,
            summary="",
            actions=[_index_action("a-1", "x.md", "x")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="ok",
                )
            ]
        )
        executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(
                enabled=True,
                max_drift=3,
                allow_new_action_types=True,
            ),
            llm_client=client,
        )
        assert client.calls, "react loop never consulted the LLM"
        enum_values = client.calls[0]["schema_enum"]
        assert "python_compute" in enum_values, (
            f"python_compute missing from the loop's action_type enum: {enum_values}"
        )


class TestReachabilityGapDocumentation:
    """Track that the fix is documented in the right spots."""

    def test_phases_md_marks_gap_fixed(self):
        text = (Path(__file__).parent.parent / "docs" / "PHASES.md").read_text()
        assert "Status update" in text
        assert "FIXED in Phase 26" in text

    def test_example_pack_readme_points_at_react_mode(self):
        text = (
            Path(__file__).parent.parent / "examples" / "compute_action_pack" / "README.md"
        ).read_text()
        assert "Phase 26" in text
        assert "react loop" in text.lower()
        assert "enable_react_mode" in text


def _trace_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTraceEvidence:
    """Trace.jsonl must show the lifecycle so an auditor can later
    grep ``loop.decision.applied`` and see a PYTHON_COMPUTE landed
    via INSERT."""

    def test_trace_shows_insert_decision_landing_python_compute(self, react_compute_executor):
        executor, _ = react_compute_executor
        plan = ActionPlan(
            plan_id="p",
            task_id=executor.run_store.task_id,
            summary="",
            actions=[_index_action("a-plan-1", "rep.md", "x")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.INSERT,
                    reason="demo: insert PYTHON_COMPUTE",
                    replacement_action=_compute_action("a-react-compute-1"),
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="planned action ok",
                ),
            ]
        )
        executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(
                enabled=True,
                max_drift=3,
                allow_new_action_types=True,
            ),
            llm_client=client,
        )

        rows = _trace_rows(executor.run_store.trace_path)
        # Pull out the applied-decision row that references the
        # compute action_id.
        applied_rows = [
            r
            for r in rows
            if r.get("event") == "loop.decision.applied"
            and r.get("payload", {}).get("action_id") == "a-react-compute-1"
        ]
        assert applied_rows, "no loop.decision.applied row for the inserted compute"
        assert "INSERT" in applied_rows[0]["payload"]["detail"]

        # And the compute action's own lifecycle events fired.
        compute_events = [r["event"] for r in rows if "compute." in r.get("event", "")]
        assert "compute.action.start" in compute_events
        assert "compute.action.end" in compute_events
