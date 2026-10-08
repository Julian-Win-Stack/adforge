# 04 · Mack Weldon Vintage French Terry Crew Neck Sweatshirt (clothing)
**Result:** finished ad, 24.5 s, 5 scenes (3 talking, 2 B-roll), no questions, 0 failed calls, ~6 min wall clock (23:37:02 → 23:42:59).

## Silent problems
- **Scene 3 again describes the Ace Sweatpant** — line: "The roomy, never-slouchy fit and perfect stretch keep it comfortable without looking saggy." "Perfect Stretch + Recovery / So they're always comfortable, but never saggy" is in the page's "Why We Love 'Em" block. That block ends "Back-Ribbed Ankle Cuffs … Shop Ace Sweatpant", so it is a cross-sell for the sweatpant. `[23:38:17] fact_check`: "Every script claim is supported". This is the first run's #04 defect again (1 line this time, 2 then). Cause: page. Severity: med.
- **The talking presenter holds the sweatshirt beside his face and never wears it** — the plan had to choose tiny, handheld or large, and it chose `handheld`. So the pose is "holds the product at chest height, beside their face", and the picture prompts add "without wearing it" (scene 1) and "Do not put the sweatshirt on him" (scene 3). He wears a navy overshirt in scenes 1, 3 and 5, and the charcoal sweatshirt only in B-roll scene 4. `POSES` has no pose for a worn product. Cause: plan. Severity: med.
- **The ad is 5.5 s short of 30 s and nobody says so** — estimate 25.2 s (63 words ÷ 2.5 w/s), "fits your 30-second target"; final `SAYS`: "It runs 24.5 seconds". Cause: plan. Severity: low.
- **The price line is saved by 0.12 s** — `$108` token ends at 2.68 s, speech ends at 3.16 s (`silencedetect`), and the cut ends at 3.28. Cause: assembly. Severity: low (risk, not seen).

## Loud failures
None.

## First-run fixes, checked here
- Price cut: **holds** (0.12 s spare).
- Fake product in portrait: **holds**. No product in `person_looks` or in the portrait.
- Captions from the script: **holds**. The transcript heard "one hundred percent" and "vintage inspired"; the captions read "100%" and "vintage-inspired".
- B-roll keeps its whole clip: **holds**. Scene 2 plays 5.6 s (asked 5.83 s), scene 4 plays 5.14 s (asked 5.5 s).
- Length allowance: one-sided (above).
- Pose by size: works as coded, but has no pose for clothing (above).
- Voice gender: **holds** ("A man's voice."; median ~98 Hz).
- ASCII voice description: not tested.
- HeyGen Avatar IV: **holds** (3 talking clips).

## Waste
- 4 producer turns only narrate one finished clip.
- Photo 9 is a copy of photo 1 (cdn.shopify.com URL). Cause: page.

## Bottom line
The cross-sell leak from the first run is back, now in 1 line, and the fact check still can't tell a cross-sell block from the product. The new pose-by-size rule has no pose for clothing, so the man holds the sweatshirt up by his face instead of wearing it.
