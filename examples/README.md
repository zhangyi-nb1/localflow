# Examples

Seed a messy workspace and run the harness end-to-end:

```bash
python examples/seed.py
localflow plan ./examples/messy_downloads --goal "Sort by category, don't delete anything"
# copy the printed task_id, then:
localflow dry-run --task-id <task_id>
localflow execute --task-id <task_id>
localflow verify  --task-id <task_id>
localflow rollback --run-id <task_id>   # optional: undo
```

`seed.py` includes a deliberate duplicate (`agent_memory_survey.pdf` and `duplicate_paper.pdf` share content) so the planner emits a `duplicates_report.md`.
