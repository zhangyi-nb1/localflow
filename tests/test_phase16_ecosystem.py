"""v0.16.0 — Phase 16 ecosystem-tier tests.

Covers all four Sprint 3 items:
- Skill manifest signing (HMAC-SHA256 + loader gating).
- Per-skill LLM tool schema capability scoping.
- WebCollect skill + ActionType.FETCH + policy_guard FETCH gating.
- MCP catalog (external server registry).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

from app.agent.prompts import build_action_plan_tool_schema
from app.harness.policy_guard import evaluate_action
from app.mcp.catalog import add_entry, remove_entry
from app.mcp.catalog import load as load_catalog
from app.mcp.catalog import save as save_catalog
from app.schemas import ActionType, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, RiskLevel
from app.skills.signing import (
    SIGNATURE_FILENAME,
    SIGNED_FILES,
    compute_signature,
    signing_required,
    verify_signature,
    write_signature,
)
from app.skills.webcollect import WebCollectSkill

# ─────────────────────────────────── skill signing


def _scaffold_external_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.py").write_text(
        textwrap.dedent(
            """\
            from app.schemas import ActionPlan, SkillManifest, TaskSpec
            from app.skills._base import Skill

            class FakeSkill(Skill):
                @property
                def manifest(self):
                    return SkillManifest(
                        name="fake",
                        description="external scaffold for tests",
                        version="0.0.1",
                        capabilities=[],
                        required_tools=[],
                        allowed_actions=["mkdir"],
                        requires_approval=["mkdir"],
                        supports_dry_run=True,
                        supports_rollback=True,
                        supports_verify=True,
                    )

                def plan(self, task, snapshot):
                    return ActionPlan(
                        plan_id="p", task_id=task.task_id, summary="x",
                        actions=[], expected_outputs=[], risk_summary="ok",
                    )

                def validate(self, plan): return None
                def report(self, **kwargs): return ""
            """
        ),
        encoding="utf-8",
    )
    (skill_dir / "skill.yaml").write_text("name: fake\nversion: 0.0.1\n", encoding="utf-8")


def test_signing_required_reads_env() -> None:
    """Env var truthy values flip the require flag."""
    with patch.dict(os.environ, {"LOCALFLOW_REQUIRE_SIGNED_SKILLS": "0"}, clear=False):
        assert signing_required() is False
    with patch.dict(os.environ, {"LOCALFLOW_REQUIRE_SIGNED_SKILLS": "1"}, clear=False):
        assert signing_required() is True


def test_compute_signature_round_trips(tmp_path: Path) -> None:
    """Signing + verifying with the same key returns True."""
    skill_dir = tmp_path / "myskill"
    _scaffold_external_skill(skill_dir)
    key = b"test-secret-key"
    digest = write_signature(skill_dir, key)
    assert (skill_dir / SIGNATURE_FILENAME).exists()
    assert len(digest) == 64  # SHA-256 hex
    assert verify_signature(skill_dir, key) is True


def test_verify_fails_after_tamper(tmp_path: Path) -> None:
    """Modifying skill.py after signing invalidates the signature."""
    skill_dir = tmp_path / "myskill"
    _scaffold_external_skill(skill_dir)
    key = b"abc"
    write_signature(skill_dir, key)
    (skill_dir / "skill.py").write_text("# tampered\n", encoding="utf-8")
    assert verify_signature(skill_dir, key) is False


def test_compute_signature_stable_per_signed_files(tmp_path: Path) -> None:
    """SIGNED_FILES enumerates the signed payload exactly. Adding a
    helper file in the skill dir doesn't change the digest (good — it
    means refactoring helpers doesn't break the signature)."""
    skill_dir = tmp_path / "myskill"
    _scaffold_external_skill(skill_dir)
    key = b"k"
    digest_before = compute_signature(skill_dir, key)
    (skill_dir / "helpers.py").write_text("# unsigned helper\n", encoding="utf-8")
    digest_after = compute_signature(skill_dir, key)
    assert digest_before == digest_after
    assert SIGNED_FILES == ("skill.py", "skill.yaml")


# ─────────────────────────────────── tool schema scoping


def test_tool_schema_scopes_action_type_enum() -> None:
    """When an allowed list is provided, the enum is restricted to it."""
    schema = build_action_plan_tool_schema(allowed_action_types=["mkdir", "move"])
    enum = schema["properties"]["actions"]["items"]["properties"]["action_type"]["enum"]
    assert sorted(enum) == ["mkdir", "move"]


