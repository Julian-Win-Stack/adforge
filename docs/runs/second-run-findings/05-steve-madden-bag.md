# 05 · Steve Madden Kenzo Bag Gold (well-known brand)
**Result:** finished ad, 24.28 s, 5 scenes (3 talking, 2 B-roll), no questions, 0 failed calls, ~3.8 min wall clock (23:43:43 → 23:47:29).

## Silent problems
- **5.7 s short of the 30 s asked for, called a fit** — the estimate was 25.2 s (63 words ÷ 2.5 w/s). `run_planning_checks` said "fits your 30-second target", and the final `SAYS` was "It runs 24.28 seconds". Cause: plan. Severity: med.
- **Scene 4 asks for five placements in one 5.56 s clip** — the motion prompt says "The hands place the phone, small wallet, keys, small headphones, and compact makeup into the open bag one at a time". That is about 1.1 s per item. (Whether it plays out is for the human to grade.) Cause: picture. Severity: low.
- **Photo 6 is photo 1 again** (`…BKENZO_GOLD_01.jpg`, with and without `width=1920`). Cause: page. Severity: low.

## Good
- Every line is word for word on the page ("Fits daily essentials, including a phone, small wallet, keys, small headphones, and compact makeup"; "Duster bag included").
- The producer told the owner honestly that the photos don't show the tassel charm or the duster bag, so they won't be pictured (`[23:46:05]`). Scene 5's prompt also says "Do not invent or add a tassel charm, duster bag".
- The price is said mid-line ("…eighty-eight dollars and includes…"), so the number margin isn't needed. The cut ends after "bag".

## Loud failures
None.

## First-run fixes, checked here
- Price cut: not needed here (the price is not the last word).
- Fake product in portrait: **holds** (no product in the portrait).
- Captions from the script: **holds**. The transcript heard "$88" and "kiss lock"; the captions read "eighty-eight dollars" and "kiss-lock".
- B-roll keeps its whole clip: **holds**. Scene 3 is 4.72 s, scene 4 is 5.56 s; each is its whole voiced clip.
- Length allowance: one-sided (above).
- Pose by size: **holds** (`handheld` clutch).
- Voice gender: **holds** ("A woman's voice."; ~172 Hz).
- ASCII voice description: not tested.
- HeyGen Avatar IV: **holds** (3 talking clips).

## Waste
- 5 producer turns only relay "Scene N is finished".

## Bottom line
Factually clean, like the first run. The ad is 5.7 s short of its target, and nobody says so.
