from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.agent.client import AnthropicClient, LLMClient, LLMClientError, StructuredResponse
from app.agent.locale_prompts import locale_instruction
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
from app.harness.trace import TraceLogger
from app.schemas import (
    Action,
    ActionPlan,
    FailureType,
    RiskAssessment,
    TaskSpec,
    TraceEvent,
    TraceEventType,
    WorkspaceSnapshot,
)

DEFAULT_MAX_ATTEMPTS = 3
ExtraPlanValidator = Callable[[TaskSpec, WorkspaceSnapshot, ActionPlan], list[str]]


class PlannerFailure(RuntimeError):
    """Raised when the LLM planner can't produce a valid plan in the
    configured number of attempts. Carries the attempt history so the
    caller can audit what went wrong.
    ``extra_validator`` lets a skill reject safe-but-incomplete plans
    with actionable errors that feed the same repair loop. The default
    is ``None`` so existing skills keep their exact behavior.
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
    system_prompt: str | None = None,
    trace: TraceLogger | None = None,
    prior_plan_actions: list[Action] | None = None,
    user_hint: str | None = None,
    extra_validator: ExtraPlanValidator | None = None,
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

    ``system_prompt`` overrides the folder-organizer-flavored
    :data:`SYSTEM_PROMPT` — the v0.9.0 ``agent`` meta-skill passes its
    own prompt teaching the model to emit chart actions in addition to
    moves/index. Pass ``None`` to keep the legacy folder-organizer
    behaviour (existing callers unchanged).

    Phase 11 (refinement loop): when ``prior_plan_actions`` AND
    ``user_hint`` are both set, the planner prepends a synthetic
    "your previous plan was X; user said Y; please re-plan" user
    message so the LLM rewrites its plan with the clarification in
    context. This reuses the same single-LLM-call codepath as a fresh
    plan — no new state machine.

    ``extra_validator`` lets a skill reject safe-but-incomplete plans
    with actionable errors that feed the same repair loop. The default
    is ``None`` so existing skills keep their exact behavior.
    """
    if client is None:
        client = _default_client()
    planner = LLMPlanner(
        client=client,
        max_attempts=max_attempts,
        system_prompt=system_prompt,
        trace=trace,
        extra_validator=extra_validator,
    )
    return planner.plan(
        task,
        snapshot,
        on_delta=on_delta,
        on_attempt=on_attempt,
        prior_plan_actions=prior_plan_actions,
        user_hint=user_hint,
    )


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

    def __init__(
        self,
        *,
        client: LLMClient,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        system_prompt: str | None = None,
        trace: TraceLogger | None = None,
        extra_validator: ExtraPlanValidator | None = None,
    ) -> None:
        self.client = client
        self.max_attempts = max_attempts
        # Default: folder-organizer-flavored SYSTEM_PROMPT (back-compat).
        # The agent skill overrides this with its own multi-capability prompt.
        self.system_prompt = system_prompt if system_prompt is not None else SYSTEM_PROMPT
        # Phase 9 — optional trace stream.
        self.trace = trace
        self.extra_validator = extra_validator
        self.last_attempts: list[AttemptLog] = []

    def plan(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        *,
        on_delta: Callable[[str], None] | None = None,
        on_attempt: Callable[[int], None] | None = None,
        prior_plan_actions: list[Action] | None = None,
        user_hint: str | None = None,
    ) -> ActionPlan:
        # v0.16 — scope the action_type enum in the tool schema to just
        # the actions declared allowed for THIS task. The LLM literally
        # cannot propose a `delete` action when task.allowed_actions
        # excludes it, even if its training data suggests otherwise.
        allowed = list(task.allowed_actions) if task.allowed_actions else None
        tool_schema = build_action_plan_tool_schema(allowed_action_types=allowed)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": render_user_prompt(task, snapshot)}
        ]
        # Phase 11: a refinement turn appends one more user message that
        # echoes the prior plan + the user's clarification. The first
        # iteration of the repair loop then runs naturally — the LLM
        # sees its prior attempt, the user's correction, and produces
        # a fresh submit_action_plan tool_use.
        if prior_plan_actions is not None and user_hint:
            messages.append(
                {
                    "role": "user",
                    "content": _build_refinement_message(prior_plan_actions, user_hint),
                }
            )
            self._emit_repair(task, attempt=0, errors=[f"user_hint: {user_hint[:200]}"])
        self.last_attempts = []

        # v0.22 — splice the locale-discipline paragraph onto the system
        # prompt at call time so the LLM honours task.locale for every
        # piece of user-facing prose it generates (summaries, reasons,
        # index bodies). Doing it here (rather than in __init__) means a
        # single planner instance can serve tasks in different locales.
        system_for_call = self.system_prompt + "\n\n" + locale_instruction(task.locale)

        for attempt in range(1, self.max_attempts + 1):
            if on_attempt is not None:
                on_attempt(attempt)
            call_start = time.perf_counter()
            self._emit_call_start(task, attempt)
            try:
                response = self.client.generate_structured(
                    system=system_for_call,
                    messages=messages,
                    tool_name=TOOL_NAME,
                    tool_description=TOOL_DESCRIPTION,
                    tool_schema=tool_schema,
                    on_delta=on_delta,
                )
            except LLMClientError as exc:
                self._emit_call_end(
                    task,
                    attempt,
                    call_start,
                    usage={},
                    status="fail",
                    failure_type=FailureType.UNKNOWN,
                    detail=f"client error: {exc}",
                )
                raise PlannerFailure(
                    f"LLM call failed on attempt {attempt}: {exc}", self.last_attempts
                ) from exc

            plan_or_errors = self._validate(task, snapshot, response.payload)
            if isinstance(plan_or_errors, ActionPlan):
                self._emit_call_end(
                    task,
                    attempt,
                    call_start,
                    usage=response.usage,
                    status="ok",
                    detail=f"plan accepted ({len(plan_or_errors.actions)} actions)",
                )
                self.last_attempts.append(
                    AttemptLog(
                        payload=response.payload,
                        errors=[],
                        usage=response.usage,
                        outcome="accepted",
                    )
                )
                # Phase 25.1 — fold LLM provenance into the plan so the
                # executor can emit ActionTraceEvent without any new
                # call-site argument. ``raw_assistant_content`` may be
                # empty for stub / fake clients used in tests; helper
                # tolerates that and leaves fields at None.
                _attach_llm_provenance(plan_or_errors, response)
                return plan_or_errors

            errors, outcome = plan_or_errors
            self._emit_call_end(
                task,
                attempt,
                call_start,
                usage=response.usage,
                status="fail",
                failure_type=_outcome_to_failure_type(outcome),
                detail=f"{outcome}: {'; '.join(errors)[:200]}",
            )
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
            self._emit_repair(task, attempt, errors)
            messages = self._append_repair_turn(messages, response, errors)

        # v0.16.1 — exhaustion path. Before raising, try to salvage a
        # partial ActionPlan from the latest attempt: keep actions that
        # individually pass policy_guard + structural checks, drop the
        # ones that don't, and pack a diagnostic into the summary so
        # the user can decide whether to execute the degraded version.
        # The user's UI / CLI surfaces this via the regular dry-run
        # ceremony — they're never executing something they didn't see.
        partial = self._synthesize_partial_plan(task)
        if partial is not None:
            return partial

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
        self, task: TaskSpec, snapshot: WorkspaceSnapshot, payload: dict[str, Any]
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

        # 5. Optional skill-specific completeness checks. The generic
        # validators can prove a plan is safe and well-formed; a skill
        # can also reject plans that are incomplete for its own promise.
        if self.extra_validator is not None:
            try:
                extra_errors = self.extra_validator(task, snapshot, plan)
            except Exception as exc:
                extra_errors = [f"extra_validator crashed: {type(exc).__name__}: {exc}"]
            if extra_errors:
                return (extra_errors, "schema_invalid")

        # 6. Plan must have a fresh ID; accept what the model returned but
        # normalize to a UUID-prefixed shape if it's empty.
        if not plan.plan_id:
            plan = plan.model_copy(update={"plan_id": f"plan-{uuid.uuid4().hex[:8]}"})

        return plan

    def _synthesize_partial_plan(self, task: TaskSpec) -> ActionPlan | None:
        """v0.16.1 — try to recover a degraded but usable ActionPlan
        from the last LLM attempt instead of raising PlannerFailure.

        Algorithm:
          1. Pull the last attempt's raw payload.
          2. Try to coerce it into ActionPlan via Pydantic. If even
             Pydantic refuses the whole thing, give up and return None.
          3. Walk actions one-by-one through policy_guard. Keep the
             ones that pass; drop the rest with the reason logged.
          4. If we keep at least 1 action, return a degraded plan with
             a diagnostic in summary + risk_summary. Otherwise return
             None and let the caller raise.

        The returned plan keeps the user in control — the harness
        still runs dry-run + approval before any execute. The user
        sees the diagnostic and decides.
        """
        if not self.last_attempts:
            return None
        last = self.last_attempts[-1]
        if not isinstance(last.payload, dict):
            return None

        try:
            candidate = ActionPlan.model_validate(last.payload)
        except ValidationError:
            # Couldn't even Pydantic-parse the payload. Try to keep
            # whatever individual actions did parse + scaffold a fresh
            # plan around them.
            actions = _salvage_actions(last.payload)
            if not actions:
                return None
            candidate = ActionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                task_id=task.task_id,
                summary="(partial) LLM payload failed schema; salvaged individual actions.",
                actions=actions,
                expected_outputs=[],
                risk_summary="degraded",
            )

        kept: list[Action] = []
        dropped: list[tuple[str, str]] = []  # (action_id, reason)
        for action in candidate.actions:
            try:
                from app.harness.action_validator import validate_plan_structure
                from app.harness.policy_guard import evaluate_action

                # Spot-check: run the action through policy_guard alone.
                # Skip dup-id checks here — those are validate_plan_structure's
                # job, and a partial plan can't have dup IDs by construction.
                pd_path = _path_for_guard(task.workspace_root)
                decision = evaluate_action(
                    pd_path,
                    action,
                    forbidden_actions=tuple(task.forbidden_actions),
                    forbidden_paths=tuple(task.forbidden_paths),
                )
                if not decision.allowed:
                    dropped.append((action.action_id, "; ".join(decision.reasons)))
                    continue
                # Also smoke-test plan-structure on a single-action plan.
                trial = candidate.model_copy(update={"actions": [action]})
                validate_plan_structure(trial)
            except Exception as exc:
                dropped.append((action.action_id, f"{type(exc).__name__}: {exc}"))
                continue
            kept.append(action)

        if not kept:
            return None

        diagnostic = self._diagnose()
        salvage_note = (
            f"⚠️ PARTIAL PLAN (v0.16.1 fallback): kept {len(kept)} of "
            f"{len(candidate.actions)} action(s) after {self.max_attempts} "
            f"failed full-plan attempts. {diagnostic}"
        )
        if dropped:
            sample = "; ".join(f"{aid}: {why[:60]}" for aid, why in dropped[:3])
            salvage_note += f" Dropped: {sample}"

        return ActionPlan(
            plan_id=candidate.plan_id or f"plan-{uuid.uuid4().hex[:8]}",
            task_id=task.task_id,
            summary=salvage_note,
            actions=kept,
            expected_outputs=[],
            risk_summary=(
                "degraded — review the plan carefully before executing. "
                f"The LLM couldn't satisfy your full goal in {self.max_attempts} attempts; "
                "this is the largest subset of its last proposal that passed policy_guard. "
                'Consider re-running with `localflow revise --hint "..."` to address '
                "the dropped actions."
            ),
        )

    def _diagnose(self) -> str:
        """Build a short human-readable explanation of why the LLM
        couldn't produce a fully-valid plan. Counts how many attempts
        ended in each outcome and surfaces the dominant failure mode."""
        if not self.last_attempts:
            return "no diagnostic available."
        counts: dict[str, int] = {}
        for log in self.last_attempts:
            counts[log.outcome] = counts.get(log.outcome, 0) + 1
        if not counts:
            return "no diagnostic available."
        # Sort by count descending, take the top reason
        sorted_outcomes = sorted(counts.items(), key=lambda kv: -kv[1])
        top, top_n = sorted_outcomes[0]
        if top == "schema_invalid":
            return (
                f"{top_n}/{len(self.last_attempts)} attempts produced a plan that violated the "
                "submit_action_plan tool schema (often: missing fields, wrong types, "
                "or an action_type not in the allowed set). Consider rephrasing the "
                "goal in more concrete terms, or use a more specialized skill."
            )
        if top == "policy_blocked":
            return (
                f"{top_n}/{len(self.last_attempts)} attempts proposed actions that policy_guard "
                "rejected (often: forbidden_paths intersection, delete action, "
                "or paths escaping the workspace). Consider widening allowed_actions "
                "or relaxing forbidden_paths for this task."
            )
        return f"top failure mode: {top} ({top_n}/{len(self.last_attempts)} attempts)."

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

    # -- Phase 9 trace emission helpers (no-op when self.trace is None)

    def _emit_call_start(self, task: TaskSpec, attempt: int) -> None:
        if self.trace is None:
            return
        try:
            self.trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    event_type=TraceEventType.LLM_CALL_START,
                    detail=f"attempt {attempt}",
                    payload={"attempt": attempt},
                )
            )
        except Exception:
            pass

    def _emit_call_end(
        self,
        task: TaskSpec,
        attempt: int,
        call_start: float,
        *,
        usage: dict,
        status: str,
        failure_type: FailureType | None = None,
        detail: str = "",
    ) -> None:
        if self.trace is None:
            return
        duration_ms = int((time.perf_counter() - call_start) * 1000)
        try:
            self.trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    event_type=TraceEventType.LLM_CALL_END,
                    status=status,  # type: ignore[arg-type]
                    failure_type=failure_type,
                    duration_ms=duration_ms,
                    token_usage=usage,
                    detail=detail[:300],
                    payload={"attempt": attempt},
                )
            )
        except Exception:
            pass

    def _emit_repair(self, task: TaskSpec, attempt: int, errors: list[str]) -> None:
        if self.trace is None:
            return
        try:
            self.trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    event_type=TraceEventType.LLM_REPAIR,
                    status="fail",
                    detail=("; ".join(errors))[:300],
                    payload={"attempt": attempt, "errors": errors},
                )
            )
        except Exception:
            pass


