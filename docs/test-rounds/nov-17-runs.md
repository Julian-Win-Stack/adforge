# Nov 17 runs: the fixed 15 again, plus 15 new products (November 17, 2026)

## What this round tested

- Main 21797ff, after four untested changes since regression-3 (Nov 14): no unneeded owner questions (#668, #669), selling lines (#665), the whole product in the starting picture, and a smaller clip judge that checks shape, colour and extra pieces.
- **regression-4:** the 15 fixed eval products (`backend/evals/regression`), all 15 at once on the Azure machine. 15 of 15 ads made, $163.50 (including $45.66 lost when fal ran out of money mid-run and the run restarted).
- **fresh-15-1:** 15 products never used in an eval run before (Allbirds Tree Runners, Fellow Stagg EKG, Glossier Balm Dotcom, Touchland Power Mist, Magic Spoon, Peak Design Everyday Sling, Baggu Standard, Wild One Walk Kit, Bala Bangles, Material Kitchen board, Vuori Ponto jogger, Tower 28 SOS spray, Stanley Quencher 40 oz, Truff, Ruggable). 15 of 15 made, $109.79.
- The eval judge ran on both runs after filming ($89.43 on Azure). This was its first test on ads it was never tuned on.
- Graded on four grading pages: whole ads, plus taps and notes on the clips he chose. Quality was not graded.

## Scorecard

| Measure | regression-4 | fresh-15-1 |
|---|---|---|
| Whole ads that work | 9 of 15 (60%) | 12 of 15 (80%) |
| Same 14 ads as regression-3 | 8 of 14 (regression-3: 11 of 14) | — |
| Final clips that work (B-roll and talking together) | 51 of 58 | 54 of 58 |
| Good ad (quality) | not graded | not graded |

- **Grading rule for this round (Julian, Nov 17):** anything he didn't tap or note is a pass. On Nov 18 he chose that his tap wins: notes on clips or ads he passed stay notes, not fails. This differs from the strict rule in [the summary](README.md#how-it-was-graded); under the strict rule fresh-15-1 would be 8 of 15.
- regression-4 against regression-3: Ridge, Owala, Crocs and Brooklinen got worse; Bissell got better. Each ad was made once, so one ad flipping can be chance.
- Manscaped was not a full ad in regression-3, so the like-for-like count uses 14 ads.

## Every failure, and what happened to it

| # | Failure | Where | Bar | Status |
|---|---|---|---|---|
| 1 | Hands or bodies break physics: "the hand is breaking physics", "The hand fails", "the hand position breaks physics", "The hand breaks physics", the foot going into the shoe, the backward-to-forward walk, "the hand gets so big and the bed and the sheet become so small" | Ridge s2, Owala s1, Crocs s1, Brooklinen s2, Material Kitchen s1, Stanley s1, Allbirds s3 (7 of 11 failed clips) | Works | Not fixed (fix thread started Nov 18) |
| 2 | A retry repeats the same failed action: "Why is the agent still keep doing the same action when it has already failed for two times?" | Owala s2, all 3 takes failed | Works | Not fixed (fix thread started Nov 18) |
| 3 | Logo comes and goes, in a different colour and place from the real one. His rule: wrong letters are fine, wrong placement, shape or colour is not | Stanley s2 | Works | Not fixed (fix thread started Nov 18) |
| 4 | Things appear or vanish on their own: strands animate out of nowhere ("why the animations occur?"), "The water disappear suddenly in the end" | Furbo s3, Gorilla Glue s3 | Works | Not fixed (fix thread started Nov 18) |
| 5 | Starting picture failed, no words given | Stanley s1 picture | Works | Not fixed |

Notes on things he passed (kept as notes, not fails): the price said in the Peak Design ad ($159.95) doesn't match the page ($99); the top label covers faces (Baggu, "and in other ads too"); too little movement in B-roll (Vuori, Gorilla Glue s2); the page never shows the spray that Touchland and Tower 28 clips show; Owala s3's line and action don't quite match ("this is an annoyance"); Crocs s1 takes 1 and 2 "The flaw is real but the users wouldn't care."; Stanley s3 "Do cup holders exists on this place of the cars?"; Allbirds s2 (a laced shoe going on hands-free) was asked about in chat, not graded.

## The eval judge on new ads

| | regression-4 | fresh-15-1 | Both |
|---|---|---|---|
| Failed clips it flagged | 2 of 7 | 1 of 4 | 3 of 11 |
| Its flagged clips that Julian failed | 2 of 6 | 1 of 9 | 3 of 15 |

- Julian tapped none of its flags, so each flag took his grade of that clip: a passed or untapped clip means the flag was wrong; a failed clip with the same fault in his words means it was right.
- It missed every hand failure except Stanley s1, and the Stanley logo.
- Its own trust bar is 8 of 10 caught and 7 of 10 flags right (`evals/judge/scoring.py`). It is far below both.
- Snow: he passed the ad and didn't tap the navy-mouthpiece flags, though he ruled the same navy mouthpiece a fail on Nov 13. Counted as a pass here. Counted as a fail, the judge would reach 6 of 14 caught and 6 of 15 right.
- Not judged (Azure content filter): Manscaped s2 (8 questions) and Wild One's product questions (9).

## Caveats

- One ad per product, so single ads flipping between rounds can be chance; the hand pattern across 7 clips is the stronger signal.
- Nine failed whole ads and the Stanley s1 picture have no words, so `file-grades` leaves them unfiled and neither run can be promoted to a baseline until they get words.
- fresh-15-1 ran from a separate checkout whose product list was changed locally; those 15 products are now used and stay out of the 30-product exam.
