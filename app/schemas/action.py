from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    MKDIR = "mkdir"
    COPY = "copy"
    MOVE = "move"
    RENAME = "rename"
    INDEX = "index"
    SUMMARIZE = "summarize"
    CONVERT = "convert"
    ANALYZE = "analyze"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


WRITE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.MKDIR,
        ActionType.COPY,
        ActionType.MOVE,
        ActionType.RENAME,
        ActionType.INDEX,
        ActionType.CONVERT,
    }
)


class Action(BaseModel):
    action_id: str
    action_type: ActionType
    source_path: str | None = None
    target_path: str | None = None
    reason: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    requires_approval: bool = False
    confidence: float | None = None
    metadata: dict = Field(default_factory=dict)

    def is_write(self) -> bool:
        return self.action_type in WRITE_ACTIONS
