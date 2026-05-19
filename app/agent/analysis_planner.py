"""Phase 3.3b — LLM-driven AnalysisSpec planner for data_analyzer.

Mirrors the architecture of ``app.agent.planner.LLMPlanner``:
  1. Build system prompt + workspace context.
  2. Hit ``LLMClient.generate_structured`` with a strict tool call.
  3. Validate the returned dict against ``AnalysisSpec`` (Pydantic).
  4. On failure, append the validation error as a tool_result with
     ``is_error=True`` and retry up to ``max_attempts``.
  5. On success, load the source file via ``data_ops`` and run the
     ``execute_analysis`` engine, then hand the result list to
     ``build_plan_from_results`` so the rendered ActionPlan is the
     same shape as the rule path.

The LLM never writes Python; it emits typed schema only.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent.analysis_prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_analysis_spec_tool_schema,
    render_repair_prompt,
    render_user_prompt,
)
from app.agent.client import LLMClient, LLMClientError, StructuredResponse
from app.agent.planner import _build_refinement_message, _default_client
from app.schemas import Action, ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.analysis import (
    AggregationOp,
    AnalysisOutcome,
    AnalysisResult,
    AnalysisSpec,
)
from app.skills.data_analyzer.planner import build_plan_from_results

DEFAULT_MAX_ATTEMPTS = 3


class AnalysisPlannerFailure(RuntimeError):
    """Raised when the LLM can't produce a valid AnalysisSpec after
    ``max_attempts``. Mirrors ``app.agent.planner.PlannerFailure``."""


def plan_analysis_with_llm(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    client: LLMClient | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_delta=None,
    on_attempt=None,
    prior_plan_actions: list[Action] | None = None,
    user_hint: str | None = None,
    **_extra: Any,
) -> ActionPlan:
    """Build an ActionPlan whose specs come from the LLM, not heuristics.

    Same return shape as ``plan_data_analysis`` (rule path) so the rest
    of the harness — dry-run, executor, verifier, rollback — doesn't
    care which planner produced the plan. Outline §10.7 ("new Skill
    doesn't touch Harness Kernel") holds for both planner variants.

    Phase 11: when ``prior_plan_actions`` + ``user_hint`` are supplied,
    a refinement user turn is prepended that echoes the prior plan +
    the clarification — same mechanic as the main agent planner.
    """
    if client is None:
        client = _default_client()

    workspace_root = Path(snapshot.root)
    tool_schema = build_analysis_spec_tool_schema()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": render_user_prompt(task, snapshot)}
    ]
    if prior_plan_actions is not None and user_hint:
        messages.append(
            {
                "role": "user",
                "content": _build_refinement_message(prior_plan_actions, user_hint),
            }
        )

    response: StructuredResponse | None = None

    for attempt in range(1, max_attempts + 1):
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            response = client.generate_structured(
                system=SYSTEM_PROMPT,
                messages=messages,
                tool_name=TOOL_NAME,
                tool_description=TOOL_DESCRIPTION,
                tool_schema=tool_schema,
                on_delta=on_delta,
            )
        except LLMClientError as exc:
            raise AnalysisPlannerFailure(f"LLM call failed on attempt {attempt}: {exc}") from exc

        # Translate the LLM-friendly payload back into AnalysisSpec.
        spec, errors = _coerce_payload_to_spec(response.payload)
        if spec is not None:
            # Validate that referenced columns / file exist before
            # hitting the engine — clearer error than a runtime KeyError.
            validation_errors = _semantic_validate(spec, snapshot, workspace_root)
            if not validation_errors:
                break  # accepted!
            errors = validation_errors

        if attempt == max_attempts:
            joined = "\n".join(f"- {e}" for e in errors)
            raise AnalysisPlannerFailure(
                f"LLM produced an invalid AnalysisSpec after {max_attempts} attempt(s):\n{joined}"
            )

        # Repair turn: feed errors back as tool_result.
        messages = messages + [
            {"role": "assistant", "content": response.raw_assistant_content},
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
            },
        ]

    # spec is guaranteed non-None here because we either broke out of the
    # loop with a valid spec or raised AnalysisPlannerFailure above.
    assert spec is not None

    # Run the engine against the LLM-chosen source file.
    result = _execute_spec_against_workspace(spec, workspace_root)

    # v0.16.1 — single self-eval retry on empty / error results. If the
    # first spec produced no useful rows, ask the LLM to try a simpler
    # alternative (groupby a low-cardinality categorical on count + no
    # filters). One retry only — beyond that we accept the empty result
    # and let the user trigger Phase 13's auto-repair or refine manually.
    if (
        result.outcome.value in ("empty_result", "invalid_spec", "execution_error")
        and response is not None
    ):
        result = _retry_with_empty_result_hint(
            client=client,
            task=task,
            snapshot=snapshot,
            workspace_root=workspace_root,
            first_spec=spec,
            first_result=result,
            on_delta=on_delta,
        )

    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    return build_plan_from_results(
        plan_id=plan_id,
        task=task,
        workspace_root=workspace_root,
        results=[result],
        input_file_count=1,
    )


def _retry_with_empty_result_hint(
    *,
    client: LLMClient,
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    workspace_root: Path,
    first_spec: AnalysisSpec,
    first_result: Any,
    on_delta,
):
    """v0.16.1 — one-shot retry when the first spec produced an empty /
    invalid / error outcome. Synthesises a hint user-message telling
    the LLM what went wrong + asking for a simpler default."""
    hint = (
        "Your first AnalysisSpec produced an empty / errored result on this "
        "dataset (outcome=" + first_result.outcome.value + "). Common causes: "
        "the column you picked is mostly null in the actual data, the filter "
        "rejected every row, or the aggregation operates on non-numeric "
        "data. Re-emit a SIMPLER spec: pick a different categorical column "
        "with low cardinality (≤ 10 distinct values) and aggregate row "
        "counts (no filter, no sort). If the previous spec's source_file "
        "looked viable, keep it; if not, pick a different file from the "
        "workspace summary."
    )
    tool_schema = build_analysis_spec_tool_schema()
    messages = [
        {"role": "user", "content": render_user_prompt(task, snapshot)},
        {"role": "user", "content": hint},
    ]
    try:
        response = client.generate_structured(
            system=SYSTEM_PROMPT,
            messages=messages,
            tool_name=TOOL_NAME,
            tool_description=TOOL_DESCRIPTION,
            tool_schema=tool_schema,
            on_delta=on_delta,
        )
    except LLMClientError:
        # Retry call itself failed — return the original (empty) result;
        # the user can refine manually.
        return first_result

    retry_spec, retry_errors = _coerce_payload_to_spec(response.payload)
    if retry_spec is None or retry_errors:
        return first_result
    validation_errors = _semantic_validate(retry_spec, snapshot, workspace_root)
    if validation_errors:
        return first_result
    retry_result = _execute_spec_against_workspace(retry_spec, workspace_root)
    if retry_result.outcome.value == "ok":
        return retry_result
    # Retry also failed — return whichever has more useful info (prefer ok).
    return first_result


# --------------------------------------------------------------------- payload coercion


def _coerce_payload_to_spec(payload: dict[str, Any]) -> tuple[AnalysisSpec | None, list[str]]:
    """Convert the LLM's tool_call payload into a typed AnalysisSpec.

    The schema sent to the model represents ``aggregations`` as a list
    of ``{column, op}`` records (OpenAI strict mode doesn't allow
    open-ended object dicts). We unpack that into the
    ``dict[str, AggregationOp]`` that the Pydantic AnalysisSpec expects.

    Returns (spec, errors). On success errors is [].
    """
    errors: list[str] = []
    try:
        payload = dict(payload)
        groupby_raw = payload.get("groupby")
        if isinstance(groupby_raw, dict):
            aggs_list = groupby_raw.get("aggregations")
            if isinstance(aggs_list, list):
                aggs_dict: dict[str, AggregationOp] = {}
                for entry in aggs_list:
                    if not isinstance(entry, dict):
                        errors.append(
                            f"aggregations entry must be an object, got {type(entry).__name__}"
                        )
                        continue
                    col = entry.get("column")
                    op = entry.get("op")
                    if not col or not op:
                        errors.append(f"aggregations entry missing column/op: {entry}")
                        continue
                    aggs_dict[str(col)] = AggregationOp(op)
                payload["groupby"] = {**groupby_raw, "aggregations": aggs_dict}
        spec = AnalysisSpec.model_validate(payload)
        return spec, []
    except ValidationError as exc:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", []))
            errors.append(f"{loc or '<root>'}: {e.get('msg', '')}")
        return None, errors
    except Exception as exc:  # ValueError from AggregationOp(...), etc.
        errors.append(f"{type(exc).__name__}: {exc}")
        return None, errors


def _semantic_validate(
    spec: AnalysisSpec,
    snapshot: WorkspaceSnapshot,
    workspace_root: Path,
) -> list[str]:
    """Cheap pre-flight check: does source_file exist + are the
    referenced columns plausible? Catches LLM hallucinations before
    we burn an engine run."""
    errors: list[str] = []

    # source_file must exist
    file_paths = {f.path for f in snapshot.files}
    if spec.source_file not in file_paths:
        errors.append(
            f"source_file={spec.source_file!r} not in workspace. "
            f"Available tabular files: {sorted(f for f in file_paths if f.endswith(('.csv', '.tsv', '.xlsx', '.xls')))[:10]}"
        )
        return errors  # rest of checks need a real file

    # Try to peek at columns (only for chart/sort_by validation; engine
    # will catch missing columns again at execution time).
    try:
        from app.tools.data_ops import read_tabular

        reads = read_tabular(workspace_root / spec.source_file, spec.source_file)
        # Pick the sheet (if specified) or first.
        df = None
        for tr in reads:
            if spec.sheet:
                if tr.display_path.endswith(f"(sheet: {spec.sheet})"):
                    df = tr.df
                    break
            else:
                df = tr.df
                break
        if df is None:
            return errors  # engine will report read error
        cols = set(df.columns.astype(str))

        # Collect every column reference in the spec.
        referenced: set[str] = set()
        for f in spec.filters:
            referenced.add(f.column)
        if spec.groupby is not None:
            referenced.update(spec.groupby.by)
            referenced.update(spec.groupby.aggregations.keys())
        if spec.chart is not None:
            referenced.add(spec.chart.x)
            if spec.chart.y:
                referenced.add(spec.chart.y)
        # sort_by may reference post-groupby columns; skip strict check.

        missing = referenced - cols
        # post-groupby aggregation result columns share the source name,
        # so if a chart references "amount" after a groupby of {amount: mean},
        # that's fine — it's in `cols`. The only tricky case is if the
        # user references a synthesized name we don't produce.
        # For MVP just flag truly missing columns.
        if missing:
            errors.append(
                f"columns referenced by spec but absent from {spec.source_file}: {sorted(missing)}; "
                f"available: {sorted(cols)[:20]}"
            )
    except Exception as exc:
        errors.append(f"could not preflight-read {spec.source_file}: {exc}")

    return errors


# --------------------------------------------------------------------- engine glue


def _execute_spec_against_workspace(spec: AnalysisSpec, workspace_root: Path) -> AnalysisResult:
    """Load the file the LLM chose and run the engine."""
    from app.tools import data_analysis, data_ops

    abs_path = workspace_root / spec.source_file
    reads = data_ops.read_tabular(abs_path, spec.source_file)
    df = None
    for tr in reads:
        if spec.sheet:
            if tr.display_path.endswith(f"(sheet: {spec.sheet})"):
                df = tr.df
                break
        else:
            df = tr.df
            if tr.error is None:
                break

    if df is None:
        return AnalysisResult(
            spec=spec,
            outcome=AnalysisOutcome.READ_ERROR,
            error=f"could not load DataFrame from {spec.source_file}",
            summary=f"{spec.source_file}: read error",
        )

    return data_analysis.execute_analysis(df, spec)
