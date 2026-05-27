"""Phase 30.1 — LLM client Protocol surface.

Lives in ``localflow_kernel`` because every kernel module that needs to
ask an LLM for a structured decision (currently only
``app.harness.react_loop``) depends on this Protocol, NOT on a concrete
provider implementation. The provider implementations (``AnthropicClient``,
``FakeLLMClient``) stay in ``app/agent/client.py`` because they pull in
the ``anthropic`` SDK and other application-layer concerns.

Back-compat: ``app.agent.client`` re-exports ``LLMClient``,
``LLMClientError``, and ``StructuredResponse`` from this module so every
existing import site (~50+ in tests and runtime) keeps working without
churn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMClientError(RuntimeError):
    """Wraps any provider error so callers depend only on this exception."""


@runtime_checkable
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
    ) -> "StructuredResponse": ...


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
