"""v0.16 — WebCollectSkill.

Takes a list of URLs (via ``task.preferences["urls"]``) and produces
one FETCH action per URL. The skill itself doesn't touch the network
— the executor does, gated by policy_guard's fetch_allowed_domains
check. The skill's only job is producing well-formed FETCH actions
with sensible target paths.

URL → target path mapping: strips the URL down to its hostname +
last path segment, slugified to ASCII. For example::

    https://example.com/docs/foo.md → fetched/example-com/foo.md
    https://api.openai.com/v1/usage → fetched/api-openai-com/usage

When the URL has no useful path segment, falls back to
``index.html``. Users who want different paths should write a custom
skill.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlparse

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.action import Action, ActionType, RiskLevel
from app.skills._base import Skill, SkillError

FETCH_ROOT = "fetched"


class WebCollectSkill(Skill):
    """Stage 1 of an internet-aware pipeline: pull a small allowlist
    of URLs into the workspace as files. Subsequent stages (PDF
    indexer, data analyzer, etc.) operate on the fetched files
    exactly the same as on user-seeded ones."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="webcollect",
            description=(
                "Fetch a small list of HTTPS URLs into the workspace. "
                "Domain allowlist enforced by policy_guard. v0.16's 2nd "
                "§10.7 exception — adds ActionType.FETCH to the kernel."
            ),
            version="0.1.0",
            capabilities=["http_fetch", "url_to_workspace"],
            required_tools=[],
            allowed_actions=["mkdir", "fetch", "index"],
            requires_approval=["mkdir", "fetch"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        urls = task.preferences.get("urls") if task.preferences else None
        if not isinstance(urls, list) or not urls:
            return ActionPlan(
                plan_id=f"plan-{uuid.uuid4().hex[:8]}",
                task_id=task.task_id,
                summary=(
                    "webcollect: no URLs supplied (set task.preferences['urls'] "
                    "to a list of HTTPS URLs). Skipping."
                ),
                actions=[],
                expected_outputs=[],
                risk_summary="zero risk — no actions emitted",
            )

        actions: list[Action] = []
        targets: list[str] = []
        counter = 0

        def next_id() -> str:
            nonlocal counter
            counter += 1
            return f"a-{counter:03d}"

        # mkdir for fetched/ + per-host subdirs.
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.MKDIR,
                target_path=FETCH_ROOT,
                reason=f"Create root for fetched files ({FETCH_ROOT}/)",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            )
        )
        seen_hosts: set[str] = set()
        for url in urls:
            target = _url_to_target_path(url)
            host_dir = target.rsplit("/", 1)[0]
            if host_dir not in seen_hosts:
                seen_hosts.add(host_dir)
                actions.append(
                    Action(
                        action_id=next_id(),
                        action_type=ActionType.MKDIR,
                        target_path=host_dir,
                        reason=f"Create per-host dir for {host_dir}",
                        risk_level=RiskLevel.LOW,
                        reversible=True,
                        requires_approval=True,
                    )
                )
            actions.append(
                Action(
                    action_id=next_id(),
                    action_type=ActionType.FETCH,
                    target_path=target,
                    reason=f"HTTPS GET {url}",
                    risk_level=RiskLevel.MEDIUM,
                    reversible=True,
                    requires_approval=True,
                    metadata={"url": url},
                )
            )
            targets.append(target)

        return ActionPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            task_id=task.task_id,
            summary=(
                f"Fetch {len(urls)} URL(s) into {FETCH_ROOT}/. "
                f"policy_guard enforces the fetch_allowed_domains allowlist."
            ),
            actions=actions,
            expected_outputs=targets,
            risk_summary=(
                "Medium risk: network IO is reversible (rollback deletes "
                "the downloaded files) but adds external dependency."
            ),
        )

    def plan_with_llm(self, task, snapshot, **kwargs) -> ActionPlan:
        # No LLM path — URLs are user-supplied input, no semantic
        # decisions for the model to make.
        raise SkillError(
            "webcollect doesn't support --planner llm. Supply URLs via "
            "task.preferences['urls'] and use --planner rule."
        )

    def validate(self, plan: ActionPlan) -> None:
        for action in plan.actions:
            if action.action_type != ActionType.FETCH:
                continue
            url = action.metadata.get("url") if action.metadata else None
            if not isinstance(url, str) or not url.startswith("https://"):
                raise SkillError(
                    f"action {action.action_id}: FETCH requires metadata.url over HTTPS"
                )

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome: Any,
        verification: VerificationResult,
    ) -> str:
        fetched = [a for a in plan.actions if a.action_type == ActionType.FETCH]
        lines = [
            "# WebCollect report",
            "",
            f"_Task `{task.task_id}` — {len(fetched)} URL(s) fetched._",
            "",
        ]
        for a in fetched:
            url = (a.metadata or {}).get("url", "?")
            lines.append(f"- `{a.target_path}` ← {url}")
        lines.append("")
        lines.append("**Verifier:** " + ("✓ passed" if verification.passed else "✗ failed"))
        return "\n".join(lines)


def _url_to_target_path(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "unknown").replace(".", "-").lower()
    raw_name = parsed.path.rsplit("/", 1)[-1] or "index.html"
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw_name).strip("._-") or "index.html"
    return f"{FETCH_ROOT}/{host}/{name}"
