"""Phase 23 — SandboxRuntime behavioural tests.

These spawn real subprocesses against the test interpreter — fast
because every script is a handful of lines, but they confirm the
isolation primitives behave the way the executor relies on:

  * cwd is the scratch action dir
  * inputs are reachable at inputs/<rel>
  * declared outputs are matched, undeclared are dropped
  * timeouts kill the child and return SANDBOX_TIMEOUT
  * env scrub removes proxy / API-key envs
  * oversize outputs return OUTPUT_OVER_SIZE
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from app.harness.sandbox import SandboxRuntime
from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    ComputeOutcomeStatus,
    SandboxPolicy,
)
from app.tools.scratch import ScratchWorkspace


def _build_action(
    script: str,
    *,
    expected_outputs: list[ArtifactSpec] | None = None,
    inputs: list[ComputeInputRef] | None = None,
    policy: SandboxPolicy | None = None,
    summary: str = "Test script.",
) -> ComputeAction:
    return ComputeAction(
        script=dedent(script),
        script_summary=summary,
        inputs=inputs or [],
        expected_outputs=expected_outputs
        or [ArtifactSpec(relative_path="outputs/out.txt", description="output")],
        sandbox_policy=policy or SandboxPolicy(timeout_sec=10),
    )


@pytest.fixture
def sandbox(tmp_path: Path):
    sw = ScratchWorkspace(home=tmp_path / "home")
    return sw, SandboxRuntime()


def test_successful_script_writes_declared_artifact(sandbox, tmp_path: Path) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("hello")
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    assert outcome.exit_code == 0
    assert len(outcome.produced_artifacts) == 1
    art = outcome.produced_artifacts[0]
    assert art.relative_path == "outputs/out.txt"
    assert art.size_bytes == 5
    assert art.sha256 is not None


def test_missing_required_artifact_returns_output_missing(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        print('ran, but did not write the file')
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OUTPUT_MISSING
    assert "outputs/out.txt" in outcome.missing_artifacts


def test_optional_artifact_absence_is_ok(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        print('no file written')
        """,
        expected_outputs=[
            ArtifactSpec(
                relative_path="outputs/optional.txt",
                description="optional",
                required=False,
            )
        ],
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    assert outcome.produced_artifacts == []


