"""Phase 23.0 step 1 — pin the ComputeAction schema contract.

These tests fix the typed surface for the upcoming SandboxRuntime +
executor + verifier work. If a field moves or a constraint loosens,
these tests force a clear schema-bump decision (and a §10.7 ledger
update if anything kernel-facing shifts).

The schema is application-layer; no kernel imports here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    ActionType,
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    ComputeOutcome,
    ComputeOutcomeStatus,
    ProducedArtifact,
    RiskLevel,
    SandboxPolicy,
)


def _ref(rel: str = "data/raw.csv", size: int = 100) -> ComputeInputRef:
    return ComputeInputRef(rel_path=rel, size_bytes=size)


def _minimal_compute(**overrides) -> dict:
    base = {
        "script": "import sys\nprint('ok')\n",
        "script_summary": "Normalises the BOM/encoding of the input CSV.",
        "inputs": [_ref().model_dump()],
        "expected_outputs": [{"relative_path": "clean.csv", "description": "Normalised CSV."}],
    }
    base.update(overrides)
    return base


def test_python_compute_action_type_registered() -> None:
    """§10.7 3rd exception is named ``python_compute``. If this changes,
    the executor dispatch + docs/PHASE_23_PLAN.md ledger row must move
    together — bumping this test forces that conversation."""
    assert ActionType.PYTHON_COMPUTE.value == "python_compute"


def test_python_compute_is_not_in_write_actions() -> None:
    """ComputeAction writes only to scratch, never to the user workspace.
    Adding it to ``WRITE_ACTIONS`` would route it through workspace path
    validation that does not apply — design principle #10."""
    from app.schemas.action import WRITE_ACTIONS

    assert ActionType.PYTHON_COMPUTE not in WRITE_ACTIONS


def test_minimal_compute_action_validates() -> None:
    action = ComputeAction.model_validate(_minimal_compute())
    assert action.script.startswith("import sys")
    assert len(action.inputs) == 1
    assert len(action.expected_outputs) == 1
    # Phase 23 honesty: defaults are conservative.
    assert action.risk_level is RiskLevel.MEDIUM
    assert action.requires_approval is True
    assert action.sandbox_policy.timeout_sec == 30
    assert action.sandbox_policy.network_isolation == "best_effort"


def test_compute_action_requires_at_least_one_expected_output() -> None:
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(_minimal_compute(expected_outputs=[]))


def test_compute_action_requires_non_empty_script_and_summary() -> None:
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(_minimal_compute(script=""))
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(_minimal_compute(script_summary=""))


def test_compute_action_summary_capped_at_500_chars() -> None:
    # Approval UI assumes the headline is short — pin the cap so we
    # don't ship a 5KB summary into a confirmation dialog by accident.
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(_minimal_compute(script_summary="x" * 501))
    ok = ComputeAction.model_validate(_minimal_compute(script_summary="x" * 500))
    assert len(ok.script_summary) == 500


def test_compute_action_rejects_duplicate_output_paths() -> None:
    bad = _minimal_compute(
        expected_outputs=[
            {"relative_path": "clean.csv", "description": "first"},
            {"relative_path": "clean.csv", "description": "second"},
        ]
    )
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(bad)


def test_compute_action_rejects_duplicate_input_paths() -> None:
    bad = _minimal_compute(
        inputs=[_ref().model_dump(), _ref().model_dump()],
    )
    with pytest.raises(ValidationError):
        ComputeAction.model_validate(bad)


def test_compute_input_ref_rejects_path_escapes() -> None:
    """Inputs are workspace-relative pointers; the executor will resolve
    them under the user's workspace root. Path escapes are refused at
    the schema layer so the runtime never sees them."""
    for bad_path in ["../secrets.txt", "/etc/passwd", "a/../b.csv", ""]:
        with pytest.raises(ValidationError):
            ComputeInputRef.model_validate({"rel_path": bad_path})


def test_compute_input_ref_normalises_backslashes() -> None:
    ref = ComputeInputRef.model_validate({"rel_path": "sub\\nested\\raw.csv"})
    assert ref.rel_path == "sub/nested/raw.csv"


def test_artifact_spec_rejects_path_escapes() -> None:
    """Phase 10 principle #2: outputs land in scratch, period. The
    schema refuses ``..`` and absolute paths up-front so the runtime
    never sees a relative_path that could escape."""
    for bad_path in ["../out.csv", "/abs/out.csv", "a/../b.csv", ""]:
        with pytest.raises(ValidationError):
            ArtifactSpec.model_validate({"relative_path": bad_path, "description": "x"})


def test_artifact_spec_normalises_backslashes() -> None:
    spec = ArtifactSpec.model_validate(
        {"relative_path": "sub\\nested\\out.csv", "description": "x"}
    )
    assert spec.relative_path == "sub/nested/out.csv"


def test_sandbox_policy_clamps_timeout_to_hard_cap() -> None:
    # Hard 300s cap — long-running compute is out of scope for Phase 23.
    with pytest.raises(ValidationError):
        SandboxPolicy(timeout_sec=301)
    with pytest.raises(ValidationError):
        SandboxPolicy(timeout_sec=0)
    ok = SandboxPolicy(timeout_sec=300)
    assert ok.timeout_sec == 300


def test_sandbox_policy_rejects_invalid_env_passthrough_names() -> None:
    with pytest.raises(ValidationError):
        SandboxPolicy(env_passthrough=["1BAD"])
    with pytest.raises(ValidationError):
        SandboxPolicy(env_passthrough=["WITH-DASH"])
    with pytest.raises(ValidationError):
        SandboxPolicy(env_passthrough=[""])
    ok = SandboxPolicy(env_passthrough=["LANG", "PYTHONIOENCODING"])
    assert ok.env_passthrough == ["LANG", "PYTHONIOENCODING"]


def test_compute_outcome_round_trips_json() -> None:
    outcome = ComputeOutcome(
        status=ComputeOutcomeStatus.OK,
        exit_code=0,
        duration_sec=1.25,
        stdout_truncated="ok\n",
        stderr_truncated="",
        produced_artifacts=[
            ProducedArtifact(relative_path="clean.csv", size_bytes=42, sha256="ab" * 32)
        ],
    )
    payload = outcome.model_dump(mode="json")
    revived = ComputeOutcome.model_validate(payload)
    assert revived.status is ComputeOutcomeStatus.OK
    assert revived.produced_artifacts[0].relative_path == "clean.csv"
    assert revived.produced_artifacts[0].size_bytes == 42


def test_compute_outcome_status_values_are_closed() -> None:
    """The verifier + UI key off these strings; an open enum would let
    a stray status leak into the dry-run dialog without a schema bump."""
    expected = {
        "ok",
        "sandbox_timeout",
        "nonzero_exit",
        "output_missing",
        "output_over_size",
        "execution_error",
        "verifier_failed",
    }
    assert {s.value for s in ComputeOutcomeStatus} == expected
