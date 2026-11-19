# AdForge test rounds

AdForge turns a product page link into a short video ad. Every few days we made a batch of ads, Julian watched and graded every one himself, and we fixed what broke. This page is the log: 15 rounds, Oct 8 to Nov 17, 2026. Early low numbers are kept as they were.

## How we grade

- **An ad works** when nothing in it is broken: right product look, real physics, readable label, right action, no made-up claims, and the app actually finished it.
- **Any complaint counts as a fail**, even if Julian tapped pass. No comment is a pass. From round 14 on, his tap decides and his notes are listed apart (his choice).
- **Good ad** (is it convincing, fast, not boring) was graded only in rounds 11 and 12. No ad passed it yet.

Words: a **product shot** shows the product in use with nobody talking to camera. A **talking shot** is an AI presenter talking to camera. The **video model** is the AI that films each shot.

## Results

| Round | Date | What we tried | Ads that worked | Product shots that worked |
|---|---|---|---|---|
| [1](round-01.md) | Oct 8 | First full ads from just a product link | 0 of 5 | 8 of 13 |
| [2](round-02.md) | Oct 9 | Fixed round 1: voice, product shots in the middle, no phone look | 3 of 3 | 11 of 11 |
| [3](round-03.md) | Oct 10 | Every product shot must prove what's being said | 1 of 4 | 7 of 13 |
| [4](round-04.md) | Oct 12–14 | 10 products, product kept on the same side, a shared "before" picture | 5 of 10 | 18 of 29 |
| [5](round-05.md) | Oct 17–19 | An AI checks each clip and remakes failed ones; 15 new products | 7 of 15 | 23 of 33 |
| [6](round-06.md) | Oct 20 | An AI that remakes failed clips; product videos on the page read | 12 of 19 | about 24 of 30 |
| [7](round-07.md) | Oct 29–31 | Page photos sent to the video model, no drawn first frame | 4 of 15 | 14 of 27 |
| [8](round-08.md) | Nov 3 | One AI per scene; a checked first frame for every shot | 9 of 15 | 26 of 27 |
| [9](round-09.md) | Nov 4–5 | The video model films every shot, talking ones too | 12 of 15 | 3 of 3 |
| 10 | Nov 4–5 | Single scenes only, no whole ads ([details](round-10.md)) | — | 8 of 11 |
| [11](round-11.md) | Nov 5–6 | 10 new products, end pictures, quality graded | 6 of 10 | 2 of 4 |
| [12](round-12.md) | Nov 6–8 | More product shots (45% of the ad), natural speaking speed, end card | 3 of 12 | 10 of 20 |
| [13](round-13.md) | Nov 9 | Round 12's fixes; same product in every scene; 3 new products | 11 of 15 | 18 of 21 |
| [14](round-14.md) | Nov 10 | Round 13's fixes on 10 new products | 7 of 8 | not graded apart |
| [15](round-15.md) | Nov 10 | 20 new products, all made at once | 17 of 19 | not graded apart |
| [Nov 17](nov-17-runs.md) | Nov 17 | Same 15 products again, plus 15 new ones | 9 of 15 and 12 of 15 | not graded apart |

- Round 9 counts demo shots where the presenter talks while using the product as talking shots, so few product shots are left.
- Rounds 14, 15 and Nov 17 were graded by tap. With "any complaint is a fail", Nov 17's new 15 would be 8 of 15.
- Products that made no ad count as failed ads, except round 6's three, which are left out ([round 6](round-06.md)).

## What the numbers show

- **It climbed, but not in a straight line:** 0 of 5 in round 1, 12 of 15 in round 9, 17 of 19 in round 15. Dips in rounds 7 and 12.
- **Round 7 was a wrong turn:** sending page photos straight to the video model leaked backgrounds and made up parts. Round 8 went back to a checked first frame: product shots went from 14 of 27 to 26 of 27.
- **Round 12 dipped because we asked for more:** three times more product shots, harder demos. More demos means more breaks.
- **Rounds 14 and 15 were all new products:** 24 of 27 ads worked. The fails left are mostly hands and physics.
- **The hardest thing is still hands:** on Nov 17, 7 of the 11 failed clips were hands or bodies breaking physics.

## Biggest problems and where they are now

| Problem | Rounds seen | Now |
|---|---|---|
| Voice cut off at the end of the ad | 1 | Fixed (proven later) |
| Product shots looked filmed on a phone for no reason | 1 | Fixed (proven later) |
| Too much talking, too few product shots | 1, 5, 6, 11, 12 | Partly fixed |
| Product shots that show nothing useful | 2, 6, 12 | Partly fixed |
| Product turns and shows a made-up side (a phone with two backs) | 1, 3, 4, 13 | Partly fixed; drops and tumbles are a known limit |
| Hair tools and brushes break physics | 1, 3, 4, 6, 7, 12, 13 | Known limit |
| Made-up way of using the product | 5, 6, 7, 9, 12 | Came back |
| Product contents made up (foam, lather, cream that doesn't grow) | 6, 8, 9, 10, 13, 15 | Came back; now filmed only when a page photo shows it |
| Labels and logos redrawn or garbled | 8, 10, 11, 12, Nov 17 | Came back; letters may be off, but place, shape and colour may not |
| Product or props change between scenes | 3, 12 | Fixed (proven later) |
| The clip checker disagrees with Julian | 4 to 9, 13, 14 | Partly fixed |
| A failed clip's retry only reworded the prompt | 7 to 11 | Fixed (not tested yet) |
| Backgrounds or strangers from page photos leaked in | 7, 8, 9 | Partly fixed |
| Ads too long, speech too slow, weak hook | 9, 11, 12, 15 | Partly fixed |
| Presenter looks pasted on or not real | 11, 12 | Fixed (not tested yet in a full round) |
| An ad made no video at all | 6, 11, 14, 15 | Partly fixed; safety-filter blocks now retried elsewhere |
| Hands, feet and straps pass through things | 12, 13, Nov 17 | Not fixed; the biggest group now |
| In a still shot something moves on its own | 12, 13 | Not fixed |
| Camera moves in, then back out | 15 | Not fixed |
| An AI gives up on a scene without trying another idea | 13 | Fixed (not tested yet) |
| No emotional "after" moment | 12 | Not fixed |

What the video model can't do, and how we work around it: [video model limits](../h3-limits.md). Known limits of the app: [limitations](../limitations.md).

## Status words

| Status | Meaning |
|---|---|
| Fixed (proven later) | A later graded round showed it stopped. |
| Fixed (not tested yet) | Changed; no graded round has checked it yet. |
| Partly fixed | Better, or fixed only for some cases. |
| Came back | Changed, then seen again. |
| Not fixed | Nothing has fixed it yet. |
| Known limit | The video model can't do it reliably, so the app avoids it. |
| Dropped | Julian chose not to fix it. |

## Smaller tests

- [Scene tests](scene-tests.md): one hard scene or one feature at a time, like fixing a scene from the user's note (6 of 11 fully fixed on Nov 18).
- [Side tests](side-tests.md): quick one-question tests between rounds.

## Read the numbers with care

- **Small batches:** 3 to 20 ads a round, so one ad can move a round a lot.
- **Products change:** rounds 7 to 9 used the same 15; rounds 11, 14 and 15 were all new.
- **Several changes per round**, so a rise or dip can't always be pinned on one change.
- **One grader, and his bar went up** over time.
