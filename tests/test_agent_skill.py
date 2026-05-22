"""v0.9.0 — agent meta-skill tests.

Three layers:

  * **Registry + manifest** — agent is registered, has the right
    allowed_actions, supports LLM.
  * **Chart post-processor** — `chart_request` blocks render to PNG
    via `chart_ops.bar_png`; malformed specs degrade to markdown
    placeholders instead of crashing the plan.
  * **System prompt** — pin the document so accidental edits that
    drop the chart_request example or compound-goal instructions get
    caught in CI.

The LLM client itself is not exercised here — it requires a network
call. The agent skill's `plan_with_llm` is wrapper-tested via
`plan_with_llm_uses_agent_prompt` which monkeypatches the LLM
plumbing.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from app.agent import FakeLLMClient
from app.schemas import ActionPlan, FileMeta, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel
from app.skills import get_default_registry
from app.skills.agent import AGENT_SYSTEM_PROMPT, render_chart_actions
from app.skills.agent.validator import AgentValidationError, validate_agent_plan

# ───────────────────────────────────── registry + manifest


def test_agent_skill_registered() -> None:
    reg = get_default_registry()
    assert "agent" in reg.list_names()


def test_agent_manifest_allows_all_compound_actions() -> None:
    """A meta-skill must be allowed to emit every action category its
    LLM might produce — mkdir + move + rename + copy + index."""
    sk = get_default_registry().require("agent")
    for action in ("mkdir", "move", "rename", "copy", "index"):
        assert action in sk.manifest.allowed_actions, action


def test_agent_supports_llm() -> None:
    """The whole v0.9.0 design assumes LLM planning. Pin it so an
    accidental refactor doesn't quietly downgrade the skill."""
    sk = get_default_registry().require("agent")
    assert sk.supports_llm() is True


def test_agent_declares_chart_ops_required_tool() -> None:
    """The chart post-processor calls chart_ops.bar_png; the manifest
    must declare it so the registry's Tool-Registry validation passes."""
    sk = get_default_registry().require("agent")
    assert "chart_ops.bar_png" in sk.manifest.required_tools


# ───────────────────────────────────── rule fallback


def test_agent_rule_fallback_delegates_to_folder_organizer() -> None:
    """When the LLM is unavailable, the agent skill must still produce
    a usable plan via the folder_organizer rule planner. Marker: the
    summary string is prefixed `[agent rule-fallback]`."""
    from datetime import datetime, timezone

    from app.schemas import FileMeta, TaskSpec, WorkspaceSnapshot

    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t",
        root="/fake",
        files=[
            FileMeta(
                path="a.pdf",
                file_type="pdf",
                size_bytes=1,
                modified_at=datetime.now(timezone.utc),
            ),
            FileMeta(
                path="b.png",
                file_type="image",
                size_bytes=1,
                modified_at=datetime.now(timezone.utc),
            ),
        ],
        total_files=2,
        total_size_bytes=2,
    )
    task = TaskSpec(
        task_id="t",
        user_goal="anything",
        workspace_root="/fake",
        skill="agent",
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
    )
    sk = get_default_registry().require("agent")
    plan = sk.plan(task, snap)
    assert plan.actions, "rule fallback should produce at least one action"
    assert plan.summary.startswith("[agent rule-fallback]")


# ───────────────────────────────────── compound-goal coverage


def _compound_task(goal: str, task_id: str = "t-compound") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        user_goal=goal,
        workspace_root="/fake",
        skill="agent",
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )


def _compound_snapshot(task_id: str = "t-compound") -> WorkspaceSnapshot:
    now = datetime.now(timezone.utc)
    files = [
        FileMeta(path="paper.pdf", file_type="pdf", size_bytes=1, modified_at=now),
        FileMeta(path="note.txt", file_type="text", size_bytes=1, modified_at=now),
    ]
    return WorkspaceSnapshot(
        snapshot_id="snap-compound",
        task_id=task_id,
        root="/fake",
        files=files,
        total_files=len(files),
        total_size_bytes=2,
    )


