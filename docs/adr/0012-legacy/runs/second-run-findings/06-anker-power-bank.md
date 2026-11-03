# 06 · Anker 313 Power Bank (gadget)
**Result:** finished ad, 31.04 s, 6 scenes (3 talking, 3 B-roll), 1 question (USB-C), 2 failed calls (429), ~5.1 min wall clock (23:47:59 → 23:53:08), including ~30 s waiting for the answer.

## Silent problems
- **The woman's voice sits in the male pitch range** — the median is 136–145 Hz on all five lines measured (rough autocorrelation). For comparison, the first run's chair job, where a woman spoke with a man's voice, measured 114–127 Hz, and the other women in this run measure 155–186 Hz. The handoff did say "A woman's voice." Listen to confirm. Cause: voice. Severity: med if it reads as male.
- **The producer claims visuals "avoid" the USB-C detail when nothing did** — `[23:51:04]` "chosen to match each message without showing the omitted USB‑C claim". `[23:51:22]` "Scene 2's charging visual … avoids the disputed USB‑C detail". No picture prompt mentions USB-C, and scene 2's prompt shows "the power bank connected by a cable to a phone". Cause: producer. Severity: low.
- **The photos may be the other variant** — all ten photos are named `USB-C_Input_and_Output_*`, while the page's notes say "The USB-C input port has no output function". The page mixes two variants; this is the conflict the planner asked about. Cause: page. Severity: low.
- **An em-dash joins two caption words** — the line "PowerCore 10K—a slim…" is split on spaces, so one caption word reads "10K—a". Cause: assembly. Severity: low.

## Good
- The planner asked about a real conflict on the page (USB-C output vs "no output function"). The owner answered "Leave it out of the ad", and no line mentions USB-C.
- Every number is right: 10,000mAh, three phone charges, 2.4 A, press twice for trickle mode, $25.99.

## Loud failures
- `[23:50:40]` and `[23:50:43] make_starting_picture` scene 2 **ERROR** 429 ("try again in 12s"). The third try at 23:50:52 succeeded. (known, 2 wasted calls)

## First-run fixes, checked here
- Price cut: **holds**. `$25.99.` token ends at 2.56 s, speech ends at 2.97 s, cut at 3.16 s: 0.19 s to spare.
- Fake product in portrait: **holds**.
- Captions from the script: **holds**. The transcript heard "10,000 milliamp hours" and "Power IQ"; the captions read "10,000mAh" and "PowerIQ".
- B-roll keeps its whole clip: **holds** (3 B-rolls, each as long as its audio).
- Length allowance: 31.04 s, inside the allowance.
- Pose by size: **holds** (`handheld`).
- Voice gender: in the handoff, yes; in the audio, doubtful (above).
- ASCII voice description: not tested. The sample had an em-dash and was accepted.
- HeyGen Avatar IV: **holds** (3 talking clips).

## Bottom line
Clean facts and a well-asked question. The things to check by ear are a possibly male-sounding voice on a female presenter, and the producer telling the owner the visuals avoid USB-C when nothing made them.
