"""workspace_visualizer skill — render a file-counts bar chart (PNG).

Phase 8.2 / v0.8.2. Built to close the v0.8.1 gap where users asked
"draw a bar chart of file counts" and the auto-detect routed them to
``data_reporter`` (which only emits markdown). This skill calls
``chart_ops.bar_png`` directly to produce a real PNG written via the
binary-content INDEX action, the same mechanism ``data_analyzer``
uses for column charts.
"""

from app.skills.workspace_visualizer.planner import plan_workspace_visualization
from app.skills.workspace_visualizer.reporter import render_final_report
from app.skills.workspace_visualizer.skill import WorkspaceVisualizerSkill
from app.skills.workspace_visualizer.validator import (
    WorkspaceVisualizerValidationError,
    validate_workspace_visualizer_plan,
)

__all__ = [
    "WorkspaceVisualizerSkill",
    "WorkspaceVisualizerValidationError",
    "plan_workspace_visualization",
    "render_final_report",
    "validate_workspace_visualizer_plan",
]
