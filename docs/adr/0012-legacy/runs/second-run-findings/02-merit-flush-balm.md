# 02 · MERIT Flush Balm (makeup)
**Result:** finished ad, 26.64 s, 6 scenes (4 talking, 2 B-roll), no questions, 1 failed call (429, retried), ~3.7 min wall clock (23:25:37 → 23:29:18).

## Silent problems
- **The price line is saved by only 0.09 s** — the planner wrote "MERIT Flush Balm is thirty dollars." (spelled out), but Scribe still merged it into one token: `[23:27:22] transcribe_line scene 6` `{"text": "$30", "start": 2.04, "end": 2.32}`, audio 3.36 s. Speech ends at 2.83 s (`silencedetect`), 0.51 s after the token. The cut ends at 2.32 + 0.6 = 2.92 (scene 6 23.72 → 26.64). So spelling the number out does not stop the merge. Cause: assembly. Severity: low (risk, not seen).
- **The last caption leaves before the last word is said** — the caption words are timed from the transcript, so "thirty dollars." ends at 2.32 s while the voice goes on to 2.83 s. The ad's final ~0.5 s has no caption. The same happens on every price line (all 4 ads). Cause: assembly. Severity: low.
- **The ad runs 3.4 s under its 30 s target and nobody says so** — estimate 26.0 s (65 words ÷ 2.5 w/s), `run_planning_checks`: "fits your 30-second target". Final `SAYS`: "It runs 26.64 seconds", with no mention of the target. The check is still one-sided. Cause: plan. Severity: low.
- **Photos 1 and 2 are the same image** (`…LeBonBon…_2000x.jpg` and the same file without the size suffix). All six scenes used photo 1. Cause: page. Severity: low.

## Loud failures
- `[23:27:35] make_starting_picture` scene 5 **ERROR** 429 "input-images per min: Limit 5, Used 5 … try again in 12s". Retried at 23:27:37 and succeeded. The owner was not told. (known, 1 wasted call)

## First-run fixes, checked here
- Price cut: **holds** (0.09 s spare, above). In the first run this ad lost "dollars".
- Fake product in portrait: **holds**. No product in `person_looks` or in the portrait. The first run's invented jar is gone.
- Captions from the script: **holds**. The transcript heard "Merit" and "$30"; the captions read "MERIT" and "thirty dollars.".
- B-roll keeps its whole clip: **holds**. Scene 2 plays 5.74 s (asked 6.17 s), scene 5 plays 3.28 s (asked 3.5 s). Each is its voiced clip, cut to its audio by design.
- Length allowance: passes. One-sided (above).
- Pose by size: **holds**. `handheld`, and the same sentence is in the pictures and the motion prompts.
- Voice gender: **holds** ("A woman's voice."; median ~182 Hz).
- ASCII voice description: not tested (none needed). Note that line 4 carries a curly "It’s", which only goes to speech.
- HeyGen Avatar IV: **holds** (4 talking clips).

## Waste
- 12 scene tools fired in one turn against a 5-images/min limit (known cause).
- 6 producer turns only narrate one finished clip (23:28:46–23:29:11).

## Bottom line
Every claim is on the page, and the first run's two big defects (price cut and fake jar) are gone. What is left is the thin price margin and a 3 s shortfall that nobody mentioned.
