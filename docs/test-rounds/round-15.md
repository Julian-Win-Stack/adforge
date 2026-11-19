# Round 15: 20 new products at once (November 10, 2026)

**Result: 17 of 19 ads worked.** 1 more product made no ad. Cost $154.25 (estimate was $160).

## What we tried

- Same app version as [round 14](round-14.md), on 20 new products a typical online shop would advertise (Ridge, Manscaped, Snow, Laneige, Jones Road, Bombas, Hatch, Furbo, Theragun, Graza and more).
- All 20 ads made at the same time, to test the app under load.
- Two products were swapped before filming: AG1's page had no price or buy button, and the app found no product photo on the BlendJet page. Quince and Brooklinen took their places.
- Graded by Julian's tap on each whole ad. His notes on ads he passed are listed below as notes, not fails.

## What broke, and what we did

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Manscaped: both product shots failed | Trimming is a hard hand action for the video model | None specific | Not fixed |
| Snow: the product shot in scene 3 failed | Not looked into | None | Not fixed |
| Sol de Janeiro: no ad. A safety filter refused to make the presenter twice | The image filter flagged a body-cream ad | None | Not fixed |
| BlendJet: the app found no product photo on its page | Its own link sent us to a bundle page; the single-product page gave no photo the app could use | Swapped for another product | Not fixed |
| 20 ads at once hit the provider's rate limit 216 times; 2 Solawave scenes had to be remade | Too many calls at once | Outside calls now wait in a shared queue | Fixed (not tested yet) |

**Notes on ads he passed** (not counted as fails):

- Hatch, Furbo: the camera moves in to the product, then back out. "just stay moved in."
- Vacation sunscreen: she squeezes hard but the sunscreen on her hand doesn't grow. Later fixed by a rule that shows contents coming out or already out (Nov 14).
- Ridge: the first shot's background doesn't look natural.
- Brooklinen: a few scenes could be better.
- Round note: some presenters talk a bit slow, mostly in the last scene. He said it may not need fixing.

## What we learned

- 17 of 19 on products the app had never seen. The best result on new products so far.
- The clip checker passed 73 of 89 attempts, failed 10, and was unsure on 6.
- What's left is small: camera moves, product contents, and pace. Hard hand actions (trimming) still fail.
