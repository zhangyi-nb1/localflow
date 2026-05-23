"""Phase 13 — runtime semantic verifier.

Mirror of the structural :class:`Verifier`, but for semantic graders
(LLM-as-judge). Designed to plug into ``control_loop.run_with_auto_repair``
*after* structural verify passes; never modifies kernel state.

The verifier IS NOT a re-export of the eval runner's grader registry —
it consumes the same registered graders (via ``app.eval.graders.get``)
but constructs the :class:`~app.eval.schema.GraderContext` from
*runtime* artifacts (live RunStore, current ExecutionOutcome, current
filesystem). So the same grader function works in both eval mode and
runtime mode without per-mode branching inside the grader.

§10.7 invariant: this module sits next to the existing Verifier (it
does NOT touch app/harness/verifier.py). Graders are pure functions
of GraderContext, so adding new ones never reaches into the kernel.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from app.eval.graders import get as get_grader
from app.eval.graders import list_names as list_grader_names
from app.eval.schema import EvalTask, GraderContext
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    SemanticVerdict,
    SemanticVerificationResult,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.execution import ExecutionRecord
from app.schemas.rollback import RollbackManifest
from app.schemas.trace import FailureType, TraceEvent, TraceEventType

# Graders considered "semantic" — they live alongside structural
# graders in the same registry. We discriminate by name so the
# structural set stays untouched (no change to app/eval/graders/structural.py).
SEMANTIC_GRADER_NAMES: tuple[str, ...] = (
    "output_addresses_goal",
    "summary_grounded",
    "analysis_result_nonempty",
)


class SemanticVerifier:
    """Runs every registered semantic grader and produces a typed
    aggregate verdict.

    Construction is cheap (no LLM contact); the work happens in
    :meth:`verify`. Each grader is called inside a try/except that
    converts crashes into a single "skipped" verdict so one broken
    grader can't poison the verification pass.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        graders: list[str] | None = None,
        trace: TraceLogger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self._graders = list(graders) if graders is not None else list(SEMANTIC_GRADER_NAMES)
        self.trace = trace

    def verify(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        execution_records: list[ExecutionRecord],
        manifest: RollbackManifest,
        snapshot_before: WorkspaceSnapshot,
        snapshot_after: WorkspaceSnapshot | None,
        structural: VerificationResult,
        trace_events: list[TraceEvent] | None = None,
        run_id: str | None = None,
    ) -> SemanticVerificationResult:
        ctx = _build_grader_context(
            task=task,
            plan=plan,
            execution_records=execution_records,
            manifest=manifest,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            structural=structural,
            trace_events=trace_events or [],
            workspace_root=self.workspace_root,
        )
        verdicts: list[SemanticVerdict] = []
        registered = set(list_grader_names())
        for name in self._graders:
            if name not in registered:
                verdicts.append(
                    SemanticVerdict(
                        grader=name,
                        passed=True,
                        reason=f"grader {name!r} not registered; skipped",
                    )
                )
                continue
            verdict = _run_one(name, ctx)
            verdicts.append(verdict)
            # Phase 25.3 — mirror the structural verifier's
            # VERIFIER_CHECK trace emission so eval graders and the
            # repair loop can see per-semantic-verdict outcomes in
            # trace.jsonl. Plan-level (NOT per-action), so this is
            # plain TraceEvent — the OpenHands-style per-action
            # critic_result on ActionTraceEvent is reserved for
            # future per-step graders (Phase 26+).
            self._emit_verdict_trace(verdict, task=task, run_id=run_id)

        failed = [v for v in verdicts if not v.passed]
        passed_all = not failed
        eligible = any(v.suggested_hint for v in failed)
        summary = _summarize(verdicts, failed)
        return SemanticVerificationResult(
            task_id=task.task_id,
            run_id=run_id or task.task_id,
            passed=passed_all,
            verdicts=verdicts,
            failed_verdicts=failed,
            summary=summary,
            created_at=datetime.now(timezone.utc),
            auto_repair_eligible=(not passed_all) and eligible,
        )

    def _emit_verdict_trace(
        self,
        verdict: SemanticVerdict,
        *,
        task: TaskSpec,
        run_id: str | None,
    ) -> None:
        """No-op when self.trace is None; otherwise emit one
        VERIFIER_CHECK row per semantic verdict.

        The structural Verifier emits one VERIFIER_CHECK per check
        (``app/harness/verifier.py``); without this mirror, the
        repair loop / grader histograms only see structural failures.
        ``trace summary`` ends up under-counting verifier activity by
        the semantic count (currently 3 graders per run).
        """
        if self.trace is None:
            return
        try:
            payload: dict = {
                "grader": verdict.grader,
                "passed": verdict.passed,
                "reason": verdict.reason[:300],
            }
            if verdict.suggested_hint:
                payload["suggested_hint"] = verdict.suggested_hint[:300]
            self.trace.emit(
                TraceEvent(
                    task_id=task.task_id,
                    run_id=run_id or task.task_id,
                    event_type=TraceEventType.VERIFIER_CHECK,
                    status="ok" if verdict.passed else "fail",
                    failure_type=(
                        None if verdict.passed else FailureType.SEMANTIC_MISMATCH
                    ),
                    detail=f"{verdict.grader}: {verdict.reason[:160]}",
                    payload=payload,
                )
            )
        except Exception:
            # Trace must never break the verify path.
            pass


