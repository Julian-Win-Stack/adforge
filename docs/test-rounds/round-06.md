# Round 6: 22 products, 7 of them repeats (October 20, 2026)

**Result: 12 of 19 ads worked.** 15 of 19 by his taps alone. Product shots in the final ads: about 24 of 30 worked. 3 products made no ad.

## What we tried
- 22 products: 15 new, 7 repeats from [round 5](round-05.md). 19 made an ad.
- New: an AI that remade failed clips (the remake AI), product videos read by the planning step, and general planning rules.
- Run on Julian's own Mac. Crashes and a restart of the background worker got in the way; 11 ads needed a fresh restart.

How we counted: any complaint counts as a fail. See [the summary](README.md#how-we-grade). "About 24 of 30" because the data doesn't always say which attempt ended up in the ad. New products: 5 of 13 passed. Repeats: 3 of 6. Status words: [here](README.md#status-words).

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Gymreapers: no product shots, all talking shots | Page reads hit a rate limit; later found the photo picker dropped in-use photos of another colour | Spaced runs out; re-read bad pages; keep every gallery photo whatever its colour | Came back |
| Glow Recipe repeat: all talking shots again | The clip checker (an AI that passes or fails each clip) failed every serum clip, so scenes turned into talking shots | Keep the last product shot instead ([#208](https://github.com/Julian-Win-Stack/adforge/issues/208)) | Fixed (not tested yet) |
| Glow Recipe: serum grows into a thick pink patch instead of thinning | The video model makes up how contents look; it never saw this gel | Send reference pictures instead of a starting picture | Known limit |
| Cocolab repeat: "Not how the product works... Every clip fails" | Invented use; wrong starting picture; prompt too complicated | Starting picture removed | Came back |
| Pointless product shots that prove nothing (Wet Brush, Beautyblender, Chipolo) | The planning step picks scenes that don't sell | Proposed a rule "every scene must sell"; not built here | Not fixed |
| Wet Brush: brushing looks AI-made, ad failed | The video model can't move a brush through hair | Recorded as a video model limit | Known limit |
| Beautyblender: foundation spreads too fast | The video model makes contents move too fast | Recorded as a video model limit | Known limit |
| Clip checker failed 9 of 31 clips Julian passed, plus 3 untapped remakes (Zulay, Legion, Olipop, Beautyblender) | Its bar was too strict; another AI model fixed only 4 of 14 wrong calls | Ask each check once ([#207](https://github.com/Julian-Win-Stack/adforge/issues/207)) | Came back |
| Clip checker passed 5 of 14 clips Julian failed (4 were kept remakes) | It has no "looks fake / not real use" bar | Nothing in this period | Not fixed |
| Legion: unnatural shaking, kept remakes still bad | Video model motion fault | Recorded as a video model limit | Known limit |
| KraveBeauty: faucet on the wrong side in the starting picture (a still image the video model starts from) | The picture model never saw the real sink; wrong in 8 of 19 scenes | His note fixed it; starting picture later removed | Partly fixed |
| KraveBeauty: 2 of 4 fix notes failed with a wrong "page blocked" message | A safety filter false alarm, labelled wrongly | Two retries per filter stop; clearer errors | Fixed (not tested yet) |
| Force of Nature: presenter holds a wrong-shaped bottle; talking shots can't be fixed by note | Starting picture drew the product wrong; no note fix for talking shots | Later, one AI per scene owns talking shots too | Fixed (not tested yet) |
| Dexas: asked to show paws being cleaned; app refused twice | Note fixes couldn't turn a talking shot into a product shot | Later the chat got scene-editing tools | Fixed (not tested yet) |
| Native Pet repeat: "Nothing broke, but not a great ad" | Ad quality wasn't a goal yet | Became the focus from round 11 to 12 | Not fixed |
| 3 products made no ad (Peace Out, Pair of Thieves, Blueland): every photo refused as wrong colour | A second page read wiped the photos' colour marks | Page reads only add photos; colour marks are advice ([#206](https://github.com/Julian-Win-Stack/adforge/issues/206)) | Fixed (proven later) |
| Killed steps left ads stuck (4 remakes never checked; 11 ads restarted) | Scenes marked done too early; dead steps stayed "running" | Steps with no heartbeat for 5 minutes are failed | Fixed (not tested yet) |
| "Assemble again" in chat never assembled the ad (2 ads) | A fix with no scene change woke nothing | Julian skipped it | Known limit |

## What we learned
- The clip checker's bar is the problem, not its eyes. It is wrong both ways.
- Some motions (brushing hair, shaking, spreading) the video model just can't do yet.

---

← [Round 5](round-05.md) · [All rounds](README.md) · [Round 7](round-07.md) →