# --------------------------------------------------------------------- helpers


def _outcome_to_failure_type(outcome: str) -> FailureType:
    """Map an attempt's outcome string to the canonical FailureType."""
    if outcome == "schema_invalid":
        return FailureType.SCHEMA_INVALID
    if outcome == "policy_blocked":
        return FailureType.POLICY_BLOCKED
    return FailureType.UNKNOWN


# --------------------------------------------------------------------- Phase 25.1
def _attach_llm_provenance(plan: ActionPlan, response: "StructuredResponse") -> None:
    """Phase 25.1 — fold the LLM's thought / reasoning / raw tool_use
    into the ActionPlan so the executor can emit ActionTraceEvent
    without any new call-site argument.

    Pulls from ``response.raw_assistant_content`` which carries the
    full ``response.content`` list of blocks (thinking + tool_use)
    that the Anthropic / OpenAI client returns. Tolerant of:

      * missing ``raw_assistant_content`` (stub clients used in tests)
      * mixed-shape blocks (different SDK versions / providers)
      * tool_use blocks with no thinking siblings

    Always sets ``llm_tool_call_raw`` when a tool_use block exists in
    the response (this is the audit-trail anchor — we want to be able
    to grep ``llm_tool_call_raw is not null`` to find every LLM-driven
    plan in the run store). Sets ``llm_thought`` only when at least
    one thinking block has non-empty text; sets ``llm_reasoning``
    only when raw thinking blocks are present.
    """
    blocks: list[dict[str, Any]] = list(response.raw_assistant_content or [])
    if not blocks:
        return

    thinking_blocks: list[dict[str, Any]] = []
    thoughts: list[str] = []
    tool_call_raw: dict[str, Any] | None = None

    for block in blocks:
        btype = block.get("type") if isinstance(block, dict) else None
        if btype == "thinking":
            thinking_blocks.append(block)
            text = block.get("thinking") or block.get("text") or ""
            if isinstance(text, str) and text.strip():
                thoughts.append(text.strip())
        elif btype == "tool_use":
            # Capture the FIRST tool_use block. The LLM contract is
            # one forced tool call per turn, so there shouldn't be
            # more than one anyway — but if there is, the harness
            # already validated against the named one and we record
            # what it actually saw.
            if tool_call_raw is None:
                tool_call_raw = {
                    k: block.get(k)
                    for k in ("id", "name", "input")
                    if k in block
                }

    if thinking_blocks:
        plan.llm_reasoning = thinking_blocks
    if thoughts:
        plan.llm_thought = "\n\n".join(thoughts)
    if tool_call_raw is not None:
        plan.llm_tool_call_raw = tool_call_raw


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


