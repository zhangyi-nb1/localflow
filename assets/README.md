# `assets/` — visual demo collateral

This directory holds the GIFs and screenshots referenced from the
top-level [README.md](../README.md). The productisation guide
(`localflow_readme_productization_guidance.md` §4.1) flags strong
visual demos as the difference between "users skim the README and
leave" vs. "users get the value in 10 seconds".

The README **does not yet embed any of these assets** — they are
queued here rather than added as broken `<img>` placeholders, because
a 404'd image on the GitHub project front page looks worse than no
image at all. Land a real asset, then update the README to embed it.

---

## Asset spec

Three assets, in priority order:

| # | Filename | Format | What it must show |
|---|---|---|---|
| 1 | `demo_research_pack.gif` | animated GIF, ≤ 6 MB, ~30–45 s | `localflow pack run research_pack` end-to-end: workspace tree before → CLI progress → workspace tree after, with the recipe verifier badge visible |
| 2 | `readme_before_workspace.png` | static PNG, ≤ 500 KB | A messy workspace listing — mixed PDFs / CSVs / images / notes at the workspace root, before any pack runs |
| 3 | `readme_create_pack_ui.png` | static PNG, ≤ 500 KB | The Streamlit "📦 Create Pack" page with one of the three flagship packs picked and the dry-run preview rendered |
| 4 | `readme_after_pack.png` | static PNG, ≤ 500 KB | The same workspace as #2, after `research_pack` ran: `papers/` · `data/` · `notes/` · `review/` · `README.md` · `SOURCES.md` · `analysis_charts/` visible |

Naming is fixed — the README will reference these exact filenames once
they land.

---

## Recording the GIF (item #1)

Pre-flight (one-time):
- Install: `pip install -e ".[all]"` from repo root.
- Seed a workspace: `python examples/research_pack/seed.py --dest .\sandbox\demo_ws\`
- Confirm an LLM key is set in `.env` if you want stage 5 (agent synthesis)
  to run — otherwise stage 5 is `failure_policy: skip` and the GIF will
  still produce stages 1–4 outputs.

Recording:
- Use any of: ScreenToGif (Windows, free), peek (Linux), Kap (macOS).
- Resolution: 1280×720 max, 15 fps is enough.
- Terminal font: ≥ 14 pt (small text on GitHub doesn't survive resizing).
- Frame:
  1. Show the before workspace tree (`tree /F .\sandbox\demo_ws\`).
  2. Run `localflow pack run research_pack --workspace .\sandbox\demo_ws\ --yes --locale zh-CN`.
  3. Let stages complete; wait for the deliverable verifier table.
  4. Show the after workspace tree.
  5. Optional: cat the `README.md` head to prove the pack is real.

Trim aggressively. 30–45 s is the upper bound — anything longer and
GitHub readers won't watch to the end.

---

## Recording the UI screenshots (items #2–4)

1. `localflow ui-serve` → opens `http://127.0.0.1:8501`.
2. For `readme_create_pack_ui.png`: pick `research_pack` on the
   📦 Create Pack page, point it at `.\sandbox\demo_ws\`, run dry-run,
   screenshot the full page with the dry-run table visible.
3. For `readme_before_workspace.png` / `readme_after_pack.png`: either
   use the 🗂️ Workspace page screenshot (before/after) or take a
   Windows Explorer screenshot of the workspace folder.

---

## When you land an asset

1. Drop the file here under its canonical filename.
2. Open [`../README.md`](../README.md) and add the embed at the top of
   the relevant section. Patterns:
   ```markdown
   ![Research Pack demo](assets/demo_research_pack.gif)
   ```
3. Verify the GIF renders on GitHub (push to a branch and open the PR
   preview — local Markdown previewers handle GIFs differently).
4. Delete the asset's row from this file's "asset spec" table once it
   has shipped.
