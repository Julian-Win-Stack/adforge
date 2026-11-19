# Round 9: Every scene filmed by the video model (November 4-5, 2026)

**Result: 12 of 15 ads worked.** 14 of 15 if only Julian's pass/fail taps count. In the final ads, 54 of 56 talking shots worked; only 3 product shots ran, and all 3 worked.

Any complaint counts as a fail. See [how we grade](README.md#how-we-grade).

## What we tried
- The same 15 products as [round 7](round-07.md).
- Every scene, talking shots too, filmed by the video model with its own voice. The outside presenter-video service was dropped.
- Also new: face swap back on, new starting pictures (a still image the video model starts from) compared with old ones, no price when the user says so, and only the product check can fail a clip.
- From here on, a scene where the presenter talks while using the product counts as a talking shot.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Peace Out, Krave, Beautyblender: ads too long; he wants 30 s, 35 s at most (14 of 15 ran 37-46 s) | With no length given, the planning step made ads of about 42 s | Default is 30 s, never over 35 s, with shorter lines | Fixed (proven later) |
| Krave: the clip checker said "unsure" on white lather nobody asked for, so it went in the ad | When no photo showed that look, the product check was skipped | The checker asks if the scene wanted that look; if not, it checks as usual | Came back |
| Beautyblender: foundation came out wrong on 4 attempts; scenes 2 and 3 given up | The video model can't show makeup blending from words alone | End pictures (a still image the video model must end on) of the blended result | Partly fixed |
| Beautyblender: the real woman from the shop's photo was sent toward the video model | Face swap skips frames taken from page videos | Left as is; she never reached the video model | Dropped |
| Beautyblender: "product" vs "products" in the spoken line failed; should pass | The word check failed any small difference | Small slips a listener wouldn't notice now pass | Fixed (not tested yet) |
| MudBuster: spoken line obviously wrong (1 of 24 word-check fails) | The video model often adds garbled words before the line (14 of 24) | Junk before the line is cut, then checked again | Fixed (not tested yet) |
| MudBuster: same fail twice in a row; the scene gave up after 3 of 3 | Mostly the random garbled start; prompts also quoted on-screen text and described the voice twice | Junk trimmed; no quoting other text; voice described once | Fixed (not tested yet) |
| MudBuster: the paw clip in the final ad invents how the product is used | The video model makes up product use; nothing checks it against the page | None | Known limit |
| MudBuster, Logitech: "what actually failed in here?" on fine clips; 5 good attempts thrown away | The check of the user's changes saw only frames and couldn't hear the words | It now also gets what was said | Fixed (not tested yet) |
| Logitech: the scene AI only tweaked the prompt for attempts 1-4; same fault repeated in 7 scenes | Nothing pushed it past small fixes | After 2 product fails in a row, it is told to make a bigger change | Came back |
| Pair of Thieves: brand name "BOWO" heard as "BOOHOO"; scene removed, ad lost its closing line | The word check failed any name that sounded different | The product's name said a little wrong now passes; captions spell it right | Fixed (not tested yet) |
| Garject: failed again, which is normal for this product | Too hard for the video model | Product set aside since round 8 | Dropped |
| Whole round: "almost all the ads pass" only because hard actions weren't filmed (about 3 in 4 scenes just hold or show the product) | Scenes avoided demos the video model can't do | End pictures added to make hard demos possible; later, more product shots | Known limit |
| Peace Out: the checker said "unsure" on a fine clip | A safety filter blocked every frame, so it never saw the clip | Left alone; the user can now open the clip and mark it | Dropped |

## What we learned
- Moving every scene to the video model raised working ads from 9 to 12 of 15.
- Ads passed partly because they avoided hard actions. Holding a product up is not much of an ad.
- Most spoken-line fails were the video model's garbled start, not our input.

---

← [Round 8](round-08.md) · [All rounds](README.md) · [Round 10](round-10.md) →
