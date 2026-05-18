"""v0.14.1 — topic_clusterer skill.

Companion to folder_organizer: where folder_organizer groups files
by extension (papers/, data/, images/), topic_clusterer groups them
by SEMANTIC TOPIC (topics/transformers/, topics/rag_eval/, etc.).
Requires the LLM planner — the rule path is a no-op fallback.
"""

from app.skills.topic_clusterer.skill import TopicClustererSkill

__all__ = ["TopicClustererSkill"]