def _compound_payload(
    task_id: str,
    *,
    include_summary: bool = True,
    include_chart: bool = True,
    chart_first: bool = False,
) -> dict:
    actions = [
        {
            "action_id": "a-001",
            "action_type": "mkdir",
            "target_path": "papers",
            "reason": "Create papers category.",
            "risk_level": "low",
            "reversible": True,
            "requires_approval": True,
        },
        {
            "action_id": "a-002",
            "action_type": "move",
            "source_path": "paper.pdf",
            "target_path": "papers/paper.pdf",
            "reason": "Organize PDF into papers.",
            "risk_level": "medium",
            "reversible": True,
            "requires_approval": True,
        },
    ]
    if include_summary:
        actions.append(
            {
                "action_id": "a-003",
                "action_type": "index",
                "target_path": "papers/index.md",
                "reason": "Summarize organized papers.",
                "risk_level": "low",
                "reversible": True,
                "requires_approval": False,
                "metadata": {"content": "# papers\n\n- paper.pdf\n"},
            }
        )
    if include_chart:
        chart_action = {
            "action_id": "a-004",
            "action_type": "index",
            "target_path": "charts/file_counts.png",
            "reason": "Chart post-organization file counts.",
            "risk_level": "low",
            "reversible": True,
            "requires_approval": False,
            "metadata": {
                "content": None,
                "chart_request": {
                    "kind": "bar",
                    "title": "Files per category",
                    "xlabel": "category",
                    "counts": [{"label": "papers", "value": 1}],
                },
                "overwrite_existing": True,
            },
        }
        if chart_first:
            actions.insert(0, chart_action)
        else:
            actions.append(chart_action)
    return {
        "plan_id": "plan-compound",
        "task_id": task_id,
        "summary": "Organize files, summarize results, and chart counts.",
        "risk_summary": "All writes are reversible.",
        "expected_outputs": ["papers/index.md", "charts/file_counts.png"],
        "actions": actions,
    }


def _last_repair_content(client: FakeLLMClient) -> str:
    messages = client.calls[-1]["messages"]
    content = messages[-1]["content"]
    assert isinstance(content, list)
    return str(content[0]["content"])


def test_agent_compound_chinese_missing_chart_repairs() -> None:
    task = _compound_task("整理文件，然后总结，最后绘制柱状图")
    snap = _compound_snapshot(task.task_id)
    client = FakeLLMClient(
        payloads=[
            _compound_payload(task.task_id, include_chart=False),
            _compound_payload(task.task_id),
        ]
    )

    plan = get_default_registry().require("agent").plan_with_llm(task, snap, client=client)

    assert len(client.calls) == 2
    assert "chart/visualization" in _last_repair_content(client)
    chart = plan.actions[-1]
    assert chart.target_path == "charts/file_counts.png"
    assert "binary_content_b64" in chart.metadata


def test_agent_compound_english_missing_chart_repairs() -> None:
    task = _compound_task("organize the files, then summarize them, finally chart counts")
    snap = _compound_snapshot(task.task_id)
    client = FakeLLMClient(
        payloads=[
            _compound_payload(task.task_id, include_chart=False),
            _compound_payload(task.task_id),
        ]
    )

    plan = get_default_registry().require("agent").plan_with_llm(task, snap, client=client)

    assert len(client.calls) == 2
    assert "chart/visualization" in _last_repair_content(client)
    assert any((a.target_path or "").endswith(".png") for a in plan.actions)


def test_agent_compound_missing_summary_feedback_is_specific() -> None:
    task = _compound_task("organize files, then summarize, then draw a chart")
    snap = _compound_snapshot(task.task_id)
    client = FakeLLMClient(
        payloads=[
            _compound_payload(task.task_id, include_summary=False),
            _compound_payload(task.task_id),
        ]
    )

    get_default_registry().require("agent").plan_with_llm(task, snap, client=client)

    feedback = _last_repair_content(client)
    assert "summary/index" in feedback
    assert "metadata.content" in feedback


