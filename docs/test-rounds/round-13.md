# Round 13: Testing round 12's fixes, plus 3 new products (November 9, 2026)

**Result: 11 of 15 ads worked** (11 of 14 made, by Julian's taps). Product shots that worked: 18 of 21. Talking shots: 27 of 27. Cost $106.36.

## What we tried
- Round 12's fixes: 6 attempts each for the starting and end pictures (stills the video model starts from / must end on), logo fixes, a natural grip, one product size in every scene, plain words for what the product is, new hooks, and two real camera setups for talking shots.
- Round 12's 12 products plus 3 new ones: LifeStraw Peak Solo, Dash Mini Waffle Maker, Bee's Wrap. All 15 ran at once on the Azure machine; 14 ads were made.
- Graded as whole ads, plus notes on clips he chose. Quality was not graded.

How we counted: any complaint counts as a fail, see [the summary](README.md#how-we-grade). The 4 failed ads: Crocs, Dash, Bondi, and KONG (stopped before filming). Hypervolt's clips are left out of the clip counts (no grading page data).

## What broke, and what we did
Status words: [the summary](README.md#status-words).

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Bondi: foam on skin stretches into strands, once turns white "like a soap" (all 3 attempts) | No page photo shows the foam on skin, so the video model made it up | Film foam or cream being applied only if the page shows it; else show the result or a dab on a fingertip. Passed on a quick test version, Nov 10 (about $10) | Fixed (passed a quick test, not a full round) |
| Crocs: heel strap passes through the ankle | Start and end pictures made the move impossible with the foot inside | A move check before filming; worked in a test, build approved Nov 9 | Not fixed |
| Dash: in a planned still shot, "the drawer just flew above" | Fixed lines told the video model to move from the first frame | Still shot as a pinned camera move; passed 2 of 2 in a test, build approved | Not fixed |
| KONG: stopped before filming | Azure's safety filter wrongly blocked the script check ($0.45 spent) | On a block, retry once with OpenAI; if still blocked, tell the user plainly ([#628](https://github.com/Julian-Win-Stack/adforge/issues/628)) | Fixed (not tested yet) |
| Revlon: hair brush breaks physics; scene took 4 attempts | The video model can't do hair tools; about 5 of 18 graded hair clips ever passed | Framing test proposed (about $5 to $6); attempt 4 passed | Known limit |
| Revlon: the video model added pink buttons to the brush handle | The hidden side of the handle isn't in any photo | None; the check caught all 3, none reached the ad | Not fixed |
| Hypervolt: checks misread which end has the holes | Checks only got the 2 photos the scene's AI picked, not the back views | Send a photo of every side of the product to all checks | Fixed (not tested yet) |
| The clip checker (an AI that watches each clip and passes or fails it) fails details nobody would notice | It can't say how visible a fault is; 4 clips Julian passed | Proposal: fail only what someone watching once on a phone would see | Not fixed |
| A scene's AI gives up without trying another action (6 times in 4 of 14 ads) | It read the main AI's instruction as fixed and had no fallback steps | A try-another-way list; first build passed Julian's test, rebuild not tested | Fixed (not tested yet) |
| Hypervolt: paid remakes ($10.33) after the user said "finish the ad with what you have" | The main AI read it as "remake from the photos" | Proposal: keep the current ad, ask before a new paid run | Not fixed |
| Grading page showed each redrawn picture with the previous image | It took the wrong picture per drawing call | Shows the right picture now | Fixed (not tested yet) |
| Bee's Wrap: scene 2 shows wrapping, not "an already wrapped one" | The main AI cut the planned wrapped shot as not selling | None yet, to ask Julian | Not fixed |

## What we learned
- Round 12's fixes for one product size, shared props, worn items and naming the product all held up.
- Moves that are impossible from the starting picture get filmed anyway, because nothing checks them first.
- Our checks only see what the scene's AI picks, so a wrong pick misleads every later check.

---

[Round 12](round-12.md) · [All rounds](README.md)