def test_tool_schema_default_is_full_set() -> None:
    """Back-compat: None or empty → full default action_type set."""
    schema = build_action_plan_tool_schema(None)
    enum = schema["properties"]["actions"]["items"]["properties"]["action_type"]["enum"]
    assert set(enum) == {"mkdir", "copy", "move", "rename", "index", "summarize"}


# ─────────────────────────────────── FETCH policy_guard gating


def test_fetch_action_requires_https() -> None:
    """A FETCH action with http:// URL is blocked by policy_guard."""
    action = Action(
        action_id="a-001",
        action_type=ActionType.FETCH,
        target_path="out.txt",
        reason="t",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"url": "http://example.com/x"},
    )
    decision = evaluate_action(
        Path("/tmp"),
        action,
        fetch_allowed_domains=("example.com",),
    )
    assert decision.allowed is False
    assert any("https" in r for r in decision.reasons)


def test_fetch_host_must_be_on_allowlist() -> None:
    """Empty allowlist → all FETCH actions blocked."""
    action = Action(
        action_id="a-001",
        action_type=ActionType.FETCH,
        target_path="out.txt",
        reason="t",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"url": "https://example.com/x"},
    )
    decision = evaluate_action(Path("/tmp"), action, fetch_allowed_domains=())
    assert decision.allowed is False
    assert any("not in fetch_allowed_domains" in r for r in decision.reasons)


def test_fetch_with_allowlisted_host_passes() -> None:
    """Host on the allowlist → policy_guard allows the action."""
    action = Action(
        action_id="a-001",
        action_type=ActionType.FETCH,
        target_path="out.txt",
        reason="t",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"url": "https://example.com/x"},
    )
    decision = evaluate_action(
        Path("/tmp"),
        action,
        fetch_allowed_domains=("example.com",),
    )
    assert decision.allowed is True


# ─────────────────────────────────── WebCollect skill


def test_webcollect_plans_fetch_per_url() -> None:
    """The skill emits one FETCH per URL in task.preferences['urls']."""
    skill = WebCollectSkill()
    task = TaskSpec(
        task_id="t",
        user_goal="fetch",
        workspace_root="/tmp/ws",
        skill="webcollect",
        constraints=[],
        allowed_actions=["mkdir", "fetch", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={"urls": ["https://example.com/a.md", "https://api.openai.com/b.json"]},
    )
    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t",
        root="/tmp/ws",
        files=[],
        total_files=0,
        total_size_bytes=0,
    )
    plan = skill.plan(task, snap)
    fetch_actions = [a for a in plan.actions if a.action_type == ActionType.FETCH]
    assert len(fetch_actions) == 2
    targets = {a.target_path for a in fetch_actions}
    assert "fetched/example-com/a.md" in targets
    assert "fetched/api-openai-com/b.json" in targets


def test_webcollect_with_no_urls_is_noop() -> None:
    """No URLs → empty plan with explanatory summary, no actions."""
    skill = WebCollectSkill()
    task = TaskSpec(
        task_id="t",
        user_goal="fetch",
        workspace_root="/tmp/ws",
        skill="webcollect",
        constraints=[],
        allowed_actions=["mkdir", "fetch", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )
    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t",
        root="/tmp/ws",
        files=[],
        total_files=0,
        total_size_bytes=0,
    )
    plan = skill.plan(task, snap)
    assert plan.actions == []
    assert "no URLs" in plan.summary


def test_webcollect_registered_in_default_registry() -> None:
    from app.skills import get_default_registry

    assert "webcollect" in get_default_registry().list_names()


# ─────────────────────────────────── MCP catalog


def test_catalog_round_trip(tmp_path: Path) -> None:
    """add_entry → save → load preserves names + commands."""
    cat = load_catalog(home=tmp_path)
    assert cat.entries == []
    add_entry(cat, "fs", "npx @modelcontextprotocol/server-filesystem /tmp")
    add_entry(cat, "fetch", "uvx mcp-server-fetch")
    save_catalog(cat, home=tmp_path)

    reloaded = load_catalog(home=tmp_path)
    names = {e.name for e in reloaded.entries}
    assert names == {"fs", "fetch"}


def test_catalog_remove_entry(tmp_path: Path) -> None:
    cat = load_catalog(home=tmp_path)
    add_entry(cat, "fs", "cmd")
    save_catalog(cat, home=tmp_path)
    cat = load_catalog(home=tmp_path)
    assert remove_entry(cat, "fs") is True
    save_catalog(cat, home=tmp_path)
    cat = load_catalog(home=tmp_path)
    assert cat.entries == []


def test_catalog_remove_unknown_returns_false(tmp_path: Path) -> None:
    cat = load_catalog(home=tmp_path)
    assert remove_entry(cat, "ghost") is False
