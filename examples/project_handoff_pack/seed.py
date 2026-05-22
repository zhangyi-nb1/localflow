"""Seed the project_handoff_pack example workspace — Phase 20 demo.

Plants ``examples/project_handoff_pack/workspace/`` with a small
"messy mid-project" layout — what a developer's directory looks like
when they're about to hand the work off and realise nobody else can
follow it:

  - 4 Python source files (a tiny example app)
  - 2 config files (.env.example + pyproject snippet)
  - 1 sample data file
  - 2 notes (TODO + meeting prep)
  - 1 image (a logo / screenshot placeholder)

Designed to exercise the v0.17 ``project_handoff_pack`` recipe:

    folder_organizer → workspace_visualizer → agent (synth README +
    SOURCES)

Usage::

    python examples/project_handoff_pack/seed.py

Idempotent — wipes any existing workspace/ first.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06\x00\x00\x00\x1f\xf3\xffa"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)


_MAIN_PY = '''"""Tiny demo app — entry point.

Reads ``data/sample.csv`` and prints a per-category sum. Not the
point of the demo — the project_handoff_pack recipe is.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from app.scoring import score_rows


def main() -> int:
    data_path = Path(__file__).parent.parent / "data" / "sample.csv"
    if not data_path.exists():
        print(f"missing data file: {data_path}", file=sys.stderr)
        return 2
    with data_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    totals = score_rows(rows)
    for category, total in sorted(totals.items()):
        print(f"{category}: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


_SCORING_PY = '''"""Scoring utilities — used by main.py."""

from __future__ import annotations

from collections import defaultdict


def score_rows(rows: list[dict]) -> dict[str, float]:
    """Sum ``value`` per ``category``. Ignores rows with bad floats."""
    out: dict[str, float] = defaultdict(float)
    for row in rows:
        try:
            v = float(row.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
        out[row.get("category", "unknown")] += v
    return dict(out)
'''


_CONFIG_PY = '''"""App config loader — minimal."""

from __future__ import annotations

import os


DEFAULT_TIMEOUT = float(os.environ.get("APP_TIMEOUT", "30"))
DEBUG = os.environ.get("APP_DEBUG", "0") == "1"
'''


_TEST_PY = '''"""Unit test for the scoring module."""

from app.scoring import score_rows


def test_score_rows_sums_per_category():
    rows = [
        {"category": "a", "value": "1.5"},
        {"category": "a", "value": "2.5"},
        {"category": "b", "value": "10"},
    ]
    assert score_rows(rows) == {"a": 4.0, "b": 10.0}


def test_score_rows_skips_bad_floats():
    rows = [
        {"category": "a", "value": "1.0"},
        {"category": "a", "value": "not a number"},
    ]
    assert score_rows(rows) == {"a": 1.0}
'''


_SAMPLE_CSV = """category,value
alpha,12.5
beta,7.0
alpha,3.5
gamma,9.0
beta,4.5
"""


_TODO_MD = """# TODO before handoff

- [ ] Document the env vars in README (APP_TIMEOUT, APP_DEBUG)
- [ ] Add CI workflow
- [ ] Decide what to do about the scoring categories — should
  unknown values raise or just be silently dropped?
- [x] Wrote unit tests for scoring
- [ ] Replace placeholder logo
"""


_MEETING_MD = """# Handoff meeting prep

Next week. Cover:

1. Code layout (app/ structure, where main.py lives).
2. Sample data origin + format (sample.csv).
3. How to run + test.
4. Open questions (see TODO.md).
5. Known limitations: no error handling on file IO, no logging,
   no real configuration file.
"""


_ENV_EXAMPLE = """# Copy this to .env, fill in values
APP_TIMEOUT=30
APP_DEBUG=0
"""


_PYPROJECT_SNIPPET = """[project]
name = "demo-app"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
demo = "app.main:main"
"""


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

    (root / "main.py").write_text(_MAIN_PY, encoding="utf-8")
    (root / "scoring.py").write_text(_SCORING_PY, encoding="utf-8")
    (root / "config.py").write_text(_CONFIG_PY, encoding="utf-8")
    (root / "test_scoring.py").write_text(_TEST_PY, encoding="utf-8")
    (root / "sample.csv").write_text(_SAMPLE_CSV, encoding="utf-8")
    (root / "TODO.md").write_text(_TODO_MD, encoding="utf-8")
    (root / "meeting_prep.md").write_text(_MEETING_MD, encoding="utf-8")
    (root / ".env.example").write_text(_ENV_EXAMPLE, encoding="utf-8")
    (root / "pyproject_snippet.toml").write_text(_PYPROJECT_SNIPPET, encoding="utf-8")
    (root / "logo.png").write_bytes(_TINY_PNG)

    print(f"Seeded {root} with {len(list(root.iterdir()))} file(s).")


if __name__ == "__main__":
    main()