def _salvage_actions(payload: dict[str, Any]) -> list[Action]:
    """v0.16.1 — best-effort: walk an unvalidated LLM payload's
    ``actions`` list and keep entries that Pydantic can individually
    accept. Used by the partial-plan fallback when the whole plan
    failed schema validation but some actions inside it didn't."""
    raw_actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(raw_actions, list):
        return []
    out: list[Action] = []
    for entry in raw_actions:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(Action.model_validate(entry))
        except ValidationError:
            continue
    return out


def _build_refinement_message(prior_plan_actions: list[Action], user_hint: str) -> str:
    """Phase 11: synthesize the user-turn body for a refinement call.

    The LLM sees a compact JSON dump of every action from the prior plan
    plus the user's clarification. The framing tells it explicitly that
    the previous attempt missed the user's intent so it doesn't just
    tweak — it re-plans from scratch with the hint in mind.
    """
    try:
        prior_json = json.dumps(
            [a.model_dump(mode="json") for a in prior_plan_actions],
            ensure_ascii=False,
            indent=2,
        )
    except Exception:
        prior_json = "(prior plan could not be serialised — re-plan from scratch)"
    return (
        "REVISION REQUEST — your previous plan did NOT match the user's intent.\n\n"
        "Your previous plan emitted the following actions:\n\n"
        f"```json\n{prior_json}\n```\n\n"
        "The user reviewed it and provided this clarification:\n\n"
        f"> {user_hint.strip()}\n\n"
        "Please regenerate a fresh ActionPlan from scratch that addresses "
        "the user's clarification. Do not simply tweak the prior plan — "
        "consider whether your prior decomposition itself was wrong (e.g. "
        "you tried to organize files when the user wanted to analyze data "
        "inside one of them). Use the same submit_action_plan tool to "
        "submit the revised plan."
    )
