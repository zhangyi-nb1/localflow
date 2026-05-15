"""Phase 13 — LLM-as-judge thin wrapper used by semantic graders.

Semantic graders need a one-shot Q/A call with a tiny structured
response. Building the tool schema and handling provider differences
every time would multiply boilerplate across the grader files, so we
collapse that here.

Design notes:

- Reuses :func:`app.agent.planner._default_client` for client
  selection — no separate config knob.
- Returns ``None`` when no LLM client is available (no API key, etc.)
  so callers can degrade gracefully rather than crash. The
  semantic verifier interprets None as "grader skipped".
- Hard-caps the user prompt at 8K characters before sending so a
  rogue report file doesn't blow the per-call token budget.
- Tool schema is generated once at module load (immutable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.client import LLMClient, LLMClientError

MAX_PROMPT_CHARS = 8000
"""Trim the user prompt to this many characters before sending. Mostly
guards against a giant report file accidentally fed to the judge."""

JUDGE_TOOL_NAME = "submit_verdict"
JUDGE_TOOL_DESCRIPTION = (
    "Submit a yes/no verdict on whether the produced output meets the "
    "grader's bar. Always provide a short reason. When verdict=false, "
    "provide a one-sentence suggested_hint phrased as an instruction "
    "for the planner that would address the failure."
)

JUDGE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "boolean",
            "description": "True iff the output meets the grader's bar.",
        },
        "reason": {
            "type": "string",
            "description": "Short human-readable why (max 1-2 sentences).",
            "maxLength": 500,
        },
        "suggested_hint": {
            "type": "string",
            "description": (
                "When verdict=false, an instruction phrased for the LLM "
                "planner that would address the rejection. Empty string "
                "when verdict=true."
            ),
            "maxLength": 300,
        },
    },
    "required": ["verdict", "reason", "suggested_hint"],
    "additionalProperties": False,
}


@dataclass
class JudgeVerdict:
    """Result of one judge call. Matches the tool schema shape."""

    verdict: bool
    reason: str
    suggested_hint: str
    token_usage: dict[str, int]


def get_default_client_or_none() -> LLMClient | None:
    """Resolve the default LLM client, returning None instead of raising
    if the environment isn't configured. Lets graders skip silently in
    CI / dev environments without API keys."""
    try:
        from app.agent.planner import _default_client

        return _default_client()
    except (LLMClientError, ImportError):
        return None
    except Exception:
        # Any other failure (network probe, env parse, etc.) — treat
        # the same as "no client available" rather than poisoning the
        # whole semantic verification pass.
        return None


def judge(
    *,
    system: str,
    user: str,
    client: LLMClient | None = None,
) -> JudgeVerdict | None:
    """Single-shot LLM judge call returning the typed verdict.

    Returns ``None`` when no client is available — callers should
    treat that as "skip this grader" and continue with the rest of
    the verification pass.
    """
    if client is None:
        client = get_default_client_or_none()
    if client is None:
        return None

    trimmed_user = user[:MAX_PROMPT_CHARS]
    try:
        response = client.generate_structured(
            system=system,
            messages=[{"role": "user", "content": trimmed_user}],
            tool_name=JUDGE_TOOL_NAME,
            tool_description=JUDGE_TOOL_DESCRIPTION,
            tool_schema=JUDGE_TOOL_SCHEMA,
        )
    except LLMClientError:
        return None
    except Exception:
        return None

    payload = response.payload or {}
    verdict_val = bool(payload.get("verdict", False))
    reason = str(payload.get("reason", "")).strip()
    suggested_hint = str(payload.get("suggested_hint", "")).strip()
    return JudgeVerdict(
        verdict=verdict_val,
        reason=reason,
        suggested_hint=suggested_hint,
        token_usage=dict(response.usage or {}),
    )