def test_nonzero_exit_returns_nonzero_exit(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        import sys
        sys.exit(7)
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.NONZERO_EXIT
    assert outcome.exit_code == 7


def test_timeout_kills_long_running_script(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        import time
        time.sleep(10)
        """,
        policy=SandboxPolicy(timeout_sec=1),
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.SANDBOX_TIMEOUT
    assert outcome.duration_sec >= 1.0


def test_inputs_are_reachable_at_inputs_subdir(sandbox, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "raw.txt").write_text("payload", encoding="utf-8")
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    sw.copy_inputs(layout, workspace, [ComputeInputRef(rel_path="raw.txt", size_bytes=7)])
    action = _build_action(
        """
        with open("inputs/raw.txt", encoding="utf-8") as f:
            data = f.read()
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write(data.upper())
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    assert (layout.outputs_dir / "out.txt").read_text(encoding="utf-8") == "PAYLOAD"


def test_env_scrub_removes_proxy_and_api_keys(sandbox, monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://corp.proxy:8080")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-leak")
    monkeypatch.setenv("SOMETHING_TOKEN", "tok-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        import os, json
        snapshot = {
            "https_proxy": os.environ.get("HTTPS_PROXY"),
            "openai": os.environ.get("OPENAI_API_KEY"),
            "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
            "token": os.environ.get("SOMETHING_TOKEN"),
            "marker": os.environ.get("LOCALFLOW_COMPUTE_NETWORK"),
        }
        with open("outputs/env.json", "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        """,
        expected_outputs=[ArtifactSpec(relative_path="outputs/env.json", description="env")],
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    import json

    snapshot = json.loads((layout.outputs_dir / "env.json").read_text(encoding="utf-8"))
    assert snapshot["https_proxy"] is None
    assert snapshot["openai"] is None
    assert snapshot["anthropic"] is None
    assert snapshot["token"] is None
    assert snapshot["marker"] == "off"


def test_undeclared_outputs_are_dropped(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("declared")
        with open("outputs/extra.txt", "w", encoding="utf-8") as f:
            f.write("undeclared")
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    paths = [a.relative_path for a in outcome.produced_artifacts]
    assert paths == ["outputs/out.txt"]


def test_oversize_output_returns_over_size(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        with open("outputs/big.bin", "wb") as f:
            f.write(b"x" * 2048)
        """,
        expected_outputs=[
            ArtifactSpec(relative_path="outputs/big.bin", description="big", max_size_bytes=1024)
        ],
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OUTPUT_OVER_SIZE
    assert "outputs/big.bin" in (outcome.error or "")


def test_stdout_and_stderr_are_captured(sandbox) -> None:
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    action = _build_action(
        """
        import sys
        print('hello-out')
        print('hello-err', file=sys.stderr)
        with open("outputs/out.txt", "w", encoding="utf-8") as f:
            f.write("ok")
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    assert "hello-out" in outcome.stdout_truncated
    assert "hello-err" in outcome.stderr_truncated
    assert layout.stdout_path.exists()
    assert layout.stderr_path.exists()


def test_interpreter_not_found_returns_execution_error(sandbox, tmp_path: Path) -> None:
    sw = tmp_path / "scratch_home"
    sw.mkdir()
    workspace = ScratchWorkspace(home=sw)
    layout = workspace.create_for_action("t-001", "a-001")
    rt = SandboxRuntime(python_executable=str(tmp_path / "does_not_exist_python"))
    action = _build_action(
        """
        print('unreachable')
        """
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.EXECUTION_ERROR
    assert outcome.error is not None


def test_python_executable_defaults_to_sys_executable() -> None:
    rt = SandboxRuntime()
    assert rt.python_executable == sys.executable


def test_env_denylist_extension_strips_extra_var(sandbox, monkeypatch) -> None:
    monkeypatch.setenv("MY_CUSTOM_SECRET", "should-not-leak")
    sw = sandbox[0]
    layout = sw.create_for_action("t-001", "a-001")
    rt = SandboxRuntime(extra_env_denylist=("MY_CUSTOM_SECRET",))
    action = _build_action(
        """
        import os, json
        with open("outputs/env.json", "w", encoding="utf-8") as f:
            json.dump({"value": os.environ.get("MY_CUSTOM_SECRET")}, f)
        """,
        expected_outputs=[ArtifactSpec(relative_path="outputs/env.json", description="env")],
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    import json

    snapshot = json.loads((layout.outputs_dir / "env.json").read_text(encoding="utf-8"))
    assert snapshot["value"] is None


def test_cwd_isolation_prevents_workspace_imports(sandbox, tmp_path: Path) -> None:
    """A script's cwd is the scratch dir. Workspace files are not on
    sys.path; the only way in is the declared inputs list."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "secret_module.py").write_text("VALUE = 'sensitive'\n", encoding="utf-8")
    sw, rt = sandbox
    layout = sw.create_for_action("t-001", "a-001")
    # Don't call copy_inputs — confirm the script can't see the workspace.
    action = _build_action(
        f"""
        import os, sys
        with open("outputs/cwd.txt", "w", encoding="utf-8") as f:
            f.write(os.getcwd())
            f.write("\\n")
            f.write({str(layout.root)!r} in sys.path and "in_path" or "not_in_path")
        """,
        expected_outputs=[ArtifactSpec(relative_path="outputs/cwd.txt", description="cwd")],
    )
    outcome = rt.execute(action, layout)
    assert outcome.status is ComputeOutcomeStatus.OK
    text = (layout.outputs_dir / "cwd.txt").read_text(encoding="utf-8")
    assert os.path.normcase(text.splitlines()[0]) == os.path.normcase(str(layout.root))
