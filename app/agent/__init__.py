"""LLM Planner module (Phase 1).

Public surface:
    LLMClient            — provider-agnostic Protocol
    AnthropicClient      — concrete impl backed by claude-opus-4-7
    OpenAIClient         — concrete impl backed by gpt-5 (configurable)
    FakeLLMClient        — deterministic test double
    LLMPlanner           — wraps a client + runs the repair loop
    plan_with_llm        — drop-in replacement for the rule-based planner
    PlannerFailure       — raised when max repair attempts are exhausted
"""
from app.agent.client import AnthropicClient, FakeLLMClient, LLMClient, LLMClientError
from app.agent.openai_client import OpenAIClient
from app.agent.planner import LLMPlanner, PlannerFailure, plan_with_llm

__all__ = [
    "AnthropicClient",
    "FakeLLMClient",
    "LLMClient",
    "LLMClientError",
    "LLMPlanner",
    "OpenAIClient",
    "PlannerFailure",
    "plan_with_llm",
]
