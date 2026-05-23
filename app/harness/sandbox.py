"""Phase 23 — Sandbox runtime for ``PYTHON_COMPUTE`` actions (Isolation-first).

Runs one ``ComputeAction`` script inside a scratch directory and
returns a typed ``ComputeOutcome``. The runtime applies:

  * **cwd confinement** — child process cwd = scratch action dir.
    Inputs are available as ``./inputs/<rel_path>``; outputs are
    expected at the paths declared in ``ArtifactSpec.relative_path``.
  * **timeout** — wall-clock kill via ``subprocess.run(timeout=...)``.
  * **env scrub** — well-known proxy / API-key env vars are removed
    before spawn; ``LOCALFLOW_COMPUTE_NETWORK=off`` is injected as a
    hint to the script. This is *best-effort*; no OS-level network
    isolation is promised. See ``docs/COMPUTE_ACTION.md``.
  * **resource limits** — soft memory cap via ``resource.setrlimit``
    on Unix when available. Windows Job Objects are out of scope for
    Phase 23.0 (declared in ``SandboxPolicy.memory_mb`` docstring).
  * **artifact verification** — every declared ``ArtifactSpec`` is
    matched against the scratch outputs; undeclared files are
    dropped, oversize files cause ``OUTPUT_OVER_SIZE``.

§10.7: this module is part of the 3rd deliberate kernel exception
(PYTHON_COMPUTE). Kept additive — nothing here changes behaviour of
the 8 existing action types.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeOutcome,
    ComputeOutcomeStatus,
    ProducedArtifact,
)
from app.tools.hash_ops import sha256_file
from app.tools.scratch import ScratchLayout

# Env vars stripped from the child process. We deny on known-sensitive
# rather than allow on known-safe — the latter would be brittle across
# Python tool ecosystems (PYTHONPATH, LANG, TMP, etc. are all needed).
_DEFAULT_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        # Proxy configuration (network)
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "FTP_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "ftp_proxy",
        "no_proxy",
        # Common LLM / cloud credentials
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACEHUB_API_TOKEN",
        # LocalFlow internal config that shouldn't leak to a script
        "LOCALFLOW_HOME",
        "LOCALFLOW_MODEL",
    }
)

_STDOUT_TAIL_BYTES = 8 * 1024
_STDERR_TAIL_BYTES = 8 * 1024


class SandboxRuntime:
    """Stateless executor for one ComputeAction. Holds policy-independent
    config; per-action behaviour comes from the ``ComputeAction`` model.
    """

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        extra_env_denylist: tuple[str, ...] = (),
    ) -> None:
        self.python_executable = python_executable or sys.executable
        self.env_denylist = _DEFAULT_ENV_DENYLIST | set(extra_env_denylist)

    def execute(
        self,
        action: ComputeAction,
        layout: ScratchLayout,
    ) -> ComputeOutcome:
        """Run the script and return a typed outcome.

        Never raises for a script failure — failure is encoded in the
        returned ``ComputeOutcome.status``. The only exceptions that
        escape are infrastructure issues (cannot write the script to
        disk, cannot launch the interpreter at all) and they map to
        ``EXECUTION_ERROR`` via the caller's try/except.
        """
        layout.script_path.write_text(action.script, encoding="utf-8")
        env = self._build_env(action)
        preexec = self._build_preexec(action)
        timeout = action.sandbox_policy.timeout_sec

        start = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        stdout_bytes = b""
        stderr_bytes = b""
        error: str | None = None

        try:
            proc = subprocess.run(  # noqa: S603 — argv built from controlled vals
                [self.python_executable, str(layout.script_path)],
                cwd=str(layout.root),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
                preexec_fn=preexec,  # type: ignore[arg-type]
            )
            stdout_bytes = proc.stdout or b""
            stderr_bytes = proc.stderr or b""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
        except FileNotFoundError as exc:
            error = f"interpreter not found: {exc}"
        except OSError as exc:
            error = f"sandbox launch failed: {type(exc).__name__}: {exc}"

        duration = time.monotonic() - start

        stdout_tail = _tail_text(stdout_bytes, _STDOUT_TAIL_BYTES)
        stderr_tail = _tail_text(stderr_bytes, _STDERR_TAIL_BYTES)
        _write_log(layout.stdout_path, stdout_bytes)
        _write_log(layout.stderr_path, stderr_bytes)

        if error is not None:
            return ComputeOutcome(
                status=ComputeOutcomeStatus.EXECUTION_ERROR,
                exit_code=exit_code,
                duration_sec=duration,
                stdout_truncated=stdout_tail,
                stderr_truncated=stderr_tail,
                error=error,
            )

        if timed_out:
            return ComputeOutcome(
                status=ComputeOutcomeStatus.SANDBOX_TIMEOUT,
                exit_code=exit_code,
                duration_sec=duration,
                stdout_truncated=stdout_tail,
                stderr_truncated=stderr_tail,
                error=f"sandbox killed after {timeout}s",
            )

        if exit_code is not None and exit_code != 0:
            return ComputeOutcome(
                status=ComputeOutcomeStatus.NONZERO_EXIT,
                exit_code=exit_code,
                duration_sec=duration,
                stdout_truncated=stdout_tail,
                stderr_truncated=stderr_tail,
                error=f"script exited with code {exit_code}",
            )

        produced, missing, over_size = self._collect_artifacts(action, layout)
        if over_size:
            return ComputeOutcome(
                status=ComputeOutcomeStatus.OUTPUT_OVER_SIZE,
                exit_code=exit_code,
                duration_sec=duration,
                stdout_truncated=stdout_tail,
                stderr_truncated=stderr_tail,
                produced_artifacts=produced,
                missing_artifacts=missing,
                error="one or more outputs exceeded the size cap: " + ", ".join(over_size),
            )
        if missing:
            return ComputeOutcome(
                status=ComputeOutcomeStatus.OUTPUT_MISSING,
                exit_code=exit_code,
                duration_sec=duration,
                stdout_truncated=stdout_tail,
                stderr_truncated=stderr_tail,
                produced_artifacts=produced,
                missing_artifacts=missing,
                error="declared outputs not produced: " + ", ".join(missing),
            )
        return ComputeOutcome(
            status=ComputeOutcomeStatus.OK,
            exit_code=exit_code,
            duration_sec=duration,
            stdout_truncated=stdout_tail,
            stderr_truncated=stderr_tail,
            produced_artifacts=produced,
        )

    # -- env / resource setup -----------------------------------------

    def _build_env(self, action: ComputeAction) -> dict[str, str]:
        # Deny-on-known-sensitive: copy os.environ then strip the
        # denylist + anything ending in well-known token suffixes.
        env: dict[str, str] = {}
        for k, v in os.environ.items():
            if k in self.env_denylist:
                continue
            if action.sandbox_policy.network_isolation == "best_effort":
                upper = k.upper()
                if upper.endswith("_API_KEY") or upper.endswith("_TOKEN"):
                    continue
            env[k] = v
        env["LOCALFLOW_COMPUTE_NETWORK"] = (
            "off" if action.sandbox_policy.network_isolation == "best_effort" else "on"
        )
        env["PYTHONIOENCODING"] = env.get("PYTHONIOENCODING", "utf-8")
        # Defense-in-depth: a script that *does* try to use HTTPS via
        # urllib without proxies will go direct — that's still better
        # than honouring a corporate MITM proxy without consent.
        return env

    def _build_preexec(self, action: ComputeAction):  # noqa: ANN202
        """Return a preexec_fn for Unix memory caps, or None elsewhere.

        Windows has no equivalent in the stdlib; the policy field docs
        say so explicitly. Phase 23.x may add Job Objects.
        """
        if os.name != "posix":
            return None
        try:
            import resource  # type: ignore[import-not-found]
        except ImportError:
            return None

        mem_bytes = action.sandbox_policy.memory_mb * 1024 * 1024

        def _limit() -> None:  # pragma: no cover — runs in child only
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except (ValueError, OSError):
                pass

        return _limit

    # -- artifact collection ------------------------------------------

    def _collect_artifacts(
        self, action: ComputeAction, layout: ScratchLayout
    ) -> tuple[list[ProducedArtifact], list[str], list[str]]:
        produced: list[ProducedArtifact] = []
        missing: list[str] = []
        over_size: list[str] = []
        for spec in action.expected_outputs:
            path = _resolve_artifact_path(layout, spec)
            if not path.is_file():
                if spec.required:
                    missing.append(spec.relative_path)
                continue
            size = path.stat().st_size
            cap = self._size_cap_bytes(action, spec)
            if size > cap:
                over_size.append(spec.relative_path)
                continue
            try:
                digest: str | None = sha256_file(path)
            except OSError:
                digest = None
            produced.append(
                ProducedArtifact(
                    relative_path=spec.relative_path,
                    size_bytes=size,
                    sha256=digest,
                    declared=True,
                )
            )
        return produced, missing, over_size

    @staticmethod
    def _size_cap_bytes(action: ComputeAction, spec: ArtifactSpec) -> int:
        if spec.max_size_bytes is not None:
            return spec.max_size_bytes
        return action.sandbox_policy.max_output_file_size_mb * 1024 * 1024


def _resolve_artifact_path(layout: ScratchLayout, spec: ArtifactSpec) -> Path:
    """Resolve an ArtifactSpec.relative_path inside the scratch action
    dir. The schema validator already rejects '..' and absolute paths."""
    rel = spec.relative_path.replace("\\", "/").lstrip("/")
    return layout.root / rel


def _tail_text(data: bytes, max_bytes: int) -> str:
    if not data:
        return ""
    tail = data[-max_bytes:]
    try:
        return tail.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _write_log(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data or b"")
    except OSError:
        pass
