from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from typing import Any

from app.agent.client import LLMClientError, StructuredResponse

DEFAULT_MODEL = "gpt-4o-mini"
"""Default model. Override via the ``LOCALFLOW_OPENAI_MODEL`` env var.

The client targets the **OpenAI /v1/chat/completions** endpoint, which
empirically yields lower latency than /v1/responses on
OpenAI-compatible relays (the latter often runs implicit reasoning
pipelines even on non-reasoning models). For the canonical OpenAI API
this distinction is moot; for self-hosted relays it can be 2-3x.
"""

DEFAULT_MAX_TOKENS = 16000


class OpenAIClient:
    """LLMClient backed by the **OpenAI /v1/chat/completions** endpoint.

    Targets /v1/chat/completions rather than /v1/responses — the former
    has lower latency on OpenAI-compatible relays that wrap reasoning
    pipelines around non-reasoning models. On the canonical OpenAI API
    either endpoint works.

    Configured via env vars:

    ===========================================  =====================================
    Variable                                     Purpose
    ===========================================  =====================================
    ``OPENAI_API_KEY``                           Auth (required unless ``api_key`` arg)
    ``OPENAI_BASE_URL``                          Custom endpoint (proxy / self-hosted)
    ``LOCALFLOW_OPENAI_MODEL``                   Model ID; default ``gpt-4o-mini``
    ``LOCALFLOW_OPENAI_REASONING_EFFORT``        ``low``/``medium``/``high`` (optional)
    ``LOCALFLOW_OPENAI_DISABLE_STORAGE``         ``true`` → send ``store=False``
    ``LOCALFLOW_OPENAI_TIMEOUT``                 Per-request timeout in seconds (180)
    ===========================================  =====================================
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        reasoning_effort: str | None = None,
        api_key: str | None = None,
        disable_storage: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise LLMClientError(
                "the `openai` package is not installed; "
                'run `pip install "openai>=1.50"` or `pip install -e ".[openai]"`'
            ) from exc

        if api_key is None:
            api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMClientError(
                "OPENAI_API_KEY not set. Put it in .env, export it in your shell, "
                "or pass api_key=... ; alternatively use --llm-provider anthropic "
                "or --planner rule."
            )

        if timeout is None:
            timeout_env = os.environ.get("LOCALFLOW_OPENAI_TIMEOUT")
            timeout = float(timeout_env) if timeout_env else 180.0

        self._openai = openai
        # OPENAI_BASE_URL is read by the SDK automatically when we don't
        # pass base_url explicitly.
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout)
        self.model = model or os.environ.get("LOCALFLOW_OPENAI_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort or os.environ.get(
            "LOCALFLOW_OPENAI_REASONING_EFFORT"
        )
        if disable_storage is None:
            self.disable_storage = _truthy(os.environ.get("LOCALFLOW_OPENAI_DISABLE_STORAGE"))
        else:
            self.disable_storage = bool(disable_storage)

    # -- public API ---------------------------------------------------

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
        chat_messages = self._to_chat_messages(system, messages)
        # chat.completions tool definition nests under "function".
        # OpenAI strict function-calling rejects any object schema with
        # additionalProperties != false (R4 fix#2). Some kernel-built
        # schemas (e.g. submit_loop_decision's replacement_action.metadata)
        # set additionalProperties:true for Anthropic's more lenient strict
        # mode; sanitise a copy so the OpenAI path accepts them. The
        # trade-off is that free-form dict fields can only be emitted as {}.
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_description,
                "parameters": _force_strict_object_schema(copy.deepcopy(tool_schema)),
                "strict": True,
            },
        }
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
            "parallel_tool_calls": False,
            "max_completion_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.disable_storage:
            kwargs["store"] = False

        if on_delta is not None:
            return self._generate_streaming(kwargs, tool_name, on_delta)
        return self._generate_blocking(kwargs, tool_name)

    # -- non-streaming path ------------------------------------------

    def _generate_blocking(self, kwargs: dict[str, Any], tool_name: str) -> StructuredResponse:
        try:
            response = self._client.chat.completions.create(**kwargs)
        except self._openai.APIError as exc:
            raise LLMClientError(f"OpenAI chat.completions error: {exc}") from exc

        choice = response.choices[0]
        msg = choice.message
        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        target = next(
            (tc for tc in tool_calls if tc.function and tc.function.name == tool_name),
            None,
        )
        if target is None:
            raise LLMClientError(
                f"model did not call required tool {tool_name!r}; "
                f"finish_reason={choice.finish_reason!r}"
            )

        try:
            payload = json.loads(target.function.arguments)
        except json.JSONDecodeError as exc:
            raise LLMClientError(
                f"tool call arguments are not valid JSON: {exc}; raw="
                f"{target.function.arguments[:200]!r}"
            ) from exc

        raw_assistant_content = [
            {
                "type": "tool_use",
                "id": target.id,
                "name": tool_name,
                "input": payload,
            }
        ]
        return StructuredResponse(
            tool_use_id=target.id,
            payload=payload,
            raw_assistant_content=raw_assistant_content,
            usage=_usage_dict(response.usage),
            stop_reason=choice.finish_reason,
        )

    # -- streaming path ----------------------------------------------

    def _generate_streaming(
        self,
        kwargs: dict[str, Any],
        tool_name: str,
        on_delta: Callable[[str], None],
    ) -> StructuredResponse:
        """Stream chat.completions and fire on_delta for every chunk of
        ``tool_calls[0].function.arguments`` as the model emits it.

        We accumulate the full arguments string and parse it once the
        stream finishes — partial JSON isn't parseable, but the user
        watching the screen sees the response building in real time.
        """
        stream_kwargs = {
            **kwargs,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            stream = self._client.chat.completions.create(**stream_kwargs)
        except self._openai.APIError as exc:
            raise LLMClientError(f"OpenAI stream open error: {exc}") from exc

        tool_call_id: str | None = None
        args_parts: list[str] = []
        finish_reason: str | None = None
        usage_obj: Any = None

        try:
            for chunk in stream:
                # Some chunks (especially the trailing usage one) have
                # an empty choices list — skip those for the delta loop.
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        for tc in getattr(delta, "tool_calls", None) or []:
                            if getattr(tc, "id", None):
                                tool_call_id = tc.id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                arg_chunk = getattr(fn, "arguments", None)
                                if arg_chunk:
                                    args_parts.append(arg_chunk)
                                    on_delta(arg_chunk)
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage_obj = chunk_usage
        except self._openai.APIError as exc:
            raise LLMClientError(f"OpenAI stream read error: {exc}") from exc

        if tool_call_id is None or not args_parts:
            raise LLMClientError(
                f"stream produced no tool call for {tool_name!r}; finish_reason={finish_reason!r}"
            )

        full_args = "".join(args_parts)
        try:
            payload = json.loads(full_args)
        except json.JSONDecodeError as exc:
            raise LLMClientError(
                f"streamed tool call arguments are not valid JSON: {exc}; raw={full_args[:200]!r}"
            ) from exc

        raw_assistant_content = [
            {
                "type": "tool_use",
                "id": tool_call_id,
                "name": tool_name,
                "input": payload,
            }
        ]
        return StructuredResponse(
            tool_use_id=tool_call_id,
            payload=payload,
            raw_assistant_content=raw_assistant_content,
            usage=_usage_dict(usage_obj),
            stop_reason=finish_reason or "tool_calls",
        )

    # -- translation --------------------------------------------------

    def _to_chat_messages(
        self, system: str, anthropic_messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Anthropic-shape -> /v1/chat/completions ``messages`` list.

        Anthropic (planner emits):
          [
            {"role": "user", "content": "<string>"},
            {"role": "assistant", "content": [tool_use_block, ...]},
            {"role": "user", "content": [tool_result_block, ...]},
          ]

        OpenAI chat:
          [
            {"role": "system", "content": "<string>"},
            {"role": "user", "content": "<string>"},
            {"role": "assistant", "content": null,
             "tool_calls": [{"id": ..., "type": "function",
                             "function": {"name": ..., "arguments": "<json>"}}]},
            {"role": "tool", "tool_call_id": ..., "content": "<string>"},
          ]
        """
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for msg in anthropic_messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                out.extend(self._translate_user(content))
            elif role == "assistant":
                out.append(self._translate_assistant(content))
            else:
                raise LLMClientError(f"unsupported message role: {role!r}")
        return out

    @staticmethod
    def _translate_user(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"role": "user", "content": content}]
        if not isinstance(content, list):
            raise LLMClientError(f"unsupported user content shape: {type(content)!r}")

        out: list[dict[str, Any]] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                out.append({"role": "user", "content": block.get("text", "")})
            elif btype == "tool_result":
                tool_content = block.get("content", "")
                if isinstance(tool_content, list):
                    tool_content = "\n".join(
                        b.get("text", "") for b in tool_content if b.get("type") == "text"
                    )
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(tool_content),
                    }
                )
            else:
                raise LLMClientError(f"unsupported user-content block type: {btype!r}")
        return out

    @staticmethod
    def _translate_assistant(content: Any) -> dict[str, Any]:
        if not isinstance(content, list):
            raise LLMClientError(f"unsupported assistant content shape: {type(content)!r}")

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )
            elif btype == "thinking":
                continue  # no chat.completions equivalent
            else:
                continue

        msg: dict[str, Any] = {"role": "assistant"}
        msg["content"] = "\n".join(text_parts) if text_parts else None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg


