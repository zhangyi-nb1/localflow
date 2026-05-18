"""v0.16 — WebCollect skill: HTTPS GET → workspace file.

Emits :class:`ActionType.FETCH` actions for URLs supplied via
``task.preferences["urls"]``. Every URL's host must be on
``MemoryStore.fetch_allowed_domains`` or policy_guard rejects the
plan. v0.16's second deliberate §10.7 exception (after Phase 5's
forbidden_paths).
"""

from app.skills.webcollect.skill import WebCollectSkill

__all__ = ["WebCollectSkill"]