def test_agent_simple_organize_does_not_require_summary_or_chart() -> None:
    task = _compound_task("organize files")
    snap = _compound_snapshot(task.task_id)
    payload = _compound_payload(task.task_id, include_summary=False, include_chart=False)
    client = FakeLLMClient(payloads=[payload])

    plan = get_default_registry().require("agent").plan_with_llm(task, snap, client=client)

    assert len(client.calls) == 1
    assert [a.action_type.value for a in plan.actions] == ["mkdir", "move"]


def test_agent_compound_chart_before_organize_repairs() -> None:
    task = _compound_task("organize, then summarize, finally chart the file counts")
    snap = _compound_snapshot(task.task_id)
    client = FakeLLMClient(
        payloads=[
            _compound_payload(task.task_id, chart_first=True),
            _compound_payload(task.task_id),
        ]
    )

    plan = get_default_registry().require("agent").plan_with_llm(task, snap, client=client)

    assert len(client.calls) == 2
    assert "after organization actions" in _last_repair_content(client)
    assert (plan.actions[-1].target_path or "").endswith(".png")


# ───────────────────────────────────── chart_request post-processor


def _plan_with_one_chart_action(chart_request: dict) -> ActionPlan:
    return ActionPlan(
        plan_id="p-test",
        task_id="t-test",
        summary="test",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="images/file_counts.png",
                reason="chart",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": None, "chart_request": chart_request},
            ),
        ],
    )


def test_chart_request_renders_real_png_bytes() -> None:
    """The post-processor must produce a base64-encoded PNG whose
    decoded bytes start with the standard 8-byte PNG signature. This
    is the only test that proves the agent emits a *real* image."""
    plan = _plan_with_one_chart_action(
        {
            "kind": "bar",
            "title": "Files per category",
            "xlabel": "category",
            "counts": {"papers": 3, "images": 4, "notes": 2},
        }
    )
    out = render_chart_actions(plan)
    action = out.actions[0]
    assert "binary_content_b64" in action.metadata
    decoded = base64.b64decode(action.metadata["binary_content_b64"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
    assert action.metadata.get("overwrite_existing") is True


def test_chart_request_accepts_array_form_counts() -> None:
    """OpenAI strict mode doesn't allow dicts with dynamic keys, so the
    LLM emits counts as ``[{"label": ..., "value": ...}, ...]``. The
    post-processor must accept that shape and still produce a real PNG."""
    plan = _plan_with_one_chart_action(
        {
            "kind": "bar",
            "title": "t",
            "xlabel": "x",
            "counts": [
                {"label": "papers", "value": 3},
                {"label": "images", "value": 4},
            ],
        }
    )
    out = render_chart_actions(plan)
    action = out.actions[0]
    assert "binary_content_b64" in action.metadata
    decoded = base64.b64decode(action.metadata["binary_content_b64"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_request_coerces_string_counts() -> None:
    """LLMs sometimes emit numeric values as strings ("3" instead of
    3). The post-processor must coerce rather than crash — otherwise
    we'd lose chart actions on every other LLM run."""
    plan = _plan_with_one_chart_action(
        {
            "kind": "bar",
            "title": "t",
            "xlabel": "x",
            "counts": {"a": "3", "b": "5"},
        }
    )
    out = render_chart_actions(plan)
    assert "binary_content_b64" in out.actions[0].metadata


def test_malformed_chart_falls_back_to_markdown() -> None:
    """A bad chart spec (empty counts, unsupported kind, etc.) must
    not crash the plan. The post-processor downgrades the action to a
    markdown error placeholder so the user sees what went wrong."""
    plan = _plan_with_one_chart_action(
        {
            "kind": "scatter",  # not implemented in v0.9.0
            "title": "t",
            "xlabel": "x",
            "counts": {"a": 1},
        }
    )
    out = render_chart_actions(plan)
    action = out.actions[0]
    assert action.target_path is not None
    assert action.target_path.endswith(".md")
    assert "binary_content_b64" not in action.metadata
    assert (
        "scatter" in action.metadata["content"].lower()
        or "kind" in action.metadata["content"].lower()
    )


def test_empty_counts_falls_back_to_markdown() -> None:
    plan = _plan_with_one_chart_action(
        {
            "kind": "bar",
            "title": "t",
            "xlabel": "x",
            "counts": {},
        }
    )
    out = render_chart_actions(plan)
    assert out.actions[0].target_path.endswith(".md")


def test_png_action_without_chart_request_downgrades_to_markdown() -> None:
    """v0.9.0 regression: the LLM sometimes emits a PNG-targeted
    INDEX action without a chart_request block (just `metadata.content:
    null`). The post-processor must defensively downgrade these to a
    markdown error placeholder — otherwise the validator catches them
    later with a less helpful message and the whole plan dies."""
    plan = ActionPlan(
        plan_id="p-test",
        task_id="t-test",
        summary="test",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="images/file_counts.png",
                reason="chart",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": None},  # No chart_request, no content
            ),
        ],
    )
    out = render_chart_actions(plan)
    action = out.actions[0]
    assert action.target_path is not None
    assert action.target_path.endswith(".md")
    assert "binary_content_b64" not in action.metadata
    assert action.metadata.get("content")


def test_non_chart_index_actions_are_left_alone() -> None:
    """Text INDEX actions (no chart_request) must pass through
    unchanged. The post-processor only touches chart_request blocks."""
    plan = ActionPlan(
        plan_id="p-test",
        task_id="t-test",
        summary="test",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="text",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "# hello"},
            ),
        ],
    )
    out = render_chart_actions(plan)
    assert out.actions[0].metadata["content"] == "# hello"
    assert "binary_content_b64" not in out.actions[0].metadata


