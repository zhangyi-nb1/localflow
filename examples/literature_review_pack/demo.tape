# vhs tape — the ≤60s flagship demo GIF.
#   install: https://github.com/charmbracelet/vhs
#   record:  vhs examples/literature_review_pack/demo.tape   (run from repo root)
#   output:  examples/literature_review_pack/demo.gif
#
# Records the DETERMINISTIC path (no API key) so it always reproduces.
# The guard ON/OFF pack-run contrast (needs a key) is left as commented
# steps at the bottom for a longer cut.

Output examples/literature_review_pack/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 820
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 35ms

# --- title card ---
Type "# LocalFlow flagship — an LLM-written literature review with planted fabrications."
Enter
Sleep 1200ms
Type "# The grounding gate splits it into claims, checks each against its source,"
Enter
Sleep 800ms
Type "# flags the fabricated ones, and REFUSES TO SHIP. (deterministic, no API key)"
Enter
Sleep 1500ms

# --- seed the complex workspace ---
Type ".venv/bin/python examples/literature_review_pack/seed.py"
Enter
Sleep 2500ms

# --- run the gate + scorecard ---
Type ".venv/bin/python examples/literature_review_pack/seed.py --check"
Enter
Sleep 6000ms

# Hold on the scorecard: recall 6/6 = 100%, FP 0/12 = 0%, decision ROLLBACK.
Sleep 4000ms

# --- (optional longer cut: guard ON vs OFF, needs ANTHROPIC_API_KEY) ---
# Type ".venv/bin/localflow pack run literature_review_pack_nogate --workspace examples/literature_review_pack/workspace   # OFF: ships fakes"
# Enter
# Sleep 8000ms
# Type ".venv/bin/localflow pack run literature_review_pack --workspace examples/literature_review_pack/workspace          # ON: gate exit 3"
# Enter
# Sleep 8000ms
