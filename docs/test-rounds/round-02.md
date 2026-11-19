# Round 2: Same 5 products after the round 1 fixes (October 9, 2026)

**Result: 3 of 3 graded ads worked, but all 3 failed on quality.** Product shots that worked: 11 of 11. Talking shots: 6 of 6.

How we counted: only 3 of the 5 ads got a verdict (the handbag and Le Duo were never graded). See [how we grade](README.md#how-we-grade).

## What we tried
- The same 5 products as [round 1](round-01.md).
- Round 1 fixes: voice no longer cut off, every middle scene a product shot (the product in use, nobody talking to camera), camera still, product never flipped, no phone-filmed look.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Phone case and shower cleaner: product shots where a hand just holds or places the product (5 clips) | With every middle scene a product shot, the planning step filled gaps with "pick it up, set it down" | "The B-roll must do something. It must sell the product." Else ask the user, else a talking shot ([#121](https://github.com/Julian-Win-Stack/adforge/issues/121)) | Fixed (proven later) |
| Toilet cleaner: the "result" is the bottle next to an already clean toilet | Nothing said a result must be filmed happening | A result is filmed from before to after in one clip | Fixed (proven later) |
| Phone case: phone dropped from just above the floor, proves nothing | The page's 6 ft height never reached the picture | A drop starts from the stated height, whole fall in view | Fixed (proven later) |
| Shower cleaner: still no before/after photo, so no cleaning shown (2nd round) | The app asked, but the shop has no such photo | Stopped testing this product until real photos exist | Known limit |

## What we learned
- Forcing product shots in the middle works only if each one shows something that sells.
- A product with no visible result can't be sold by the video model without real photos.
