"""Phase 26.1 — execute-stage react loop (Route B: stage spine + step-by-step).

Implements the inner loop of the v0.24.0 react execution mode. The
outer plan/dry-run/approval/verify/rollback spine stays untouched;
this module is invoked by ``Executor.execute(react_mode=True)`` and
returns the same ``ExecutionOutcome`` shape the batch path returns.

The loop logic:

  1. Pop the next planned action.
  2. Ask the LLM what to do (CONTINUE / REPLACE / INSERT / SKIP / ABORT).
  3. Apply the decision against the remaining queue:
       - CONTINUE: run the popped action as-is.
       - REPLACE:  run ``replacement_action`` instead. Costs 1 drift.
       - INSERT:   run ``replacement_action`` first, then re-queue the popped action. Costs 1 drift.
       - SKIP:     do not run the popped action. Costs 1 drift.
       - ABORT:    end the loop, hand back to verify with what ran so far.
  4. When ``drift_used >= max_drift``, force CONTINUE / ABORT and ignore other shapes.
  5. When the LLM call times out / raises / returns malformed JSON, log
     a fail event and fall back to CONTINUE for this turn (the planned
     action runs as scheduled).

§10.7 invariant: every action that gets dispatched still passes
through ``Executor._run_action_with_policy_check`` — same
policy_guard, same trace emission, same rollback manifest entry.
The react loop adds ORCHESTRATION, not new dispatch paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.schemas import (
    Action,
    ActionPlan,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RollbackManifest,
)
from app.schemas.execution import ExecutionRecord, ExecutionStatus
from app.schemas.trace import FailureType, TraceEventType

# Phase 30.1 — LLMClient Protocol now lives in localflow_kernel.llm.
# react_loop is kernel-resident so it imports from the canonical kernel
# location. Concrete provider clients (AnthropicClient / FakeLLMClient)
# still live in app.agent.client and consume the Protocol from
# localflow_kernel via a back-compat re-export.
from localflow_kernel.llm import LLMClient, LLMClientError
from localflow_kernel.react_prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_loop_decision_tool_schema,
    render_loop_user_prompt,
)

if TYPE_CHECKING:
    from app.harness.executor import ExecutionOutcome, Executor


@dataclass
class _LoopState:
    """Internal bookkeeping for one react loop run."""

    plan: ActionPlan
    queue: list[Action]
    """Mutable copy of ``plan.actions`` — decisions mutate this in place."""
    records: list[ExecutionRecord]
    manifest: RollbackManifest
    drift_used: int = 0
    last_action_id: str | None = None
    last_observation: dict[str, Any] | None = None
    last_status: str = "ok"
    all_ok: bool = True
    aborted: bool = False
    """Set when an LLM ABORT decision lands. Loop exits, verify still runs."""
    fallback_to_batch: bool = False
    """Set when LLM fails repeatedly or drift exhausts the budget; the
    runtime stops consulting the LLM and processes the rest of the
    queue as a vanilla batch."""


def run_react_loop(
    executor: "Executor",
    plan: ActionPlan,
    *,
    llm_client: LLMClient | None,
    config: ReactConfig,
) -> "ExecutionOutcome":
    """Execute ``plan`` step-by-step with LLM consultations between
    actions. Returns the standard ``ExecutionOutcome``.

    When ``config.enabled`` is False OR ``llm_client`` is None, this
    function returns immediately by delegating to the batch executor
    (``executor.execute(plan, approved=True)``). This is the safe
    default: a caller that asks for react_mode but didn't wire an
    LLM client cannot accidentally run a degraded loop.
    """
    # Local import — avoids the Executor ↔ react_loop cycle.
    from app.harness.executor import ExecutionOutcome

    if not config.enabled or llm_client is None:
        return executor.execute(plan, approved=True)

    run_id = executor.run_store.task_id
    manifest = RollbackManifest(run_id=run_id, task_id=plan.task_id)
    executor.audit.log(
        "execute.react.start",
        run_id=run_id,
        plan_id=plan.plan_id,
        max_drift=config.max_drift,
    )

    state = _LoopState(
        plan=plan,
        queue=list(plan.actions),
        records=[],
        manifest=manifest,
    )

    while state.queue and not state.aborted:
        if state.fallback_to_batch:
            # Process every remaining action through the regular
            # policy-checked dispatch and stop consulting the LLM.
            for action in state.queue:
                record = _dispatch_one(executor, action, run_id, state.manifest, plan)
                state.records.append(record)
                if record.status == ExecutionStatus.FAILED:
                    state.all_ok = False
            state.queue.clear()
            break

        # Consult the LLM about the next action.
        decision = _consult_llm(
            executor=executor,
            llm_client=llm_client,
            config=config,
            plan=plan,
            state=state,
        )

        if decision is None:
            # LLM call failed in a way we couldn't recover (timeout,
            # auth error, etc.). Fall back to batch for the remainder.
            state.fallback_to_batch = True
            continue

        _apply_decision(executor, state, decision, run_id, plan, config)

    # Persist manifest + records (mirrors Executor.execute() tail).
    executor.run_store.save_rollback(state.manifest)
    executor.run_store.write_json(
        executor.run_store.actions_path,
        [r.model_dump(mode="json") for r in state.records],
    )
    executor.audit.log(
        "execute.react.end",
        run_id=run_id,
        success=state.all_ok,
        total=len(state.records),
        drift_used=state.drift_used,
        aborted=state.aborted,
        fallback_to_batch=state.fallback_to_batch,
    )

    return ExecutionOutcome(
        run_id=run_id,
        records=state.records,
        manifest=state.manifest,
        success=state.all_ok,
    )


# ────────────────────────────────────────────────────────────── helpers


def _dispatch_one(
    executor: "Executor",
    action: Action,
    run_id: str,
    manifest: RollbackManifest,
    plan: ActionPlan,
) -> ExecutionRecord:
    """Run one action through executor's existing policy-checked
    dispatch path. Mirrors the per-action body of
    ``Executor.execute()`` so the react loop reuses every safety net.
    """
    from app.harness.policy_guard import evaluate_action

    decision = evaluate_action(
        executor.workspace_root,
        action,
        forbidden_actions=executor.forbidden_actions,
        forbidden_paths=executor.forbidden_paths,
    )
    if not decision.allowed:
        err = "; ".join(decision.reasons)
        executor.exec_log.write(
            "action.end",
            {
                "action_id": action.action_id,
                "status": ExecutionStatus.FAILED.value,
                "error": f"policy_violation: {err}",
            },
        )
        executor._emit_trace(
            TraceEventType.POLICY_CHECK,
            status="blocked",
            action_id=action.action_id,
            detail=err,
            payload={"task_id": plan.task_id, "reasons": list(decision.reasons)},
        )
        from datetime import datetime, timezone

        return ExecutionRecord(
            run_id=run_id,
            action_id=action.action_id,
            status=ExecutionStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            error=f"policy_violation: {err}",
        )

    # Phase 27.2 — apply ConfirmationPolicy inside the react loop.
    # An LLM-proposed REPLACE / INSERT action is just as eligible
    # for user approval as a planner-emitted one. ``_policy_check``
    # returns None when no policy is wired (the v0.24.x path);
    # otherwise it consults the policy + the optional approver.
    policy_decision = executor._policy_check(action)
    if policy_decision is not None and not policy_decision.approved:
        from datetime import datetime, timezone

        executor.exec_log.write(
            "action.end",
            {
                "action_id": action.action_id,
                "status": ExecutionStatus.FAILED.value,
                "error": f"policy_rejected: {policy_decision.reason}",
            },
        )
        executor._emit_trace(
            TraceEventType.POLICY_CHECK,
            status="blocked",
            failure_type=FailureType.POLICY_BLOCKED,
            action_id=action.action_id,
            detail=f"react-loop policy rejection: {policy_decision.reason}",
            payload={
                "task_id": plan.task_id,
                "policy_decision": policy_decision.reason,
            },
        )
        return ExecutionRecord(
            run_id=run_id,
            action_id=action.action_id,
            status=ExecutionStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            error=f"user_rejected: {policy_decision.reason}",
        )

    return executor._run_one(action, run_id, manifest, plan=plan)


def _consult_llm(
    *,
    executor: "Executor",
    llm_client: LLMClient,
    config: ReactConfig,
    plan: ActionPlan,
    state: _LoopState,
) -> LoopDecision | None:
    """Ask the LLM what to do next. Returns ``None`` on unrecoverable
    failure (caller should fall back to batch)."""
    allowed_types = (
        list(plan.task_id and [])  # placeholder; real allowed list passed below
    )
    # The executor's task knows what action_types are legal; surface
    # them via plan.actions' types as a proxy. If the recipe enabled
    # ``allow_new_action_types``, the prompt lifts the restriction.
    if config.allow_new_action_types:
        allowed_types = []  # no restriction in the prompt schema
    else:
        allowed_types = sorted({a.action_type.value for a in plan.actions})

    next_action = state.queue[0]

    executor._emit_trace(
        TraceEventType.LOOP_DECISION_REQUESTED,
        action_id=next_action.action_id,
        detail=f"react step before {next_action.action_id}",
        payload={
            "next_action_type": next_action.action_type.value,
            "drift_used": state.drift_used,
            "drift_budget": config.max_drift,
        },
    )

    user_prompt = render_loop_user_prompt(
        last_action_id=state.last_action_id or "(none)",
        last_observation=state.last_observation,
        last_status=state.last_status,
        remaining_actions=[a.model_dump(mode="json") for a in state.queue],
        drift_used=state.drift_used,
        drift_budget=config.max_drift,
        allowed_action_types=allowed_types or [],
    )
    tool_schema = build_loop_decision_tool_schema(allowed_action_types=allowed_types or None)

    try:
        response = llm_client.generate_structured(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tool_name=TOOL_NAME,
            tool_description=TOOL_DESCRIPTION,
            tool_schema=tool_schema,
        )
    except LLMClientError as exc:
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_DECIDED,
            status="fail",
            action_id=next_action.action_id,
            failure_type=FailureType.UNKNOWN,
            detail=f"llm error: {exc}",
        )
        return None

    try:
        decision = LoopDecision.model_validate(response.payload)
    except ValidationError as exc:
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_DECIDED,
            status="fail",
            action_id=next_action.action_id,
            failure_type=FailureType.SCHEMA_INVALID,
            detail=f"malformed decision: {exc}",
        )
        return None
    except (TypeError, json.JSONDecodeError) as exc:
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_DECIDED,
            status="fail",
            action_id=next_action.action_id,
            failure_type=FailureType.SCHEMA_INVALID,
            detail=f"unparseable decision: {exc}",
        )
        return None

    executor._emit_trace(
        TraceEventType.LOOP_DECISION_DECIDED,
        status="ok",
        action_id=next_action.action_id,
        detail=f"decision={decision.decision_type.value} reason={decision.reason[:80]!r}",
        payload={
            "decision_type": decision.decision_type.value,
            "reason": decision.reason[:300],
            "has_replacement": decision.replacement_action is not None,
        },
    )
    return decision


def _apply_decision(
    executor: "Executor",
    state: _LoopState,
    decision: LoopDecision,
    run_id: str,
    plan: ActionPlan,
    config: ReactConfig,
) -> None:
    """Mutate ``state`` in place to reflect the LLM decision."""
    counts_as_drift = decision.decision_type in (
        LoopDecisionType.REPLACE,
        LoopDecisionType.INSERT,
        LoopDecisionType.SKIP,
    )
    if counts_as_drift and state.drift_used >= config.max_drift:
        # Treat the over-budget decision as CONTINUE and warn via trace.
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            status="blocked",
            action_id=state.queue[0].action_id,
            failure_type=FailureType.POLICY_BLOCKED,
            detail=(
                f"drift budget exhausted ({state.drift_used}/{config.max_drift}); "
                f"forcing CONTINUE for {state.queue[0].action_id}"
            ),
        )
        # fall through to CONTINUE branch below
        decision = LoopDecision(
            decision_type=LoopDecisionType.CONTINUE,
            reason="drift budget exhausted",
        )

    if decision.decision_type == LoopDecisionType.ABORT:
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            action_id=state.queue[0].action_id,
            detail="ABORT — handing control back to verify stage",
        )
        state.aborted = True
        return

    if decision.decision_type == LoopDecisionType.SKIP:
        skipped = state.queue.pop(0)
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            action_id=skipped.action_id,
            detail=f"SKIP — action {skipped.action_id} dropped",
        )
        state.drift_used += 1
        return

    # CONTINUE / REPLACE / INSERT all run an action — the question is
    # *which* action.
    if decision.decision_type == LoopDecisionType.REPLACE:
        original = state.queue.pop(0)
        replacement = decision.replacement_action
        assert replacement is not None  # schema-enforced
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            action_id=replacement.action_id,
            detail=f"REPLACE — {original.action_id} → {replacement.action_id}",
            payload={"original_action_id": original.action_id},
        )
        state.drift_used += 1
        next_to_run = replacement
    elif decision.decision_type == LoopDecisionType.INSERT:
        replacement = decision.replacement_action
        assert replacement is not None
        # Insert BEFORE the next planned action; do not pop yet.
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            action_id=replacement.action_id,
            detail=f"INSERT — {replacement.action_id} before {state.queue[0].action_id}",
        )
        state.drift_used += 1
        next_to_run = replacement
        # The originally-next planned action stays in the queue and
        # will be the next iteration's target.
    else:  # CONTINUE
        next_to_run = state.queue.pop(0)
        executor._emit_trace(
            TraceEventType.LOOP_DECISION_APPLIED,
            action_id=next_to_run.action_id,
            detail=f"CONTINUE — running planned {next_to_run.action_id}",
        )

    # Dispatch via the policy-checked path. Failure does NOT abort the
    # loop by default — the LLM might decide to recover on the next
    # turn — but it does flip ``all_ok`` so verify sees the truth.
    record = _dispatch_one(executor, next_to_run, run_id, state.manifest, plan)
    state.records.append(record)
    if record.status == ExecutionStatus.FAILED:
        state.all_ok = False

    # Stash observation for the next loop turn's LLM consultation.
    state.last_action_id = next_to_run.action_id
    state.last_status = "ok" if record.status == ExecutionStatus.SUCCESS else "fail"
    state.last_observation = _extract_observation(executor, next_to_run.action_id)


def _extract_observation(executor: "Executor", action_id: str) -> dict[str, Any] | None:
    """Find the most recent ACTION_END row in trace.jsonl for
    ``action_id`` and lift its ``observation`` dict. Returns None
    when no trace file exists or no matching row is present (e.g.
    the executor was constructed without a TraceLogger)."""
    trace_path = executor.run_store.trace_path
    if not trace_path.exists():
        return None
    try:
        for line in reversed(trace_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "action.end":
                continue
            payload = row.get("payload") or {}
            if payload.get("action_id") != action_id:
                continue
            return payload.get("observation")
    except OSError:
        return None
    return None
