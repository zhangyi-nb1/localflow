"""Phase 37 — failure-mode ablation benchmark (application-eval layer).

Turns the six harness failure modes (docs/research/
FEISHU_HARNESS_ENGINEERING_SUMMARY.md §11) into a reproducible,
deterministic ablation: for each mode, a by-construction failure is run
with the relevant LocalFlow guard ON vs OFF, and we measure whether the
failure shipped. The delta is exactly "what that guard buys you."

Honesty (rule F): this is an ablation (guard-on vs guard-off), NOT a
comparison against a real competitor agent; the numbers prove "the
guard fires when it should," not a wild-field failure rate. It also
reports the mode LocalFlow does NOT mitigate (Context Rot) as an honest
gap.

§10.7: calls the existing guards as libraries (policy_guard, grounding,
verifier, react loop). Uses the kernel; never modifies it. Not
re-exported through localflow_kernel.

    python -m app.eval.failure_modes      # print the results table
"""

from __future__ import annotations

from app.eval.failure_modes.benchmark import (
    render_markdown_table,
    run_benchmark,
)
from app.eval.failure_modes.schema import FailureModeReport

__all__ = ["FailureModeReport", "render_markdown_table", "run_benchmark"]
