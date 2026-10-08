# 08 · Molly's Suds Toilet Bowl Cleaner (cleaning)
**Result:** finished ad, 15.48 s against a 15 s target, 4 scenes (2 talking, 2 B-roll), 1 question (one-time vs subscription price), 0 failed calls, ~4.7 min wall clock (00:02:28 → 00:07:10), including ~37 s waiting for the answer.

## Silent problems
- **The price scene is only 2.2 s of "It's $9.99."** — it is a two-word line on its own talking clip (scene 4, 13.28 → 15.48). This is a pacing fact, not an error: the plan kept the price as its own scene to fit 15 s. Cause: plan. Severity: low.
- **The last caption ends ~0.4 s before the voice does** — the `$9.99.` token ends at 1.6 s, and speech ends at 2.03 s. Cause: assembly. Severity: low.
- **Photos 1 and 2 are the same image** (`TBC-3PK-MAIN_1080x.png` / `TBC-3PK-MAIN.png`). It is a single bottle despite the "3PK" name. Cause: page. Severity: low.

## Why it asked about the price this time (and #03 asked too)
- The rule has been in `PLAN_INSTRUCTIONS` since 2026-09-21, unchanged: "Decide "ask" when … they give different prices to choose between (such as a single item, a pack and a subscription)" ([planning.py:91-93](../../../backend/jobs/planning.py)). The only planner prompt changes since the first run (`f259110`, `2eb6ace`, `d5084b0`) add size, gender and "describe only the person". None of them touches price.
- The page offers "One-time purchase: $9.99" and "Subscribe & save (5%)". That is the same choice the first run answered silently. So the difference is the model's call on the same rule. It is not deterministic: in the first run #17 asked and #08 didn't.
- #03 is different: it asked about **size** ("10.5 oz for $27.00 or 25.7 oz for $39.99"), not subscription. Its page_text this run lists both sizes with prices. The first-run write-up of #03 mentions only "$27.00 / Subscribe & Save 15%" and the 24 oz photos. So the second size's price may not have been in that run's page text (a page or scraper difference; the first run's page text is no longer here to compare).

## Good
- Every claim is on the page ("98% plant-based", "Extended reach neck provides 360 degree coverage under rim", "Fights toilet bowl rings and removes stains from limescale, hard water & rust").
- The script estimate came out on target, so there was no "shorten?" question this time (the first run asked one needlessly).

## Loud failures
None.

## First-run fixes, checked here
- Price cut: **holds**. Token ends at 1.6 s, speech at 2.03 s, cut at 2.2 s: 0.17 s to spare (the first run cut "$9.99").
- Fake product in portrait: **holds** (the first run had a fake bottle).
- Captions from the script: **holds**. The transcript heard "ninety-eight percent" and "extended reach"; the captions read "98%" and "extended-reach".
- B-roll keeps its whole clip: **holds**. Scenes 2 and 3 play 4.6 s and 3.94 s; in the first run the scrub was cut by 0.8 s.
- Length allowance: 15.48 s; the estimate was right.
- Pose by size: **holds** (`handheld`).
- Voice gender: **holds** (~186 Hz).
- ASCII voice description: not tested.
- HeyGen Avatar IV: **holds** (2 talking clips).

## Bottom line
Clean, and the three defects this ad had in the first run (price cut, fake bottle, B-roll scrub cut short) are all gone.
