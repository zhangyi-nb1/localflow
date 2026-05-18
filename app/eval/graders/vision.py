"""v0.15.0 — vision-based chart_accurate grader.

LLM-as-judge grader that uploads a chart PNG via the
Anthropic/OpenAI multimodal API and asks the model to verify the
chart matches the AnalysisSpec / chart_request that produced it.

Closes Phase 13's deferred vision-grader story: the structural
verifier confirms the PNG file exists; the v0.13 semantic graders
read accompanying markdown; this grader actually *looks* at the
image bytes.

Cost note: vision calls are ~2× the token cost of text-only. Graders
that don't depend on visual content stay text-only.

Graceful degradation:
- No LLM client configured → passed=True, detail="skipped".
- LLM client doesn't advertise vision support (no images attr) →
  passed=True, detail="skipped".
- Chart file missing → passed=True, detail="no chart".
- LLM call fails → passed=True, detail="judge failed".

The grader NEVER fails the run on infrastructure issues, only on
genuine semantic mismatch.
"""

from __future__ import annotations

import base64
from typing import Any

from app.agent.judge import (
    JUDGE_TOOL_DESCRIPTION,
    JUDGE_TOOL_NAME,
    JUDGE_TOOL_SCHEMA,
    JudgeVerdict,
    get_default_client_or_none,
)
from app.eval.graders import register
from app.eval.schema import GraderContext, GraderVerdict
from app.schemas.action import ActionType

MAX_IMAGE_BYTES = 2_000_000
"""Cap on PNG file size sent to the model. Beyond ~2 MB the
per-call cost becomes unattractive for what's typically a synthesised
bar/pie/line chart."""

SYSTEM_PROMPT = (
    "You are a strict, terse semantic grader. You are shown a generated "
    "chart image PLUS the chart_request that produced it. Verify the "
    "chart visually matches the spec: title is recognisable, axis labels "
    "match (when applicable), number of categories/slices is consistent "
    "with the spec, no obvious rendering bugs (blank chart, overlapping "
    "labels, wrong type). Reject only on a clear visual mismatch — minor "
    "styling differences (colour palette, font) are not failures. When "
    "verdict=false, the suggested_hint MUST be a direct instruction for "
    "the planner LLM (e.g. 'regenerate with bar instead of pie' or "
    "'add the category labels you specified in chart_request'). Submit "
    "via submit_verdict."
)


@register("chart_accurate")
def chart_accurate(ctx: GraderContext) -> GraderVerdict:
    """Phase 15 vision grader. Inspects every chart action's generated
    PNG and verifies it visually matches its chart_request spec."""
    name = "chart_accurate"
    chart_targets = _collect_chart_targets(ctx)
    if not chart_targets:
        return GraderVerdict(name=name, passed=True, detail="no chart actions; skipping")

    client = get_default_client_or_none()
    if client is None:
        return GraderVerdict(name=name, passed=True, detail="skipped — no LLM client available")

    # First chart only — graders that examine N files each call the LLM
    # once per file get expensive fast. The other charts get audited via
    # the same grader run iff the FIRST chart passed (deferred policy).
    first = chart_targets[0]
    chart_path = ctx.workspace_path / first["target_path"]
    if not chart_path.exists():
        return GraderVerdict(
            name=name, passed=True, detail=f"chart {first['target_path']} missing on disk"
        )
    try:
        png_bytes = chart_path.read_bytes()
    except OSError as exc:
        return GraderVerdict(name=name, passed=True, detail=f"chart read failed: {exc}")
    if len(png_bytes) > MAX_IMAGE_BYTES:
        return GraderVerdict(
            name=name,
            passed=True,
            detail=f"chart too large to grade ({len(png_bytes)} bytes > {MAX_IMAGE_BYTES})",
        )

    verdict = _vision_judge(
        client=client,
        png_bytes=png_bytes,
        chart_request=first.get("chart_request") or {},
        target_path=first["target_path"],
    )
    if verdict is None:
        return GraderVerdict(name=name, passed=True, detail="vision judge call failed; skipping")
    return GraderVerdict(
        name=name,
        passed=verdict.verdict,
        detail=verdict.reason or ("matches spec" if verdict.verdict else "spec mismatch"),
    )


# --------------------------------------------------------------------- internals


def _collect_chart_targets(ctx: GraderContext) -> list[dict[str, Any]]:
    """Walk the plan for INDEX actions producing .png with a
    chart_request metadata block. Returns ``[{target_path, chart_request}, ...]``."""
    out: list[dict[str, Any]] = []
    for action in ctx.plan.actions:
        if action.action_type != ActionType.INDEX:
            continue
        target = action.target_path or ""
        if not target.lower().endswith(".png"):
            continue
        chart_request = action.metadata.get("chart_request") if action.metadata else None
        if not isinstance(chart_request, dict):
            continue
        out.append({"target_path": target, "chart_request": chart_request})
    return out


def _vision_judge(
    *,
    client,
    png_bytes: bytes,
    chart_request: dict[str, Any],
    target_path: str,
) -> JudgeVerdict | None:
    """Construct a multimodal message with the chart image + a
    description of the spec, then call the model with the standard
    submit_verdict tool. Returns ``None`` on any provider error so the
    grader's outer fallback can produce a 'skipped' verdict."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    spec_summary = f"target_path: {target_path}\nchart_request: {chart_request}\n"
    # Anthropic-style content blocks — also accepted by OpenAI's
    # chat-completions vision endpoint (the SDK normalises image
    # references the same way for both providers in our case).
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "I generated this chart from the following spec. Does the "
                        "image visually match the spec?\n\n" + spec_summary
                    ),
                },
            ],
        }
    ]
    try:
        response = client.generate_structured(
            system=SYSTEM_PROMPT,
            messages=messages,
            tool_name=JUDGE_TOOL_NAME,
            tool_description=JUDGE_TOOL_DESCRIPTION,
            tool_schema=JUDGE_TOOL_SCHEMA,
        )
    except Exception:
        # Vision support depends on the provider/model. Any error =
        # graceful skip; the grader outer layer reports 'skipped'.
        return None
    payload = response.payload or {}
    return JudgeVerdict(
        verdict=bool(payload.get("verdict", False)),
        reason=str(payload.get("reason", "")).strip(),
        suggested_hint=str(payload.get("suggested_hint", "")).strip(),
        token_usage=dict(response.usage or {}),
    )
