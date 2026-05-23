"""Phase 23 — typed ComputeAction schema (Isolation-first, not security sandbox).

Per ``docs/PHASE_23_PLAN.md``, LocalFlow's intelligence ceiling has been
capped by its narrow action vocabulary (8 built-in skills). Phase 23
adds one new typed action — ``ActionType.PYTHON_COMPUTE`` — that lets
the model propose a Python script to run inside an isolated scratch
workspace. The user's source workspace stays bound by the 8 iron rules.

This module hosts the typed contract; the executor + sandbox runtime
live elsewhere and consume these schemas via ``model_validate`` on the
host Action's ``metadata`` dict (same convention as Phase 16 FETCH).

**Honesty discipline (also enforced in docs/COMPUTE_ACTION.md):** this
is *isolation*, not a security sandbox. Network "blocking" is best
effort — we scrub well-known proxy / API-key env vars before spawning
the child, nothing more. Strict network isolation needs Docker or a
firewall rule and is explicitly out of scope for Phase 23.0.

§10.7 invariant: this is application-layer schema only. No kernel
references.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.action import RiskLevel


class ComputeInputRef(BaseModel):
    """Pointer to a workspace file the executor must copy into the
    scratch ``inputs/`` directory before the script starts.

    Deliberately narrower than ``app.primitives.ContentRef`` — that
    type lives in a package that imports tools at module load, which
    would create a circular import inside ``app.schemas``. The compute
    contract only needs a path + optional integrity fields; semantic
    classification (``ContentKind``) belongs at the planning layer
    above this schema, not inside it.
    """

    rel_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Workspace-relative path, forward-slashed, no '..' segments. "
            "The executor will copy this file into "
            "``scratch/<action_id>/inputs/<basename>`` (or a chosen "
            "stable name) before the script runs."
        ),
    )
    size_bytes: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional. Recorded so reviewers can see input scale in "
            "the approval UI without re-stat-ing the file."
        ),
    )
    sha256: str | None = Field(
        default=None,
        description="Optional hex digest, recorded for the trace and rollback manifest.",
    )

    @field_validator("rel_path")
    @classmethod
    def _no_escape(cls, v: str) -> str:
        normalized = v.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("rel_path must be non-empty")
        if normalized.startswith("/"):
            raise ValueError("rel_path must not start with '/'")
        parts = [p for p in normalized.split("/") if p]
        if any(p == ".." for p in parts):
            raise ValueError("rel_path must not contain '..' segments")
        return normalized


class ArtifactSpec(BaseModel):
    """A single expected output of a ``ComputeAction``.

    Every produced file the script writes that we plan to trust must
    be declared up-front. The verifier matches what landed in the
    scratch directory against this list — anything undeclared is
    dropped (not promoted to a later stage). ``relative_path`` is
    interpreted relative to the scratch action dir, never to the
    user workspace.
    """

    relative_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Path inside the scratch action directory where the script "
            "is expected to write. Forward-slashed, no '..' segments."
        ),
    )
    kind: Literal["file"] = Field(
        default="file",
        description=(
            "Only single files for Phase 23.0. Directory artifacts are reserved for a later phase."
        ),
    )
    description: str = Field(
        default="",
        description=(
            "Human-readable purpose of this output (shown in approval "
            "UI + trace). Helps reviewers judge whether the artifact "
            "matches the script's stated intent."
        ),
    )
    max_size_bytes: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional per-artifact byte cap. Verifier rejects the "
            "outcome if the produced file exceeds this. None falls back "
            "to ``SandboxPolicy.max_output_file_size_mb``."
        ),
    )
    content_type_hint: str | None = Field(
        default=None,
        description=(
            "Optional content-type hint (e.g. 'text/csv', 'image/png'). "
            "Verifier uses it for sniffing; never trusted blindly."
        ),
    )
    required: bool = Field(
        default=True,
        description=(
            "If True (default), missing this artifact fails the action. "
            "If False, the artifact is treated as optional best-effort."
        ),
    )

    @field_validator("relative_path")
    @classmethod
    def _no_escape(cls, v: str) -> str:
        normalized = v.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("relative_path must be non-empty")
        if normalized.startswith("/"):
            raise ValueError("relative_path must not start with '/'")
        parts = [p for p in normalized.split("/") if p]
        if any(p == ".." for p in parts):
            raise ValueError("relative_path must not contain '..' segments")
        return normalized


class SandboxPolicy(BaseModel):
    """Resource + isolation limits applied by the sandbox runtime.

    Defaults are conservative; the planner may tighten them but never
    relax beyond the hard maxima encoded here. Phase 23.0 enforces
    timeout + env scrub + cwd confinement. Memory / file-size caps are
    declared here but enforced opportunistically (Unix ``resource``
    where available; Windows hardening deferred to 23.x).
    """

    timeout_sec: int = Field(
        default=30,
        ge=1,
        le=300,
        description=(
            "Wall-clock seconds before the runtime kills the child. "
            "Hard cap 300s — long-running compute is not the target of "
            "Phase 23. Trace event SANDBOX_TIMEOUT is emitted on hit."
        ),
    )
    memory_mb: int = Field(
        default=512,
        ge=64,
        le=4096,
        description=(
            "Memory cap in MiB. Best-effort: enforced via "
            "``resource.setrlimit`` on Unix; Windows Job Objects are "
            "deferred to Phase 23.x."
        ),
    )
    max_output_file_size_mb: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Default per-artifact size cap when ``ArtifactSpec."
            "max_size_bytes`` is None. Verifier rejects files larger "
            "than this."
        ),
    )
    network_isolation: Literal["best_effort", "off"] = Field(
        default="best_effort",
        description=(
            "'best_effort' (default) scrubs HTTP_PROXY / HTTPS_PROXY / "
            "ALL_PROXY and known API-key envs before spawn and injects "
            "LOCALFLOW_COMPUTE_NETWORK=off as a hint to the script. "
            "'off' means the script may see the host's network config. "
            "Neither value provides OS-level isolation — see module "
            "docstring."
        ),
    )
    allow_workspace_reads: bool = Field(
        default=False,
        description=(
            "If False (default), no workspace file is exposed to the "
            "script — even read access goes via explicit ``inputs`` "
            "copies into scratch. If True, the executor may bind-mount "
            "the workspace read-only. Phase 23.0 keeps the strict path "
            "and ignores True (treated as False) to keep the contract "
            "honest until the runtime supports it."
        ),
    )
    env_passthrough: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit allow-list of env var names the script needs "
            "(e.g. 'LANG', 'PYTHONIOENCODING'). Everything not on this "
            "list and not on the scrubbed deny-list is passed by "
            "default; this field is for reviewers' visibility, not a "
            "strict allow-list — sandbox policy is deny-on-known-"
            "sensitive, not allow-on-known-safe."
        ),
    )
    allowed_modules: list[str] | None = Field(
        default=None,
        description=(
            "Optional informational allow-list of Python modules the "
            "script declares it needs (e.g. ['pandas', 'json']). The "
            "Phase 23.0 runtime does not enforce import restrictions; "
            "this field is metadata for the approval UI + audit trail."
        ),
    )

    @field_validator("env_passthrough")
    @classmethod
    def _validate_env_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not name or not all(c.isalnum() or c == "_" for c in name):
                raise ValueError(f"env_passthrough entry {name!r} is not a valid env var name")
            if name.startswith(tuple("0123456789")):
                raise ValueError(f"env_passthrough entry {name!r} must not start with a digit")
        return v


class ComputeAction(BaseModel):
    """A Python script the model wants run inside the scratch sandbox.

    This is the typed payload carried inside an ``Action`` whose
    ``action_type == ActionType.PYTHON_COMPUTE``. The host Action
    contributes ``action_id``, ``risk_level``, and ``reason`` (so they
    appear in the standard plan); the structured detail lives here in
    ``metadata`` for the executor + verifier to consume.

    Phase 23.0 keeps the contract minimal: one script, declared inputs,
    declared outputs, an explicit policy. No multi-script chaining —
    that is what stages are for.
    """

    script: str = Field(
        ...,
        min_length=1,
        description=(
            "The full Python source the sandbox will execute. The "
            "script runs with cwd set to the scratch action directory "
            "and reads inputs from ``./inputs/`` (relative)."
        ),
    )
    script_summary: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "One-paragraph plain-English description of what the "
            "script does. Surfaced verbatim in dry-run + approval UI "
            "as the headline — reviewers should not need to read the "
            "full source to make a yes/no call."
        ),
    )
    inputs: list[ComputeInputRef] = Field(
        default_factory=list,
        description=(
            "Workspace files the executor will copy into "
            "``scratch/<action_id>/inputs/`` before launching the "
            "child. Empty list = no inputs needed. The script must "
            "not assume any other workspace file is reachable."
        ),
    )
    expected_outputs: list[ArtifactSpec] = Field(
        ...,
        min_length=1,
        description=(
            "Artifacts the script promises to write. At least one is "
            "required — a ComputeAction that produces nothing has no "
            "reason to run. Undeclared files left in the scratch dir "
            "are dropped by the verifier."
        ),
    )
    sandbox_policy: SandboxPolicy = Field(
        default_factory=SandboxPolicy,
        description="Resource limits + isolation toggles for this run.",
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.MEDIUM,
        description=(
            "Default MEDIUM: scripted code execution is inherently "
            "higher-risk than a typed MOVE/COPY. Planners may bump to "
            "HIGH when the script touches sensitive content; LOW is "
            "discouraged and the approval UI may refuse to honour it."
        ),
    )
    requires_approval: bool = Field(
        default=True,
        description=(
            "ComputeAction defaults to requiring explicit approval "
            "regardless of risk_level. The flag exists for future "
            "trusted-recipe scenarios; Phase 23.0 enforcement keeps "
            "approval mandatory."
        ),
    )

    @field_validator("expected_outputs")
    @classmethod
    def _unique_output_paths(cls, v: list[ArtifactSpec]) -> list[ArtifactSpec]:
        seen: set[str] = set()
        for spec in v:
            if spec.relative_path in seen:
                raise ValueError(f"duplicate expected_outputs.relative_path: {spec.relative_path}")
            seen.add(spec.relative_path)
        return v

    @field_validator("inputs")
    @classmethod
    def _unique_input_paths(cls, v: list[ComputeInputRef]) -> list[ComputeInputRef]:
        seen: set[str] = set()
        for ref in v:
            if ref.rel_path in seen:
                raise ValueError(f"duplicate inputs.rel_path: {ref.rel_path}")
            seen.add(ref.rel_path)
        return v


class ComputeOutcomeStatus(str, Enum):
    """End-state of one ComputeAction execution.

    These are closed values the verifier + executor + UI all share.
    Don't add new states without bumping the schema and updating the
    approval UI strings.
    """

    OK = "ok"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    NONZERO_EXIT = "nonzero_exit"
    OUTPUT_MISSING = "output_missing"
    OUTPUT_OVER_SIZE = "output_over_size"
    EXECUTION_ERROR = "execution_error"
    VERIFIER_FAILED = "verifier_failed"


class ProducedArtifact(BaseModel):
    """A single file the script actually wrote, post-verification.

    Materialised by the sandbox runtime by scanning the scratch
    directory after the child exits and matching against declared
    ``ArtifactSpec`` entries.
    """

    relative_path: str = Field(
        ..., description="Path inside scratch/<action_id>/, forward-slashed."
    )
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(
        default=None,
        description="Hex digest of the produced file. Optional in case hashing is skipped.",
    )
    declared: bool = Field(
        default=True,
        description=(
            "True when the file matched an ``ArtifactSpec``; False is "
            "reserved for future modes that allow extras through. In "
            "Phase 23.0 the verifier drops everything with declared=False."
        ),
    )


class ComputeOutcome(BaseModel):
    """Structured result of running one ComputeAction.

    Persisted alongside the trace so the rollback manifest, the
    approval-history UI, and downstream stages all see the same
    outcome shape. JSON-safe; large stdout/stderr is truncated by the
    runtime before reaching this model.
    """

    status: ComputeOutcomeStatus
    exit_code: int | None = Field(
        default=None,
        description="Child process exit code; None when the child was killed before exit.",
    )
    duration_sec: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock seconds spent in the sandbox runtime.",
    )
    stdout_truncated: str = Field(
        default="",
        description=(
            "Last N bytes of stdout (runtime-truncated). Trace may "
            "store a longer copy; this is the embedded fast-path."
        ),
    )
    stderr_truncated: str = Field(
        default="",
        description="Last N bytes of stderr (runtime-truncated).",
    )
    produced_artifacts: list[ProducedArtifact] = Field(
        default_factory=list,
        description="Artifacts that survived verification.",
    )
    missing_artifacts: list[str] = Field(
        default_factory=list,
        description=(
            "Declared but absent artifact relative_paths. Non-empty "
            "implies status=OUTPUT_MISSING (or a worse status that "
            "preempted the check)."
        ),
    )
    error: str | None = Field(
        default=None,
        description=(
            "Short human-readable error string when status != OK. "
            "Full diagnostics live in the trace."
        ),
    )
    verifier_summary: str | None = Field(
        default=None,
        description=(
            "One-paragraph summary from the recipe-level verifier when "
            "it ran. None when the verifier was not configured for "
            "this stage."
        ),
    )


__all__ = [
    "ArtifactSpec",
    "ComputeAction",
    "ComputeInputRef",
    "ComputeOutcome",
    "ComputeOutcomeStatus",
    "ProducedArtifact",
    "SandboxPolicy",
]
