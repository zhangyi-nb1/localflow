"""agent — the v0.9.0 default LLM-driven meta-skill.

Where folder_organizer / pdf_indexer / data_reporter / data_analyzer /
workspace_visualizer are single-purpose specialists, ``agent`` is the
opposite: it accepts a compound user goal ("organize my workspace,
then summarize each category, then chart file counts as a PNG")
and produces a SINGLE ActionPlan that covers the whole thing in one
dry-run / approval / execute / verify / rollback cycle.

How it works:
  1. The LLM gets an extended system prompt that teaches it about
     four capabilities (move, rename, write_md, chart) and the
     ``chart_request`` metadata convention.
  2. The LLM emits a plan whose chart actions carry
     ``metadata.chart_request = {kind, title, xlabel, counts}``.
  3. A pure-Python post-processor walks the returned plan, renders
     each ``chart_request`` to PNG bytes via ``chart_ops.bar_png``,
     and substitutes them as ``metadata.binary_content_b64``. The
     harness then sees a plan it can dry-run + execute like any other.

The skill is intentionally "the only one the UI exposes by default" —
specialist skills remain in the registry for CLI / MCP use, but the
v0.9.0 Plan page always routes through here. The user described the
old multi-skill selector as "我觉得很蠢"; this skill is the answer.
"""

from app.skills.agent.llm_planner import (
    AGENT_SYSTEM_PROMPT,
    render_chart_actions,
)
from app.skills.agent.planner import plan_agent_fallback
from app.skills.agent.reporter import render_final_report
from app.skills.agent.skill import AgentSkill
from app.skills.agent.validator import (
    AgentValidationError,
    validate_agent_plan,
)

__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "AgentSkill",
    "AgentValidationError",
    "plan_agent_fallback",
    "render_chart_actions",
    "render_final_report",
    "validate_agent_plan",
]
