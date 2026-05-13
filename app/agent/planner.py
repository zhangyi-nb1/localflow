from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.agent.client import AnthropicClient, LLMClient, LLMClientError, StructuredResponse
from app.agent.prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_action_plan_tool_schema,
    render_repair_prompt,
    render_user_prompt,
)
from app.harness.action_validator import PlanValidationError, validate_plan_structure
from app.harness.policy_guard import assess_plan
from app.schemas import ActionPlan, RiskAssessment, TaskSpec, WorkspaceSnapshot

DEFAULT_MAX_ATTEMPTS = 3


class PlannerFailure(RuntimeError):
    """Raised when the LLM planner can't produce a valid plan in the
    configured number of attempts. Carries the attempt history so the
    caller can audit what went wrong.
    """

    def __init__(self, message: str, attempts: list["AttemptLog"]) -> None:
        super().__init__(message)
        self.attempts = attempts


class AttemptLog:
    """One iteration of the repair loop — what came back and why we rejected it (if we did)."""

    __slots__ = ("payload", "errors", "usage", "outcome")

    def __init__(
        self,
        *,
        payload: dict[str, Any],
        errors: list[str],
        usage: dict[str, int],
        outcome: str,
    ) -> None:
        self.payload = payload
        self.errors = errors
        self.usage = usage
        self.outcome = outcome  # "accepted" | "schema_invalid" | "policy_blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "errors": list(self.errors),
            "usage": dict(self.usage),
        }


# --------------------------------------------------------------------- public API


