# 09 · Momofuku Chili Crunch Sauce (food, 6-second ad)
**Result:** finished ad, 5.7 s against a 6 s target, 2 scenes (1 talking, 1 B-roll), no questions, 0 failed calls, ~4.4 min wall clock (00:07:37 → 00:12:00).

## Silent problems
- **The producer quotes a transcript it didn't get** — `[00:09:41] SAYS` "Scene 2's audio was verified: "Drizzle it over a bowl. Just ten dollars."" The transcript was "Drizzle it over a bowl. Just $10". Harmless here, but the producer writes what it expects rather than what it was sent. Cause: producer. Severity: low.
- **An em-dash joins two caption words** — "bowl—just" is one caption word. Cause: assembly. Severity: low.
- **The voice pitch is borderline** (~155 Hz on scene 1, with few voiced frames; scene 2 ~182 Hz). Cause: voice. Severity: low.
- **Photo 2 is photo 1 again** (`…Hero.jpg` / `…Hero_1024x1024.jpg`). Cause: page. Severity: low.

## Good
- Both lines are on the page ("Drizzle it over a bowl", $10.00, "squeezable … format").
- The price is in the B-roll scene, which keeps its whole 3.08 s clip. So "ten dollars", which ends 0.52 s after the `$10` token, is never at risk. In the first run the drizzle was cut from 3.17 s to 2.0 s and "$10" was cut off.

## Loud failures
None.

## First-run fixes, checked here
- Price cut: not needed. The price is in a B-roll scene, which is kept whole.
- Fake product in portrait: **holds**.
- Captions from the script: **holds** ("ten dollars." for the heard "$10").
- B-roll keeps its whole clip: **holds** (3.08 s, the whole voiced clip).
- Length allowance: 5.7 s for 6 s.
- Pose by size: **holds** (`handheld`).
- Voice gender: **holds** in the handoff; pitch borderline.
- ASCII voice description: not tested.
- HeyGen Avatar IV: **holds** (1 talking clip).

## Bottom line
A clean 6-second ad. The first run's two cuts (drizzle and "$10") are both gone.