# ───────────────────────────────────── validator


def test_validator_accepts_text_only_plan() -> None:
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "# hi"},
            ),
        ],
    )
    validate_agent_plan(plan)  # should not raise


def test_validator_rejects_png_without_binary_content() -> None:
    """The post-processor must run BEFORE the validator. A PNG action
    that still has chart_request but no binary_content_b64 means the
    post-processor failed and the executor would write a 0-byte file."""
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="chart.png",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"chart_request": {"kind": "bar", "counts": {"a": 1}}},
            ),
        ],
    )
    try:
        validate_agent_plan(plan)
    except AgentValidationError as exc:
        assert "binary_content_b64" in str(exc)
    else:
        raise AssertionError("validator should have rejected png-less binary")


def test_validator_rejects_empty_text_content() -> None:
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="s",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="report.md",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": ""},
            ),
        ],
    )
    try:
        validate_agent_plan(plan)
    except AgentValidationError as exc:
        assert "non-empty metadata.content" in str(exc)
    else:
        raise AssertionError("validator should have rejected empty content")


# ───────────────────────────────────── system prompt pin


def test_system_prompt_documents_chart_request_convention() -> None:
    """If a refactor accidentally drops the chart_request example from
    the prompt, LLMs lose the convention and the agent reverts to
    markdown-only output. Pin the critical strings."""
    p = AGENT_SYSTEM_PROMPT
    assert "chart_request" in p
    assert "binary_content_b64" in p  # mentioned as "do NOT emit"
    assert "bar_png" in p or '"kind": "bar"' in p
    assert "PNG" in p


def test_system_prompt_calls_out_compound_goals() -> None:
    """The whole v0.9.0 thesis is one-shot compound execution.
    The prompt must instruct the model to handle every step."""
    p = AGENT_SYSTEM_PROMPT
    assert "compound" in p.lower() or "Decompose" in p
    # Bilingual marker words — pinned so prompt translations don't
    # silently break compound detection guidance.
    assert "然后" in p or "compound" in p.lower()
    assert "coverage check" in p
    assert "summary/index" in p
    assert "chart actions after all organization actions" in p