def plan_with_llm(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    client: LLMClient | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_delta: Callable[[str], None] | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> ActionPlan:
    """Drop-in replacement for ``plan_organization`` that uses an LLM.

    Same signature, same return type. The Harness Kernel does not need to
    know which planner produced the plan — passing the kernel-invariance
    test from outline §10.7.

    When ``client`` is None, the default provider is chosen by the
    ``LOCALFLOW_LLM_PROVIDER`` env var (``openai`` by default; set to
    ``anthropic`` to flip).

    ``on_delta`` and ``on_attempt`` enable progressive UI: ``on_delta``
    fires with each streamed chunk of the tool_call arguments JSON;
    ``on_attempt`` fires at the start of each (possibly repair) attempt.
    """
    if client is None:
        client = _default_client()
    planner = LLMPlanner(client=client, max_attempts=max_attempts)
    return planner.plan(task, snapshot, on_delta=on_delta, on_attempt=on_attempt)


def _default_client() -> LLMClient:
    """Resolve the default LLM provider from environment.

    Order: ``LOCALFLOW_LLM_PROVIDER`` env var → ``openai`` fallback.
    Imported lazily so a missing OpenAI SDK doesn't break Anthropic users
    (and vice versa).
    """
    provider = os.environ.get("LOCALFLOW_LLM_PROVIDER", "openai").lower()
    if provider == "openai":
        from app.agent.openai_client import OpenAIClient

        return OpenAIClient()
    if provider == "anthropic":
        return AnthropicClient()
    raise LLMClientError(
        f"unknown LOCALFLOW_LLM_PROVIDER={provider!r}; expected 'openai' or 'anthropic'"
    )


# --------------------------------------------------------------------- LLMPlanner


class LLMPlanner:
    """Wraps an LLMClient with the repair loop.

    The repair loop appends a tool_result with ``is_error=True`` whenever
    the model's submission fails Pydantic validation, the action-validator,
    or the policy guard. The model sees its own prior tool_use and the
    error text — it has everything it needs to fix the plan without us
    re-sending the workspace snapshot.
    """

    def __init__(self, *, client: LLMClient, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        self.client = client
        self.max_attempts = max_attempts
        self.last_attempts: list[AttemptLog] = []

    def plan(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        *,
        on_delta: Callable[[str], None] | None = None,
        on_attempt: Callable[[int], None] | None = None,
    ) -> ActionPlan:
        tool_schema = build_action_plan_tool_schema()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": render_user_prompt(task, snapshot)}
        ]
        self.last_attempts = []

        for attempt in range(1, self.max_attempts + 1):
            if on_attempt is not None:
                on_attempt(attempt)
            try:
                response = self.client.generate_structured(
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tool_name=TOOL_NAME,
                    tool_description=TOOL_DESCRIPTION,
                    tool_schema=tool_schema,
                    on_delta=on_delta,
                )
            except LLMClientError as exc:
                raise PlannerFailure(
                    f"LLM call failed on attempt {attempt}: {exc}", self.last_attempts
                ) from exc

            plan_or_errors = self._validate(task, response.payload)
            if isinstance(plan_or_errors, ActionPlan):
                self.last_attempts.append(
                    AttemptLog(
                        payload=response.payload,
                        errors=[],
                        usage=response.usage,
                        outcome="accepted",
                    )
                )
                return plan_or_errors

            errors, outcome = plan_or_errors
            self.last_attempts.append(
                AttemptLog(
                    payload=response.payload,
                    errors=errors,
                    usage=response.usage,
                    outcome=outcome,
                )
            )
            if attempt == self.max_attempts:
                break

            # Repair: append the model's response + a tool_result with the
            # error so the next call sees its own prior attempt and the
            # specific harness objections.
            messages = self._append_repair_turn(messages, response, errors)

        joined = "\n\n".join(
            f"-- attempt {i} ({log.outcome}) --\n" + "\n".join(log.errors)
            for i, log in enumerate(self.last_attempts, start=1)
        )
        raise PlannerFailure(
            f"LLM planner failed after {self.max_attempts} attempt(s):\n{joined}",
            self.last_attempts,
        )

    # -- internals -----------------------------------------------------

    def _validate(
        self, task: TaskSpec, payload: dict[str, Any]
    ) -> ActionPlan | tuple[list[str], str]:
        # 1. Pydantic schema validation.
        try:
            plan = ActionPlan.model_validate(payload)
        except ValidationError as exc:
            return ([_format_pydantic_error(e) for e in exc.errors()], "schema_invalid")

        # 2. Plan-shape validator (duplicate IDs, mkdir-with-source, etc.).
        try:
            validate_plan_structure(plan)
        except PlanValidationError as exc:
            return ([str(exc)], "schema_invalid")

        # 3. Pin task_id — the model can hallucinate; the harness requires
        # exact match with the run's task.
        if plan.task_id != task.task_id:
            return (
                [f"plan.task_id must be exactly '{task.task_id}' (you returned '{plan.task_id}')."],
                "schema_invalid",
            )

        # 4. Policy Guard — the real defense-in-depth check. If this
        # rejects the plan, the model has tried to do something it's not
        # allowed to do.
        assessment = assess_plan(
            _path_for_guard(task.workspace_root),
            plan,
            forbidden_actions=tuple(task.forbidden_actions),
        )
        if not assessment.passed:
            return (_format_policy_errors(assessment), "policy_blocked")

        # 5. Plan must have a fresh ID — accept what the model returned but
        # normalize to a UUID-prefixed shape if it's empty.
        if not plan.plan_id:
            plan = plan.model_copy(update={"plan_id": f"plan-{uuid.uuid4().hex[:8]}"})

        return plan

    def _append_repair_turn(
        self,
        messages: list[dict[str, Any]],
        response: StructuredResponse,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        new_messages = list(messages)
        # Echo the assistant turn so the model can see its own prior plan.
        new_messages.append({"role": "assistant", "content": response.raw_assistant_content})
        # Then deliver the error as a tool_result.
        new_messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": response.tool_use_id,
                        "is_error": True,
                        "content": render_repair_prompt("\n".join(f"- {e}" for e in errors)),
                    }
                ],
            }
        )
        return new_messages


# --------------------------------------------------------------------- helpers


def _format_pydantic_error(err: dict[str, Any]) -> str:
    loc = ".".join(str(p) for p in err.get("loc", []))
    msg = err.get("msg", "")
    typ = err.get("type", "")
    return f"{loc or '<root>'}: {msg} ({typ})"


def _format_policy_errors(assessment: RiskAssessment) -> list[str]:
    out = list(assessment.warnings)
    if assessment.blocked_actions:
        out.append(
            f"Policy Guard blocked these action_ids: {', '.join(assessment.blocked_actions)}"
        )
    if not out:
        out.append(f"Policy Guard rejected the plan: {assessment.reason}")
    return out


def _path_for_guard(workspace_root: str):
    from pathlib import Path

    return Path(workspace_root)
