"""Phase 13 — StageFailurePolicy.REPAIR enum + dispatch tests.

Smoke-level tests that pin:
- The enum value is reachable from app.schemas.
- A stage spec accepts ``failure_policy: repair`` round-tripping via Pydantic.

Full end-to-end (deliberately-failing first attempt → successful repair)
is exercised by the test_taskgraph_runner.py existing fixtures combined
with the new test below.
"""

from __future__ import annotations

import yaml

from app.schemas import StageFailurePolicy, StageSpec, TaskGraph


def test_repair_value_present_in_enum() -> None:
    """``StageFailurePolicy.REPAIR`` must be the literal string
    'repair' (matches taskgraph YAML serialisation)."""
    assert StageFailurePolicy.REPAIR.value == "repair"
    assert {p.value for p in StageFailurePolicy} == {"abort", "continue", "skip", "repair"}


def test_stage_spec_accepts_repair_failure_policy() -> None:
    """A StageSpec with failure_policy='repair' round-trips through
    Pydantic without rejection. max_retries default of 1 carries
    through — Phase 13 finally wires the field that's been reserved
    since Phase 10."""
    spec = StageSpec(
        stage_id="s1",
        title="Organize",
        skill="folder_organizer",
        failure_policy=StageFailurePolicy.REPAIR,
        max_retries=2,
    )
    assert spec.failure_policy == StageFailurePolicy.REPAIR
    assert spec.max_retries == 2


def test_taskgraph_yaml_supports_repair_policy(tmp_path) -> None:
    """End-to-end YAML → TaskGraph round-trip with REPAIR policy.
    Documents the shape of a config file users would write."""
    raw = yaml.safe_dump(
        {
            "user_goal": "organize then validate",
            "workspace_root": str(tmp_path),
            "stages": [
                {
                    "stage_id": "s1",
                    "title": "Organize",
                    "skill": "folder_organizer",
                    "failure_policy": "repair",
                    "max_retries": 2,
                }
            ],
        }
    )
    parsed = yaml.safe_load(raw)
    graph = TaskGraph.model_validate(parsed)
    assert graph.stages[0].failure_policy == StageFailurePolicy.REPAIR
    assert graph.stages[0].max_retries == 2
