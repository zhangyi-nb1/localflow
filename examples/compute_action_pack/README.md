# compute_action_pack — the 怪 CSV demo

Demonstrates Phase 23 `PYTHON_COMPUTE`: a task the eight built-in
actions cannot complete on their own, because no built-in skill knows
how to *transform* tabular content.

## The dirty CSV

`workspace/sales_dirty.csv` is a deliberately messy 50-row sales export
with five distinct hygiene problems:

| Problem                          | Example row                       |
| -------------------------------- | --------------------------------- |
| Case-inconsistent region/product | `north,alpha` vs `North,Alpha`    |
| Mixed currency formats           | `"$1,240.50"` vs `$1240.50`       |
| Duplicate rows                   | Same `(date, region, product)`     |
| Missing values                   | empty `revenue` or `units` cells   |
| Mixed date formats               | `2026-01-05`, `01/05/2026`, `01-11-2026` |
| Outliers                         | `$15,000.00` for 1 unit            |

A plain `data_analyzer` recipe will choke on the mixed types and the
duplicates. A `PYTHON_COMPUTE` action can clean it in one shot.

## Running the demo

The integration test `tests/test_compute_demo_end_to_end.py` drives the
flow programmatically: it plans a single `PYTHON_COMPUTE` action that
normalises the CSV and emits `outputs/cleaned.csv` + a one-paragraph
`outputs/report.md`. Then a follow-up `MOVE` action (regular pack
stage) promotes the cleaned file into the workspace.

```
python -m pytest tests/test_compute_demo_end_to_end.py -v
```

## Why ComputeAction is the right primitive here

- **Capability gap.** None of MKDIR/MOVE/RENAME/COPY/INDEX/SUMMARIZE/
  CONVERT/FETCH can do tabular cleaning.
- **Isolation, not extension.** The cleaning script runs in scratch.
  The workspace stays untouched until pack.
- **Approval-first.** The model-authored script is shown verbatim in
  the dry-run and approval UI before it runs.
- **Reversible.** Rollback wipes scratch; if the user rejects the
  cleaned output, no workspace state has shifted.

See `docs/COMPUTE_ACTION.md` for the contract and the honesty
discipline ("isolation, not security sandbox").
