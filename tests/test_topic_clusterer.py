"""v0.14.1 — topic_clusterer skill tests.

LLM-free: stub the LLM client by patching ``_default_client`` so the
test deterministically returns a known assignments payload. Covers
the planner's slugifying, max-topics cap, and ActionPlan shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent.client import StructuredResponse
from app.schemas import FileMeta, TaskSpec, WorkspaceSnapshot
from app.skills.topic_clusterer.llm_planner import (
    TOPICS_ROOT,
    _slugify,
    plan_topic_clustering,
)


def test_slugify_handles_chinese_and_spaces() -> None:
    assert _slugify("Transformer architectures") == "transformer-architectures"
    assert _slugify("RAG eval / 评估") == "rag-eval"
    assert _slugify("") == "misc"
    assert _slugify("!!!") == "misc"


def _snap(*items: tuple[str, str, str]) -> WorkspaceSnapshot:
    """items: (path, file_type, text_preview)."""
    files = [
        FileMeta(
            path=path,
            file_type=ftype,
            size_bytes=10,
            modified_at=datetime.now(timezone.utc),
            text_preview=preview,
        )
        for path, ftype, preview in items
    ]
    return WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root="/tmp/ws",
        files=files,
        total_files=len(files),
        total_size_bytes=10 * len(files),
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="t-1",
        user_goal="cluster",
        workspace_root="/tmp/ws",
        skill="topic_clusterer",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )


class _StubClient:
    """LLMClient stand-in. Returns a fixed assignments payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def generate_structured(self, **_kwargs) -> StructuredResponse:
        return StructuredResponse(
            payload=self._payload,
            raw_assistant_content=[],
            tool_use_id="stub-1",
            usage={},
        )


def test_clustering_groups_files_and_emits_per_topic_actions() -> None:
    snap = _snap(
        ("attention.pdf", "pdf", "transformer self attention"),
        ("memory.pdf", "pdf", "agent memory architectures"),
        ("rag_eval.pdf", "pdf", "retrieval evaluation benchmark"),
    )
    client = _StubClient(
        {
            "assignments": [
                {"path": "attention.pdf", "topic": "transformers"},
                {"path": "memory.pdf", "topic": "agent-memory"},
                {"path": "rag_eval.pdf", "topic": "rag-eval"},
            ]
        }
    )
    plan = plan_topic_clustering(_task(), snap, client=client)

    # 3 mkdir + 3 move + 3 index = 9 actions
    assert len(plan.actions) == 9
    move_targets = {
        a.source_path: a.target_path for a in plan.actions if a.action_type.value == "move"
    }
    assert move_targets["attention.pdf"] == f"{TOPICS_ROOT}/transformers/attention.pdf"
    assert move_targets["memory.pdf"] == f"{TOPICS_ROOT}/agent-memory/memory.pdf"
    indexes = {a.target_path for a in plan.actions if a.action_type.value == "index"}
    assert f"{TOPICS_ROOT}/transformers/index.md" in indexes


def test_clustering_skips_files_without_text_preview() -> None:
    """A file with no text_preview can't be clustered (LLM has no
    content to classify). Skip silently — no actions for it."""
    snap = _snap(
        ("paper.pdf", "pdf", "content here"),
        ("opaque.bin", "other", ""),  # empty preview
    )
    client = _StubClient({"assignments": [{"path": "paper.pdf", "topic": "general"}]})
    plan = plan_topic_clustering(_task(), snap, client=client)
    sources = {a.source_path for a in plan.actions if a.action_type.value == "move"}
    assert sources == {"paper.pdf"}
    # opaque.bin was not in the prompt → no move emitted.


def test_clustering_returns_noop_when_no_files_have_previews() -> None:
    """Edge case: every file is binary/unreadable → zero-action plan."""
    snap = _snap(("a.bin", "other", ""), ("b.dat", "other", ""))
    plan = plan_topic_clustering(_task(), snap, client=_StubClient({"assignments": []}))
    assert plan.actions == []
    assert "Nothing to do" in plan.summary or "nothing to do" in plan.summary


def test_llm_hallucinated_paths_are_dropped() -> None:
    """If the LLM returns an assignment for a path that wasn't in the
    snapshot, the planner silently drops it rather than emitting a
    move for a non-existent source."""
    snap = _snap(("real.pdf", "pdf", "real preview"))
    client = _StubClient(
        {
            "assignments": [
                {"path": "real.pdf", "topic": "good"},
                {"path": "ghost.pdf", "topic": "hallucinated"},
            ]
        }
    )
    plan = plan_topic_clustering(_task(), snap, client=client)
    sources = {a.source_path for a in plan.actions if a.action_type.value == "move"}
    assert sources == {"real.pdf"}


def test_topic_clusterer_registered_in_default_registry() -> None:
    """Smoke check: skill registration succeeded at import time."""
    from app.skills import get_default_registry

    assert "topic_clusterer" in get_default_registry().list_names()


def test_topic_clusterer_supports_llm() -> None:
    """Skill correctly advertises LLM support so v0.12's autodetect +
    revise loop work against it."""
    from app.skills import get_default_registry

    skill = get_default_registry().require("topic_clusterer")
    assert skill.supports_llm()
