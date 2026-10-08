# 07 · Great Jones Dutch Baby (kitchen)
**Result:** finished ad, 24.06 s (no target given), 6 scenes (3 talking, 3 B-roll), no questions, 2 failed calls (429), ~8.4 min wall clock (23:53:35 → 00:02:00). Scene 1's HeyGen clip alone took ~5.8 min.

## Silent problems
- **An 11 lb cast-iron pot is posed as "handheld", beside her face** — the page says "Weight (empty, with lid): 11 lbs". The plan set `product_size: handheld`. So all three talking scenes (1, 5, 6) ask for "holds the royal-blue Dutch oven … at chest height beside her face", and the HeyGen motion prompt repeats it. The size prompt's own example list puts a bottle and a bag under "handheld". The planner judged the size from the photos and didn't use the page's weight. Cause: plan. Severity: med.
- **Scene 4 still asks one start-frame clip for "three quick cuts"** — plan `shows`: "…beef bourguignon, mashed potatoes, and congee in three quick cuts". The motion prompt: "use two quick hard cuts … first to mashed potatoes in the pot, then to congee". Boreal makes one continuous 3.54 s clip from one picture. This is the same as the first run's #07 scene 5. Cause: plan. Severity: med.
- **Scene 3 asks for three copies of the product in one frame** — "Show three adjacent views … the royal-blue Dutch oven … on a gas stovetop, an electric-coil stovetop, and a flat induction stovetop … the three Dutch ovens remain still". Cause: picture. Severity: low–med.
- **Photos 6–8 are photos 1–3 again** (the same files at `_1024x1024`). Cause: page. Severity: low.

## Good
- Every line is on the page ("Sized to fit perfectly on one burner. Ideal if you're cooking for one or two", "Safe for all stovetops (including induction)", "4.5 (448 Reviews)", "Free shipping on orders over $100").
- The price is said mid-line, and the last word is "free", so the number margin isn't needed.

## Loud failures
- `[23:55:30]` and `[23:55:33] make_starting_picture` (scene 3 or 6) **ERROR** 429. The third try at 23:55:41 succeeded. (known, 2 wasted calls)

## First-run fixes, checked here
- Price cut: not needed (the price is mid-line).
- Fake product in portrait: **holds**.
- Captions from the script: **holds**. The transcript heard "Dutch baby", "3.5 quart cast iron"; the captions read "Dutch Baby", "3.5-quart cast-iron".
- B-roll keeps its whole clip: **holds** (scenes 2, 3, 4 play their full 4.32 / 3.16 / 3.54 s).
- Length allowance: no target.
- Pose by size: the code works, but **the plan picked the wrong size** (above).
- Voice gender: **holds** ("A woman's voice."; ~178 Hz).
- ASCII voice description: not tested (the sample's curly apostrophe was accepted).
- HeyGen Avatar IV: **holds** (3 talking clips).

## Bottom line
The facts are clean, but the size rule fails at its first borderline case: an 11 lb pot is called "handheld" and held beside the face. The first run's "three shots in one clip" scene came back unchanged.
