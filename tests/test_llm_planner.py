"""Phase 1 — LLM Planner tests using FakeLLMClient (no real API calls)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent import FakeLLMClient, LLMPlanner, PlannerFailure, plan_with_llm
from app.agent.prompts import TOOL_NAME
from app.harness.executor import Executor


def _good_payload(task_id: str) -> dict[str, Any]:
    """A minimal valid plan that should pass all gates."""
    return {
        "plan_id": "plan-test0001",
        "task_id": task_id,
        "summary": "Move PDFs into papers/, write an index.",
        "risk_summary": "Medium risk; all reversible.",
        "expected_outputs": ["papers/index.md"],
        "actions": [
            {
                "action_id": "a-001",
                "action_type": "mkdir",
                "target_path": "papers",
                "reason": "Create category dir for PDFs.",
                "risk_level": "low",
                "reversible": True,
                "requires_approval": True,
            },
            {
                "action_id": "a-002",
                "action_type": "move",
                "source_path": "a.pdf",
                "target_path": "papers/a.pdf",
                "reason": "PDF belongs in papers/.",
                "risk_level": "medium",
                "reversible": True,
                "requires_approval": True,
            },
            {
                "action_id": "a-003",
                "action_type": "move",
                "source_path": "b.pdf",
                "target_path": "papers/b.pdf",
                "reason": "PDF belongs in papers/.",
                "risk_level": "medium",
                "reversible": True,
                "requires_approval": True,
            },
            {
                "action_id": "a-004",
                "action_type": "index",
                "target_path": "papers/index.md",
                "reason": "Catalog of files in papers/.",
                "risk_level": "low",
                "reversible": True,
                "requires_approval": False,
                "metadata": {"content": "# papers/\n\n- a.pdf\n- b.pdf\n"},
            },
        ],
    }


# --------------------------------------------------------------------- happy path


def test_llm_planner_happy_path(task, snapshot) -> None:
    client = FakeLLMClient(payloads=[_good_payload(task.task_id)])
    plan = plan_with_llm(task, snapshot, client=client)

    assert plan.task_id == task.task_id
    assert len(plan.actions) == 4
    assert {a.action_type.value for a in plan.actions} == {"mkdir", "move", "index"}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["tool_name"] == TOOL_NAME
    # System prompt is non-empty and stable; messages contain the user prompt.
    assert "LocalFlow" in call["system"]
    assert call["messages"][0]["role"] == "user"
    assert task.user_goal in call["messages"][0]["content"]


# --------------------------------------------------------------------- repair


def test_llm_planner_repairs_schema_error(task, snapshot) -> None:
    bad = _good_payload(task.task_id)
    # Drop a required top-level field — Pydantic rejects at schema layer
    # before Policy Guard even sees the plan.
    del bad["summary"]
    good = _good_payload(task.task_id)
    client = FakeLLMClient(payloads=[bad, good])

    planner = LLMPlanner(client=client, max_attempts=3)
    plan = planner.plan(task, snapshot)

    assert plan.task_id == task.task_id
    assert len(client.calls) == 2
    # Second call must carry the failure-feedback turn.
    second_call_messages = client.calls[1]["messages"]
    assert second_call_messages[-1]["role"] == "user"
    last_content = second_call_messages[-1]["content"]
    assert isinstance(last_content, list)
    assert last_content[0]["type"] == "tool_result"
    assert last_content[0]["is_error"] is True
    # Attempt log captured the failure + the success.
    assert [a.outcome for a in planner.last_attempts] == ["schema_invalid", "accepted"]


def test_llm_planner_repairs_policy_violation(task, snapshot) -> None:
    bad = _good_payload(task.task_id)
    # Try to move a file outside the workspace — Policy Guard catches this.
    bad["actions"][1]["target_path"] = "../escape/a.pdf"
    good = _good_payload(task.task_id)
    client = FakeLLMClient(payloads=[bad, good])

    plan = plan_with_llm(task, snapshot, client=client, max_attempts=3)

    assert len(plan.actions) == 4
    assert len(client.calls) == 2


def test_llm_planner_rejects_wrong_task_id(task, snapshot) -> None:
    wrong = _good_payload("totally-bogus-id")
    good = _good_payload(task.task_id)
    client = FakeLLMClient(payloads=[wrong, good])

    plan = plan_with_llm(task, snapshot, client=client, max_attempts=3)
    assert plan.task_id == task.task_id
    assert len(client.calls) == 2


# --------------------------------------------------------------------- failure


def test_llm_planner_partial_plan_fallback_after_max_attempts(task, snapshot) -> None:
    """v0.16.1 — instead of raising on exhaustion, the planner now
    tries to salvage individually-valid actions from the last attempt
    into a degraded ActionPlan. The user sees the plan in dry-run +
    decides whether to execute the partial result."""
    bad = _good_payload(task.task_id)
    del bad["summary"]  # plan-level required field missing; Pydantic rejects whole plan
    # Three identical bad payloads — the model never learns.
    client = FakeLLMClient(payloads=[bad, bad, bad])

    plan = plan_with_llm(task, snapshot, client=client, max_attempts=3)
    # Three failed full-plan attempts → salvage path produces a degraded plan
    # with the validated actions kept + a diagnostic in summary.
    assert "PARTIAL" in plan.summary.upper() or "partial" in plan.summary
    assert plan.actions, "partial-plan fallback should keep at least one action"
    assert len(client.calls) == 3


def test_llm_planner_raises_when_no_salvage_possible(task, snapshot) -> None:
    """v0.16.1 — when the last attempt has zero parseable actions, the
    planner falls through to the original PlannerFailure path."""
    # Payload with malformed actions field (not a list) — Pydantic-parse
    # fails AND _salvage_actions finds nothing.
    bad = {
        "plan_id": "x",
        "task_id": task.task_id,
        "summary": "x",
        "actions": "not-a-list",
        "expected_outputs": [],
        "risk_summary": "x",
    }
    client = FakeLLMClient(payloads=[bad, bad, bad])
    with pytest.raises(PlannerFailure) as excinfo:
        plan_with_llm(task, snapshot, client=client, max_attempts=3)
    assert len(excinfo.value.attempts) == 3


# --------------------------------------------------------------------- integration


def test_llm_plan_runs_through_executor(task, snapshot, run_store, workspace) -> None:
    """Verify the LLM-produced plan is structurally identical enough to run
    through the existing Executor — i.e. the Harness Kernel does not need to
    know which planner produced the plan."""
    client = FakeLLMClient(payloads=[_good_payload(task.task_id)])
    plan = plan_with_llm(task, snapshot, client=client)

    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)

    assert outcome.success
    # All four actions ran (mkdir + 2 moves + 1 index).
    assert sum(1 for r in outcome.records if r.status.value == "success") == 4
    assert (workspace / "papers" / "a.pdf").exists()
    assert (workspace / "papers" / "b.pdf").exists()
    assert (workspace / "papers" / "index.md").exists()


# --------------------------------------------------------------------- forbidden actions


def test_llm_planner_rejects_delete_action(task, snapshot) -> None:
    """The model is explicitly told 'no delete'; if it tries anyway the
    Pydantic schema rejects it because `delete` is not in ActionType.
    Verify the planner converts that to a normal repair attempt."""
    bad = _good_payload(task.task_id)
    bad["actions"].append(
        {
            "action_id": "a-999",
            "action_type": "delete",  # not in our enum
            "source_path": "a.pdf",
            "reason": "Get rid of duplicate.",
            "risk_level": "high",
            "reversible": False,
            "requires_approval": True,
        }
    )
    good = _good_payload(task.task_id)
    client = FakeLLMClient(payloads=[bad, good])

    plan = plan_with_llm(task, snapshot, client=client, max_attempts=3)
    assert len(plan.actions) == 4
    assert "delete" not in {a.action_type.value for a in plan.actions}
