from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 16000


class LLMClientError(RuntimeError):
    """Wraps any provider error so callers depend only on this exception."""


class LLMClient(Protocol):
    """Provider-agnostic interface for forced-tool-call structured output.

    The contract: given a system prompt, a conversation, and a tool schema,
    return the dict the model placed in the forced tool call's ``input``.
    Implementations are free to use tool use, JSON mode, or grammar
    constraints — callers see only the validated dict.

    If ``on_delta`` is provided, implementations SHOULD stream the
    response and invoke the callback with each incremental chunk of the
    tool_call.arguments JSON string. This is a UX hint — callers use it
    to show progressive output. Implementations that don't support
    streaming can ignore it (calling it once at the end is acceptable).
    """

    def generate_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> "StructuredResponse":
        ...


@dataclass
class StructuredResponse:
    """Wraps the model's forced tool call along with audit metadata."""

    tool_use_id: str
    """ID of the tool_use block — needed to thread a tool_result on repair."""

    payload: dict[str, Any]
    """The dict the model placed in the tool_use ``input`` field."""

    raw_assistant_content: list[dict[str, Any]] = field(default_factory=list)
    """The full ``response.content`` echoed back into messages on repair."""

    usage: dict[str, int] = field(default_factory=dict)
    """Token counts: input / output / cache_read / cache_creation."""

    stop_reason: str | None = None


# --------------------------------------------------------------------- Anthropic


class AnthropicClient:
    """Production LLMClient backed by the Anthropic SDK.

    Defaults mandated by the claude-api skill:
      * model: ``claude-opus-4-7`` (override via env ``LOCALFLOW_LLM_MODEL``
        or constructor kwarg)
      * adaptive thinking
      * effort: high
      * cached, immutable system prompt
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = "high",
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMClientError(
                "the `anthropic` package is not installed; "
                "run `pip install anthropic` or `pip install -e .[dev]`"
            ) from exc

        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMClientError(
                "ANTHROPIC_API_KEY not set. Export it, or pass api_key=... ; "
                "alternatively use --planner rule to skip the LLM entirely."
            )

        if timeout is None:
            timeout_env = os.environ.get("LOCALFLOW_ANTHROPIC_TIMEOUT")
            timeout = float(timeout_env) if timeout_env else 180.0

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model or os.environ.get("LOCALFLOW_LLM_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.effort = effort

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
        # AnthropicClient does not implement streaming yet — it just
        # invokes the callback once with the full payload at the end so
        # callers that pass on_delta still get a single notification.
        tool = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": tool_schema,
            # `strict` constrains the model to emit a payload that matches
            # the schema exactly (no extra keys, all required fields set).
            "strict": True,
        }
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=[
                    {
                        "type": "text",
                        "text": system,
                        # The system prompt is stable across plan calls,
                        # so cache it. Workspace summary varies per call
                        # and lives in `messages` — naturally reused only
                        # within a single plan call's repair loop.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[tool],
                tool_choice={
                    "type": "tool",
                    "name": tool_name,
                    "disable_parallel_tool_use": True,
                },
                messages=messages,
            )
        except self._anthropic.APIError as exc:
            raise LLMClientError(f"Anthropic API error: {exc}") from exc

        tool_use = _find_tool_use_block(response.content, tool_name)
        if tool_use is None:
            raise LLMClientError(
                f"model did not call the required tool {tool_name!r}; "
                f"stop_reason={response.stop_reason!r}"
            )

        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(
                response.usage, "cache_creation_input_tokens", 0
            ) or 0,
        }

        payload = dict(tool_use.input)
        if on_delta is not None:
            import json as _json
            on_delta(_json.dumps(payload, ensure_ascii=False))

        return StructuredResponse(
            tool_use_id=tool_use.id,
            payload=payload,
            raw_assistant_content=[_block_to_param(b) for b in response.content],
            usage=usage,
            stop_reason=response.stop_reason,
        )


def _find_tool_use_block(content: Iterable[Any], tool_name: str):
    for block in content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            return block
    return None


def _block_to_param(block: Any) -> dict[str, Any]:
    """Convert a response ContentBlock back into the param shape we can
    re-send as part of an assistant turn.

    The SDK objects support model_dump(); fall back to manual extraction
    for the variants we care about.
    """
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input),
        }
    if btype == "thinking":
        # Thinking blocks have an opaque signature that must be preserved
        # verbatim when echoed back; SDK model_dump handles this above.
        return {
            "type": "thinking",
            "thinking": getattr(block, "thinking", ""),
            "signature": getattr(block, "signature", ""),
        }
    raise LLMClientError(f"unsupported content block type: {btype!r}")


# --------------------------------------------------------------------- Fake


class FakeLLMClient:
    """Deterministic LLMClient for tests.

    Pre-load a list of ``payload`` dicts; each ``generate_structured`` call
    pops the next one. Records every invocation in ``self.calls`` so tests
    can assert what the planner sent.
    """

    def __init__(self, payloads: list[dict[str, Any] | Exception]) -> None:
        self._queue: list[dict[str, Any] | Exception] = list(payloads)
        self.calls: list[dict[str, Any]] = []
        self._next_id = 0

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
                "system": system,
                "messages": [dict(m) for m in messages],
                "tool_name": tool_name,
                "tool_schema": tool_schema,
            }
        )
        if not self._queue:
            raise LLMClientError("FakeLLMClient queue exhausted")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        self._next_id += 1
        tool_use_id = f"toolu_fake_{self._next_id:03d}"
        if on_delta is not None:
            import json as _json
            on_delta(_json.dumps(item, ensure_ascii=False))
        return StructuredResponse(
            tool_use_id=tool_use_id,
            payload=dict(item),
            raw_assistant_content=[
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": tool_name,
                    "input": dict(item),
                }
            ],
            usage={"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            stop_reason="tool_use",
        )