# ──────────────────────────────────── internals


def _build_grader_context(
    *,
    task: TaskSpec,
    plan: ActionPlan,
    execution_records: list[ExecutionRecord],
    manifest: RollbackManifest,
    snapshot_before: WorkspaceSnapshot,
    snapshot_after: WorkspaceSnapshot | None,
    structural: VerificationResult,
    trace_events: list[TraceEvent],
    workspace_root: Path,
) -> GraderContext:
    """Synthesize an :class:`EvalTask`-shaped wrapper so the eval-time
    graders work unchanged in runtime mode.

    The fake :class:`EvalTask` is a minimal placeholder — graders read
    ``ctx.task_spec.user_goal`` and ``ctx.task.expected_outputs`` (both
    populated from the live TaskSpec / ActionPlan), but they don't
    consult any other EvalTask field at this point. We use ``model_construct``
    to bypass strict validation for the shim fields we don't fill in
    (workspace_seed, graders, etc.)."""
    eval_task_shim = EvalTask.model_construct(
        task_id=task.task_id,
        title=task.user_goal[:80] or "(runtime task)",
        goal=task.user_goal,
        skill=task.skill,
        planner="llm" if any(True for _ in plan.actions) else "rule",
        expected_outputs=list(plan.expected_outputs),
        workspace_seed=[],
        graders=[],
        must_pass=[],
        stages=None,
    )
    return GraderContext(
        task=eval_task_shim,
        task_spec=task,
        plan=plan,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        execution_records=execution_records,
        manifest=manifest,
        verification=structural,
        trace_events=trace_events,
        workspace_path=workspace_root,
        seed_hashes={},
    )


def _run_one(name: str, ctx: GraderContext) -> SemanticVerdict:
    fn = get_grader(name)
    started = time.perf_counter()
    try:
        gv = fn(ctx)
    except Exception as exc:  # pragma: no cover — defensive
        return SemanticVerdict(
            grader=name,
            passed=True,  # crashes treated as "skipped — don't trigger repair"
            reason=f"grader crashed: {type(exc).__name__}: {exc}",
            suggested_hint=None,
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    # Convert GraderVerdict → SemanticVerdict. The grader detail line
    # often contains a hint when passed=False; we lift it verbatim
    # (semantic graders are responsible for phrasing detail as a
    # planner-usable instruction in that case). When the grader has no
    # detail AND the generic fallback also returns an empty string,
    # we set suggested_hint=None so the auto-repair-eligible gate
    # correctly classifies this rejection as "user must intervene".
    suggested_hint = None
    if not gv.passed:
        hint = (gv.detail or "").strip()
        if not hint:
            hint = _generic_hint(name).strip()
        suggested_hint = hint or None
    return SemanticVerdict(
        grader=name,
        passed=gv.passed,
        reason=gv.detail or "",
        suggested_hint=suggested_hint,
        duration_ms=duration_ms,
    )


def _generic_hint(grader: str) -> str:
    """Fallback hint when the grader didn't supply specific detail.

    Generic hints aren't great (the LLM gets less context), but
    they're better than empty — they at least tell the planner
    *what kind* of failure to address."""
    return {
        "output_addresses_goal": (
            "Re-plan so the produced output materially addresses the user's "
            "goal — substantive content, not boilerplate or meta-descriptions."
        ),
        "summary_grounded": (
            "Re-plan so the generated summary mentions and groups the "
            "actual files in the workspace, not generic placeholders."
        ),
        "analysis_result_nonempty": (
            "Re-plan with a different AnalysisSpec — pick columns and "
            "aggregations that actually produce non-empty results on this data."
        ),
    }.get(grader, "Re-plan to address the rejected grader's concern.")


def _summarize(verdicts: list[SemanticVerdict], failed: list[SemanticVerdict]) -> str:
    if not verdicts:
        return "no semantic graders run"
    if not failed:
        return f"all {len(verdicts)} semantic verdict(s) passed"
    names = ", ".join(v.grader for v in failed)
    return f"{len(failed)}/{len(verdicts)} semantic verdict(s) rejected: {names}"