# --------------------------------------------------------------------- helpers


def _force_strict_object_schema(node: Any) -> Any:
    """Recursively make a JSON schema OpenAI-strict-compatible.

    OpenAI strict function-calling imposes two rules on every object
    schema that Anthropic's more lenient strict mode does not:

    1. ``additionalProperties`` MUST be ``false`` (no free-form dicts).
    2. ``required`` MUST list *every* key in ``properties``.

    Some kernel-built schemas (e.g. ``submit_loop_decision``) were authored
    for Anthropic strict mode and violate both. We rewrite a copy so the
    OpenAI path accepts them. Recurses through ``properties``, ``items``,
    ``$defs``/``definitions`` and the ``anyOf``/``allOf``/``oneOf`` branch
    lists. Mutates ``node`` in place and returns it.

    A free-form dict field (an object schema with no declared
    ``properties`` — e.g. an Action's ``metadata``) cannot be expressed
    under strict mode at all: forcing ``additionalProperties:false`` leaves
    a property-less object that OpenAI strips, leaving a dangling
    ``required`` entry. Such fields are therefore **dropped** from the
    request schema — the model simply won't be asked to produce them
    (their pydantic defaults apply on parse).

    Safe on the OpenAI path: a schema that violates these rules is already
    rejected under strict mode, so enforcing them can only fix or no-op,
    never regress. Trade-off: free-form dict fields are omitted from the
    request, and previously-optional scalar fields become mandatory.
    """
    if isinstance(node, dict):
        if node.get("type") == "object":
            props = node.get("properties")
            if isinstance(props, dict):
                for key in [k for k, v in props.items() if _is_freeform_object(v)]:
                    del props[key]
                node["required"] = list(props.keys())
            node["additionalProperties"] = False
        for value in node.values():
            _force_strict_object_schema(value)
    elif isinstance(node, list):
        for item in node:
            _force_strict_object_schema(item)
    return node


def _is_freeform_object(schema: Any) -> bool:
    """True when ``schema`` is (or unions) a free-form dict — an object
    type with no declared ``properties``. These can't be expressed under
    OpenAI strict mode and are dropped from the request schema."""
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "object" and not schema.get("properties"):
        return True
    for branch in (schema.get("anyOf") or []) + (schema.get("oneOf") or []):
        if (
            isinstance(branch, dict)
            and branch.get("type") == "object"
            and not branch.get("properties")
        ):
            return True
    return False


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = 0
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
    }
