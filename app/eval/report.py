"""Markdown report renderer for eval results.

A report aggregates one batch of :class:`EvalResult` into a single
Markdown document the user (or CI) can read at a glance:

  * **Summary**: pass count, average duration, failure-type histogram
    across the batch
  * **Per task**: one section per task with grader verdicts + a trace
    failure breakdown

The report is the user-visible output of ``localflow eval run`` and
the artefact that gets attached to CI runs / release notes.
"""

from __future__ import annotations

from collections import Counter

from app.eval.schema import EvalResult


def render_eval_report(results: list[EvalResult]) -> str:
    lines: list[str] = []
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    avg_ms = int(sum(r.duration_ms for r in results) / total) if total else 0
    histogram: Counter[str] = Counter()
    for r in results:
        for ftype, count in r.failure_summary.items():
            histogram[ftype] += count

    lines.append("# LocalFlow eval report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Tasks: **{total}**")
    lines.append(f"- Passed: **{passed} / {total}**")
    lines.append(f"- Failed: **{total - passed} / {total}**")
    lines.append(f"- Average duration: **{avg_ms} ms**")
    lines.append("")
    if histogram:
        lines.append("### Failure-type histogram (from trace)")
        lines.append("")
        lines.append("| FailureType | Count |")
        lines.append("|---|---:|")
        for ftype, count in histogram.most_common():
            lines.append(f"| `{ftype}` | {count} |")
        lines.append("")
    else:
        lines.append("_No failure-type events emitted across the batch._")
        lines.append("")

    lines.append("## Per task")
    lines.append("")
    for r in results:
        badge = "✅" if r.passed else "❌"
        lines.append(f"### {badge} `{r.task_id}` — {r.title}")
        lines.append("")
        lines.append(
            f"_run_id: `{r.run_id}` · duration: {r.duration_ms} ms · "
            f"{sum(1 for v in r.grader_verdicts if v.passed)}/{len(r.grader_verdicts)} graders passed_"
        )
        lines.append("")
        if r.error:
            lines.append("**Run-level error:**")
            lines.append("")
            lines.append("```")
            lines.append(r.error)
            lines.append("```")
            lines.append("")
            continue
        if r.grader_verdicts:
            lines.append("| Grader | Verdict | Detail |")
            lines.append("|---|:---:|---|")
            for v in r.grader_verdicts:
                badge = "✅" if v.passed else "❌"
                detail = v.detail.replace("|", "\\|") if v.detail else ""
                lines.append(f"| `{v.name}` | {badge} | {detail} |")
            lines.append("")
        if r.failure_summary:
            lines.append("**Trace failure breakdown:**")
            lines.append("")
            for ftype, count in sorted(r.failure_summary.items(), key=lambda x: -x[1]):
                lines.append(f"- `{ftype}`: {count}")
            lines.append("")

    return "\n".join(lines)
