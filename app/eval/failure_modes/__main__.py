"""``python -m app.eval.failure_modes`` — run the benchmark + print the table."""

from __future__ import annotations

from app.eval.failure_modes.benchmark import render_markdown_table, run_benchmark


def main() -> None:
    reports = run_benchmark()
    print(render_markdown_table(reports))
    print()
    for r in reports:
        print(f"[{r.feishu_id}] {r.mode}: {r.detail}")


if __name__ == "__main__":
    main()
