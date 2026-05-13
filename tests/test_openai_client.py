"""Tests for OpenAIClient (chat.completions wire API).

Uses a stubbed openai.OpenAI so no network calls happen.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import LLMClientError, OpenAIClient, plan_with_llm
from app.agent.prompts import TOOL_NAME


# --------------------------------------------------------------------- translator


def _client(monkeypatch) -> OpenAIClient:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return OpenAIClient(model="gpt-test")


def test_translate_initial_user_message(monkeypatch) -> None:
    client = _client(monkeypatch)
    out = client._to_chat_messages(
        "SYSTEM PROMPT",
        [{"role": "user", "content": "Hello"}],
    )
    assert out == [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "Hello"},
    ]


def test_translate_full_repair_turn(monkeypatch) -> None:
    client = _client(monkeypatch)
    anthropic_msgs = [
        {"role": "user", "content": "Plan the workspace."},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_abc",
                    "name": "submit_action_plan",
                    "input": {"plan_id": "p-1", "actions": []},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_abc",
                    "is_error": True,
                    "content": "missing field: summary",
                }
            ],
        },
    ]
    out = client._to_chat_messages("SYS", anthropic_msgs)

    # 1 system + 1 user + 1 assistant(tool_calls) + 1 tool = 4
    assert len(out) == 4
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "Plan the workspace."}

    assistant = out[2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert len(assistant["tool_calls"]) == 1
    tc = assistant["tool_calls"][0]
    assert tc["id"] == "call_abc"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "submit_action_plan"
    assert json.loads(tc["function"]["arguments"]) == {"plan_id": "p-1", "actions": []}

    tool_msg = out[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_abc"
    assert tool_msg["content"] == "missing field: summary"


def test_translator_drops_thinking_blocks(monkeypatch) -> None:
    client = _client(monkeypatch)
    out = client._to_chat_messages(
        "SYS",
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "...", "signature": "sig"},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "submit_action_plan",
                        "input": {},
                    },
                ],
            }
        ],
    )
    # Thinking dropped, tool_use becomes tool_calls.
    assert len(out) == 2  # system + assistant
    assert out[1]["tool_calls"][0]["id"] == "call_1"


# --------------------------------------------------------------------- error paths


def test_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMClientError, match="OPENAI_API_KEY"):
        OpenAIClient()


def test_env_var_model_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOCALFLOW_OPENAI_MODEL", "gpt-from-env")
    client = OpenAIClient()
    assert client.model == "gpt-from-env"


def test_env_var_disable_storage(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOCALFLOW_OPENAI_DISABLE_STORAGE", "true")
    client = OpenAIClient()
    assert client.disable_storage is True


def test_env_var_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LOCALFLOW_OPENAI_REASONING_EFFORT", "high")
    client = OpenAIClient()
    assert client.reasoning_effort == "high"


# --------------------------------------------------------------------- end-to-end with stubbed chat.completions


def _good_payload(task_id: str) -> dict[str, Any]:
    return {
        "plan_id": "plan-test0001",
        "task_id": task_id,
        "summary": "Move PDFs into papers/, index them.",
        "risk_summary": "Medium; reversible.",
        "expected_outputs": ["papers/index.md"],
        "actions": [
            {
                "action_id": "a-001",
                "action_type": "mkdir",
                "source_path": None,
                "target_path": "papers",
                "reason": "Make papers/.",
                "risk_level": "low",
                "reversible": True,
                "requires_approval": True,
                "confidence": None,
                "metadata": {"content": None},
            },
            {
                "action_id": "a-002",
                "action_type": "move",
                "source_path": "a.pdf",
                "target_path": "papers/a.pdf",
                "reason": "PDF into papers/.",
                "risk_level": "medium",
                "reversible": True,
                "requires_approval": True,
                "confidence": None,
                "metadata": {"content": None},
            },
            {
                "action_id": "a-003",
                "action_type": "move",
                "source_path": "b.pdf",
                "target_path": "papers/b.pdf",
                "reason": "PDF into papers/.",
                "risk_level": "medium",
                "reversible": True,
                "requires_approval": True,
                "confidence": None,
                "metadata": {"content": None},
            },
            {
                "action_id": "a-004",
                "action_type": "index",
                "source_path": None,
                "target_path": "papers/index.md",
                "reason": "Catalog papers/.",
                "risk_level": "low",
                "reversible": True,
                "requires_approval": False,
                "confidence": None,
                "metadata": {"content": "# papers/\n\n- a.pdf\n- b.pdf\n"},
            },
        ],
    }


class _StubOpenAI:
    """Stand-in for ``openai.OpenAI`` with a mocked chat.completions."""

    APIError = type("APIError", (Exception,), {})

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.recorded_calls: list[dict[str, Any]] = []
        self._next_id = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.recorded_calls.append(kwargs)
        if not self._payloads:
            raise RuntimeError("_StubOpenAI: no more queued payloads")
        payload = self._payloads.pop(0)
        self._next_id += 1
        return _build_chat_response(payload, self._next_id)


def _build_chat_response(payload: dict[str, Any], seq: int) -> Any:
    tool_call = SimpleNamespace(
        id=f"call_test_{seq}",
        type="function",
        function=SimpleNamespace(
            name="submit_action_plan",
            arguments=json.dumps(payload),
        ),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_openai_client_with_stub(
    monkeypatch, payloads: list[dict[str, Any]]
) -> tuple[OpenAIClient, _StubOpenAI]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = OpenAIClient(model="gpt-test")
    stub = _StubOpenAI(payloads)
    client._client = stub
    client._openai = SimpleNamespace(APIError=_StubOpenAI.APIError)
    return client, stub


def test_openai_client_end_to_end_happy_path(monkeypatch, task, snapshot) -> None:
    client, stub = _make_openai_client_with_stub(monkeypatch, [_good_payload(task.task_id)])
    plan = plan_with_llm(task, snapshot, client=client)

    assert plan.task_id == task.task_id
    assert len(plan.actions) == 4
    assert len(stub.recorded_calls) == 1
    call = stub.recorded_calls[0]

    assert call["model"] == "gpt-test"
    # chat.completions: tool wrapped in {"type": "function", "function": {...}}
    assert call["tools"][0]["type"] == "function"
    assert call["tools"][0]["function"]["name"] == "submit_action_plan"
    assert call["tools"][0]["function"]["strict"] is True
    assert call["tool_choice"] == {"type": "function", "function": {"name": "submit_action_plan"}}
    assert call["parallel_tool_calls"] is False
    # System message is first.
    assert call["messages"][0]["role"] == "system"
    assert "LocalFlow" in call["messages"][0]["content"]
    # User message contains the goal.
    assert call["messages"][1]["role"] == "user"
    assert task.user_goal in call["messages"][1]["content"]


def test_openai_client_repair_loop(monkeypatch, task, snapshot) -> None:
    bad = _good_payload(task.task_id)
    del bad["summary"]
    good = _good_payload(task.task_id)
    client, stub = _make_openai_client_with_stub(monkeypatch, [bad, good])

    plan = plan_with_llm(task, snapshot, client=client, max_attempts=3)
    assert plan.task_id == task.task_id
    assert len(stub.recorded_calls) == 2

    # Second call must carry the assistant turn + a tool role message.
    second_messages = stub.recorded_calls[1]["messages"]
    roles = [m["role"] for m in second_messages]
    assert "tool" in roles, f"expected a tool message in the repair turn, got {roles}"
    tool_msg = next(m for m in second_messages if m["role"] == "tool")
    assert tool_msg["tool_call_id"].startswith("call_test_")


def test_openai_client_passes_reasoning_effort(monkeypatch, task, snapshot) -> None:
    monkeypatch.setenv("LOCALFLOW_OPENAI_REASONING_EFFORT", "low")
    client, stub = _make_openai_client_with_stub(monkeypatch, [_good_payload(task.task_id)])
    plan_with_llm(task, snapshot, client=client)
    call = stub.recorded_calls[0]
    assert call["reasoning_effort"] == "low"


def test_openai_client_passes_store_false(monkeypatch, task, snapshot) -> None:
    monkeypatch.setenv("LOCALFLOW_OPENAI_DISABLE_STORAGE", "true")
    client, stub = _make_openai_client_with_stub(monkeypatch, [_good_payload(task.task_id)])
    plan_with_llm(task, snapshot, client=client)
    call = stub.recorded_calls[0]
    assert call["store"] is False


# --------------------------------------------------------------------- streaming


class _StreamingStubOpenAI:
    """Stub that returns an iterator of chunk objects when stream=True,
    mimicking the openai SDK's streaming behavior. Splits the payload's
    JSON into N pieces to simulate token-by-token delivery."""

    APIError = type("APIError", (Exception,), {})

    def __init__(self, payload: dict[str, Any], chunk_count: int = 8) -> None:
        self.payload = payload
        self.chunk_count = chunk_count
        self.recorded_calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.recorded_calls.append(kwargs)
        assert kwargs.get("stream") is True, "expected streaming call"
        return self._make_stream()

    def _make_stream(self):
        full = json.dumps(self.payload)
        size = max(1, len(full) // self.chunk_count)
        pieces = [full[i:i + size] for i in range(0, len(full), size)]
        # First chunk: tool_call ID + initial empty arguments fragment
        first = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call_stream_1",
                                function=SimpleNamespace(arguments=""),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        yield first
        # Middle chunks: incremental argument deltas
        for piece in pieces:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    id=None,
                                    function=SimpleNamespace(arguments=piece),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
        # Final chunk: finish_reason, no delta content
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(tool_calls=None),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        )
        # Usage chunk (sent when stream_options.include_usage = True)
        yield SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=200,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )


def test_openai_client_streaming_calls_on_delta(monkeypatch, task, snapshot) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = OpenAIClient(model="gpt-test")
    stub = _StreamingStubOpenAI(_good_payload(task.task_id), chunk_count=10)
    client._client = stub
    client._openai = SimpleNamespace(APIError=_StreamingStubOpenAI.APIError)

    received: list[str] = []
    plan = plan_with_llm(
        task, snapshot, client=client, on_delta=received.append
    )

    assert plan.task_id == task.task_id
    assert len(plan.actions) == 4
    # We should have gotten multiple deltas (proves streaming, not one-shot).
    assert len(received) >= 5, f"expected >=5 deltas, got {len(received)}"
    # The reassembled deltas must be valid JSON matching the plan.
    reassembled = json.loads("".join(received))
    assert reassembled["task_id"] == task.task_id
    # Streaming-enabled call must have stream=True in the request.
    assert stub.recorded_calls[0]["stream"] is True
