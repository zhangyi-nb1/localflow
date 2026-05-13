from __future__ import annotations

from pathlib import Path
from typing import Any

from app.storage.jsonl_logger import JsonlLogger


class AuditLogger:
    """High-level audit trail. Wraps a JsonlLogger and labels events."""

    def __init__(self, path: Path) -> None:
        self._logger = JsonlLogger(path)

    def log(self, event: str, **payload: Any) -> None:
        self._logger.write(event, payload)

    def read_all(self) -> list[dict]:
        return self._logger.read_all()
