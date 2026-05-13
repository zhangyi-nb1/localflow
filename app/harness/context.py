from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOWED_ACTIONS: tuple[str, ...] = (
    "mkdir",
    "copy",
    "move",
    "rename",
    "index",
    "summarize",
)

DEFAULT_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "delete",
    "overwrite",
    "shell",
)


@dataclass
class HarnessContext:
    """Per-task execution envelope injected into every harness phase.

    Mirrors the JSON snippet in the design doc section 4.1.
    """

    task_id: str
    workspace_root: Path
    allowed_actions: tuple[str, ...] = DEFAULT_ALLOWED_ACTIONS
    forbidden_actions: tuple[str, ...] = DEFAULT_FORBIDDEN_ACTIONS
    risk_policy: str = "all write actions require dry_run and approval"
    task_status: str = "planning"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "workspace_root": str(self.workspace_root),
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "risk_policy": self.risk_policy,
            "task_status": self.task_status,
            **self.extra,
        }
