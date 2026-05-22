"""Seed the data_report_pack example workspace — Phase 20 demo.

Plants ``examples/data_report_pack/workspace/`` with a tabular-only
workspace mimicking a typical analyst's "I have a folder full of CSVs
and one Excel file, now what?" situation:

  - 3 CSVs (revenue / users / errors) covering different metrics
  - 1 XLSX with two sheets (quarterly summaries)
  - 1 small README.md describing the dataset (so summary_grounding
    has something to evaluate)

Designed to exercise the v0.17 ``data_report_pack`` recipe:

    data_analyzer → workspace_visualizer → agent (synth README + SOURCES)

Usage::

    python examples/data_report_pack/seed.py

Idempotent. The .xlsx slot falls back to a placeholder if openpyxl
isn't installed (the [data] extra is required for full demo).
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
from pathlib import Path


def _make_revenue_csv() -> str:
    """30 days of synthetic daily revenue across 3 product lines."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "product", "revenue_usd", "orders"])
    rows = [
        ("2026-04-01", "alpha", 1240.5, 24),
        ("2026-04-01", "beta", 870.0, 18),
        ("2026-04-01", "gamma", 2150.75, 41),
        ("2026-04-02", "alpha", 1180.25, 22),
        ("2026-04-02", "beta", 905.5, 19),
        ("2026-04-02", "gamma", 2280.0, 43),
        ("2026-04-03", "alpha", 1320.0, 27),
        ("2026-04-03", "beta", 945.75, 20),
        ("2026-04-03", "gamma", 2010.25, 38),
        ("2026-04-04", "alpha", 1410.5, 29),
        ("2026-04-04", "beta", 980.0, 21),
        ("2026-04-04", "gamma", 2350.25, 45),
        ("2026-04-05", "alpha", 1505.75, 31),
        ("2026-04-05", "beta", 1020.0, 22),
        ("2026-04-05", "gamma", 2470.5, 47),
        ("2026-04-06", "alpha", 1390.0, 28),
        ("2026-04-06", "beta", 995.25, 21),
        ("2026-04-06", "gamma", 2380.0, 46),
        ("2026-04-07", "alpha", 1450.5, 30),
        ("2026-04-07", "beta", 1010.0, 22),
        ("2026-04-07", "gamma", 2520.75, 48),
    ]
    writer.writerows(rows)
    return buf.getvalue()


def _make_users_csv() -> str:
    """Daily active / new / churned users across 2 weeks."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "active_users", "new_users", "churned_users"])
    rows = [
        ("2026-04-01", 12450, 240, 95),
        ("2026-04-02", 12595, 215, 70),
        ("2026-04-03", 12740, 230, 85),
        ("2026-04-04", 12885, 250, 105),
        ("2026-04-05", 13030, 245, 100),
        ("2026-04-06", 13175, 235, 90),
        ("2026-04-07", 13320, 260, 115),
        ("2026-04-08", 13465, 250, 105),
        ("2026-04-09", 13610, 240, 95),
        ("2026-04-10", 13755, 230, 85),
        ("2026-04-11", 13900, 255, 110),
        ("2026-04-12", 14045, 265, 120),
        ("2026-04-13", 14190, 240, 95),
        ("2026-04-14", 14335, 250, 105),
    ]
    writer.writerows(rows)
    return buf.getvalue()


def _make_errors_csv() -> str:
    """Hourly error counts by service over one day."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["hour", "service", "error_count", "severity"])
    rows = [
        (0, "api", 12, "low"),
        (0, "worker", 5, "low"),
        (0, "db", 1, "high"),
        (1, "api", 8, "low"),
        (1, "worker", 4, "low"),
        (1, "db", 0, "low"),
        (2, "api", 6, "low"),
        (2, "worker", 3, "low"),
        (2, "db", 0, "low"),
        (3, "api", 7, "low"),
        (3, "worker", 4, "low"),
        (3, "db", 1, "medium"),
        (4, "api", 15, "medium"),
        (4, "worker", 8, "low"),
        (4, "db", 2, "medium"),
    ]
    writer.writerows(rows)
    return buf.getvalue()


def _make_xlsx(path: Path) -> bool:
    """Quarterly summary with two sheets. Returns True on success."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return False
    wb = Workbook()
    q1 = wb.active
    q1.title = "Q1"
    q1.append(["metric", "jan", "feb", "mar"])
    q1.append(["revenue_usd", 38500, 42100, 45300])
    q1.append(["active_users", 11200, 11800, 12450])
    q1.append(["nps", 38, 41, 44])
    q2 = wb.create_sheet("Q2_forecast")
    q2.append(["metric", "apr", "may", "jun"])
    q2.append(["revenue_usd", 47800, 50200, 52600])
    q2.append(["active_users", 12900, 13400, 13900])
    q2.append(["nps", 46, 48, 49])
    wb.save(path)
    return True


def _make_readme() -> str:
    return (
        "# Dataset notes\n\n"
        "Weekly product KPI export pulled from analytics warehouse on "
        "2026-04-15. Three CSVs:\n\n"
        "- `revenue.csv` — daily revenue per product line (alpha / beta / gamma).\n"
        "- `users.csv` — daily active / new / churned counts.\n"
        "- `errors.csv` — hourly error counts by service for one day.\n\n"
        "Plus `quarterly_summary.xlsx` containing Q1 actuals and Q2 forecast.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent / "workspace",
        help="Target workspace dir (default: alongside this script).",
    )
    args = parser.parse_args()

    root: Path = args.root
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    (root / "revenue.csv").write_text(_make_revenue_csv(), encoding="utf-8")
    (root / "users.csv").write_text(_make_users_csv(), encoding="utf-8")
    (root / "errors.csv").write_text(_make_errors_csv(), encoding="utf-8")
    (root / "README.md").write_text(_make_readme(), encoding="utf-8")
    xlsx_ok = _make_xlsx(root / "quarterly_summary.xlsx")
    if not xlsx_ok:
        # Graceful: skip the xlsx if openpyxl is unavailable; the pack
        # still runs against the 3 CSVs + README.
        print(
            "  warn: openpyxl not installed; skipping quarterly_summary.xlsx "
            "(install 'localflow-agent[data]' for the full demo)"
        )

    print(f"Seeded {root} with {len(list(root.iterdir()))} file(s).")


if __name__ == "__main__":
    main()
