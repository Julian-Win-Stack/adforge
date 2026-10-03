# 01 · Naturium Multi-Active Exosome Serum (skincare)
**Result:** finished ad, 29.36 s, 5 scenes (4 talking, 1 B-roll), no questions, 0 failed calls, ~5.2 min wall clock (23:19:39 → 23:24:52).

## Silent problems
- **The price line is saved by only 0.11 s** — `[23:21:30] transcribe_line scene 5`: `{"text": "$27", "start": 4.84, "end": 5.38}`, audio 6.38 s. `silencedetect` on `speak_line_OuD618E.wav` puts the end of speech at 5.87 s, so "twenty-seven dollars" runs 0.49 s past the token. The cut ends at 5.38 + 0.6 = 5.98 (`assemble_ad`: scene 5 23.38 → 29.36), so nothing is lost this time, with 0.11 s to spare. Cause: assembly. Severity: low (risk, not seen).
- **Producer over-claims checking** — `[23:21:30] SAYS` "Three voice lines have been verified word-for-word", `[23:21:32]` "All five voice lines are now verified exactly." It only read the transcripts it was sent; nothing compares them to the lines. They did match this time. Cause: producer (known). Severity: low.
- **The two "photos" are one photo** — photos 1 and 2 are the same file at two widths (`…Front-CapOn-bonebkgd.jpg?width=1920` / `width=2048`). The producer told the owner "I found two usable product photos"; all five scenes used photo 1. Cause: page. Severity: low.
- **Voice pitch is low for a woman** — median ~157 Hz on scene 1 (rough autocorrelation; women usually 165–255 Hz). The description did say "A woman's voice." Listen to confirm. Cause: voice. Severity: low.

## Loud failures
None.

## First-run fixes, checked here
- Price cut (NUMBER_MARGIN_SECONDS): **holds** (0.11 s spare, above).
- Fake product in portrait: **holds**. `person_looks` names no product; the portrait shows none.
- Captions from the script: **holds**. `timed_script` gives the line's own words ("Multi-Active", "$27.").
- B-roll keeps its whole clip: **holds**. Scene 2 plays 5.66 s = its whole voiced clip (Boreal asked for 5.83 s, cut to the 5.66 s audio by design).
- Length allowance: estimate 31.0 s (65 words ÷ 2.1 w/s) passed under the 32 s ceiling; real 29.36 s.
- Pose by size: **holds**. `handheld`; every talking picture and HeyGen motion prompt carry the handheld sentence.
- Voice gender: **holds** in the handoff ("A woman's voice."); pitch borderline (above).
- ASCII voice description: not tested. The description had no non-ASCII characters.
- HeyGen Avatar IV: **holds**. 4 talking clips on `heygen/avatar-iv`, each as long as its audio.

## Waste
- 4 producer turns only relay "Scene N is finished" (23:23:26–23:24:20).
- The one B-roll is a still bottle with a slow push-in (motion prompt: "The bottle remains still").

## Bottom line
A clean run. Every claim is on the page, and the portrait, captions and price cut all behave as fixed. The only risk is how thin the price margin is.
