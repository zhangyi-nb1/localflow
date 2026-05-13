from __future__ import annotations

from app.schemas import ExecutionStatus
from app.storage.jsonl_logger import JsonlLogger


def completed_action_ids(execution_log: JsonlLogger) -> set[str]:
    """Replay the execution log to find which actions already succeeded.

    Used by the executor at startup to skip work that's already done — the
    "resume from checkpoint" capability.
    """
    done: set[str] = set()
    for record in execution_log.read_all():
        payload = record.get("payload", {})
        if (
            record.get("event") == "action.end"
            and payload.get("status") == ExecutionStatus.SUCCESS.value
        ):
            aid = payload.get("action_id")
            if aid:
                done.add(aid)
    return done
