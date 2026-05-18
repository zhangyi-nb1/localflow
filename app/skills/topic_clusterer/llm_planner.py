"""v0.14.1 — LLM-driven topic clustering.

Single-turn LLM call with a strict tool schema. The model receives a
list of files with their text_previews and returns a list of
``{path, topic}`` assignments. We group by topic, slugify the labels,
and emit mkdir + move + index.md actions.

Topic labels are slugified to filesystem-safe kebab-case so any LLM
output (Chinese, spaces, special chars) maps to a usable directory
name.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from app.agent.client import LLMClient, LLMClientError
from app.agent.planner import _default_client
from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel

TOPICS_ROOT = "topics"
MAX_TOPICS = 8
"""Hard cap so the LLM can't explode the workspace into 50 single-file dirs."""

MAX_PREVIEW_CHARS = 400
"""How much of each file's text_preview to send to the LLM. Keeps the
prompt bounded; the LLM doesn't need the whole content to classify."""

SYSTEM_PROMPT = (
    "You are a strict, terse topic-clustering grader for a research-pack "
    "agent. You are given a list of files with content previews. Group "
    "them into 2-8 semantic topics. Each file gets exactly ONE topic. "
    "Topic labels MUST be 1-3 English words, lowercase, kebab-case "
    "(e.g. 'transformers', 'rag-eval', 'agent-memory'). Submit the "
    "assignments via the submit_clustering tool."
)

TOOL_NAME = "submit_clustering"
TOOL_DESCRIPTION = (
    "Submit topic assignments for every input file. Each entry must be "
    "{path: <workspace-relative path>, topic: <kebab-case label>}."
)

TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": ["path", "topic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assignments"],
    "additionalProperties": False,
}


def plan_topic_clustering(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    client: LLMClient | None = None,
    **_kwargs,
) -> ActionPlan:
    """Drive one LLM call to assign topics, then build the ActionPlan.

    No internal repair loop — if the LLM returns garbage, the
    standard skill.validate + policy_guard catch it and the user can
    re-run with a manual ``--hint`` via Phase 11's revise flow.
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"

    # 1. Pick which files to cluster — anything with a non-empty text_preview.
    files_to_cluster = [f for f in snapshot.files if (f.text_preview or "").strip()]
    if not files_to_cluster:
        return ActionPlan(
            plan_id=plan_id,
            task_id=task.task_id,
            summary="No files with text_preview to cluster — nothing to do.",
            actions=[],
            expected_outputs=[],
            risk_summary="zero risk — no actions emitted",
        )

    # 2. Call the LLM.
    if client is None:
        client = _default_client()
    user_prompt = _build_user_prompt(task, files_to_cluster)
    try:
        response = client.generate_structured(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tool_name=TOOL_NAME,
            tool_description=TOOL_DESCRIPTION,
            tool_schema=TOOL_SCHEMA,
        )
    except LLMClientError as exc:
        return ActionPlan(
            plan_id=plan_id,
            task_id=task.task_id,
            summary=f"LLM call failed: {exc}",
            actions=[],
            expected_outputs=[],
            risk_summary="zero risk — no actions emitted",
        )

    raw_assignments = (response.payload or {}).get("assignments") or []
    # 3. Validate + slugify + group.
    files_by_path = {f.path: f for f in files_to_cluster}
    by_topic: dict[str, list[str]] = defaultdict(list)
    for entry in raw_assignments:
        path = str(entry.get("path", "")).strip()
        topic = _slugify(str(entry.get("topic", "")).strip())
        if not path or not topic:
            continue
        if path not in files_by_path:
            continue  # LLM hallucinated a path; skip
        by_topic[topic].append(path)

    # Enforce MAX_TOPICS — merge the smallest topics into 'misc-topic'.
    while len(by_topic) > MAX_TOPICS:
        smallest = min(by_topic, key=lambda k: len(by_topic[k]))
        merged = by_topic.pop(smallest)
        by_topic["misc"].extend(merged)

    # 4. Build the ActionPlan.
    actions: list[Action] = []
    expected_outputs: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"a-{counter:03d}"

    # mkdir per topic (parent topics/ created implicitly by executor).
    for topic in sorted(by_topic):
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.MKDIR,
                target_path=f"{TOPICS_ROOT}/{topic}",
                reason=f"Create topic dir for {topic}",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            )
        )

    # moves
    for topic in sorted(by_topic):
        for path in sorted(by_topic[topic]):
            basename = PurePosixPath(path).name
            target = f"{TOPICS_ROOT}/{topic}/{basename}"
            if path == target:
                continue
            actions.append(
                Action(
                    action_id=next_id(),
                    action_type=ActionType.MOVE,
                    source_path=path,
                    target_path=target,
                    reason=f"Group under topic '{topic}'",
                    risk_level=RiskLevel.MEDIUM,
                    reversible=True,
                    requires_approval=True,
                )
            )

    # index.md per topic
    for topic in sorted(by_topic):
        members = sorted({PurePosixPath(p).name for p in by_topic[topic]})
        index_rel = f"{TOPICS_ROOT}/{topic}/index.md"
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.INDEX,
                target_path=index_rel,
                reason=f"Generate topic index for {topic}/",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={
                    "content": _render_topic_index(topic, members),
                    "overwrite_existing": True,
                },
            )
        )
        expected_outputs.append(index_rel)

    summary = (
        f"Cluster {len(files_to_cluster)} file(s) into {len(by_topic)} "
        f"semantic topic(s) under {TOPICS_ROOT}/."
    )
    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=summary,
        actions=actions,
        expected_outputs=expected_outputs,
        risk_summary="Medium risk: moves are reversible. Topic labels come from "
        "the LLM and are slugified — no shell, no delete.",
    )


# --------------------------------------------------------------------- helpers


def _build_user_prompt(task: TaskSpec, files) -> str:
    lines = [
        f"User goal: {task.user_goal or '(unspecified)'}",
        "",
        "Files to cluster (path + first lines of content):",
        "",
    ]
    for f in files:
        preview = (f.text_preview or "").strip().replace("\n", " ")[:MAX_PREVIEW_CHARS]
        lines.append(f"- `{f.path}` ({f.file_type}): {preview}")
    lines.append("")
    lines.append(
        f"Cluster into 2-{MAX_TOPICS} topics. Each file gets exactly ONE topic. "
        "Topic labels: kebab-case, 1-3 English words. Submit via submit_clustering."
    )
    return "\n".join(lines)


def _slugify(label: str) -> str:
    """Force any LLM-emitted label into kebab-case, ASCII-only.

    Empty / whitespace-only labels collapse to 'misc'.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", label.lower()).strip("-")
    return cleaned or "misc"


def _render_topic_index(topic: str, members: list[str]) -> str:
    lines = [
        f"# topics/{topic}/",
        "",
        f"_{len(members)} file(s) clustered under this topic._",
        "",
    ]
    for name in members:
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines)
