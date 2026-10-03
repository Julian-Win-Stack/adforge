# 03 · Vital Proteins Salted Caramel Collagen Peptides (supplement)
**Result:** finished ad, 24.56 s, 5 scenes (4 talking, 1 B-roll), 1 question (size/price), 0 failed calls, ~6.4 min wall clock (23:30:09 → 23:36:30), including ~40 s waiting for the answer.

## Silent problems
- **The ad is 5.4 s short of the 30 s asked for, and the check calls it a fit** — only 60 words were written (estimate 26.1 s at 2.3 w/s; real 24.56 s). `[23:32:07] run_planning_checks`: "the script fits your 30-second target". Final `SAYS`: "It runs **24.56 seconds**", with no mention of 30. `fits_target` only fails a script that is too long. Cause: plan. Severity: med (the owner asked for 30 s and got 24.5 s).
- **The price line is saved by only 0.06 s, the thinnest of the four** — `[23:32:43] transcribe_line scene 5` `{"text": "$27", "start": 2.46, "end": 3.04}`, audio 4.08 s. Speech ends at 3.58 s (`silencedetect`): 0.54 s past the token, more than the "at most 0.51 s" that NUMBER_MARGIN_SECONDS was sized on. The cut ends at 3.64 (scene 5 20.92 → 24.56). Cause: assembly. Severity: low (risk, not seen).
- **"No artificial sweeteners, colors, or flavors" comes from the brand's generic block** — the page's sentence is "Our Collagen Peptides contain one single ingredient … no artificial sweeteners, colors or flavors", which describes the unflavoured peptides. This product's own badge says only "No Artificial Sweeteners". `fact_check` passed it. Cause: page. Severity: low.
- **Overlays aren't checked** — "Fits Your Lifestyle" is a page heading, and "Mixes Easily" is fine, but nothing checks overlays against the page (as with #03's "Everyday support" in the first run). Cause: plan. Severity: low.

## Improved since the first run
- The planner asked "10.5 oz for $27.00 or 25.7 oz for $39.99?" and planned on "The 10.5 oz at $27". Every scene used a 10 oz photo (1, 2, 4); the 24 oz photos 7–10 were left out of `colour_photos`.
- The first run's unqualified "supports skin, hair, nails, bones, joints" line (footnoted on the page) is **not** in this script: no health claims this time.

## Loud failures
None.

## First-run fixes, checked here
- Price cut: **holds**, 0.06 s spare (above).
- Fake product in portrait: **holds**. No product in `person_looks` or in the portrait (the first run had a "plain canister").
- Captions from the script: **holds**. The transcript heard "caramelly"; the caption reads "caramel-y,".
- B-roll keeps its whole clip: **holds**. Scene 2 plays 5.22 s (asked 5.5 s), its whole voiced clip.
- Length allowance: one-sided. **Doesn't fix** short ads (above).
- Pose by size: **holds**. `handheld` (a 10.5 oz tub).
- Voice gender: **holds** ("A woman's voice."; median ~167 Hz).
- ASCII voice description: not tested.
- HeyGen Avatar IV: **holds** (4 talking clips).

## Waste
- 4 producer turns only narrate one finished clip.

## Bottom line
Clean facts and a sensible size question. The ad is 5.4 s short of the target with no warning, and its price line has the smallest margin of the run.
