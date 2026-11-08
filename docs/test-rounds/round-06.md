# Round 6: 22 products, 7 of them reruns (October 20, 2026)

## What this round tested

- 22 products (15 new, 7 reruns from round 5); 19 made an ad.
- New products: KraveBeauty cleanser, Beautyblender makeup sponge, Wet Brush hair brush, Dreamfarm Garject garlic press, Zulay milk frother, Gymreapers lifting straps, Dexas MudBuster dog-paw washer, ezpz baby mat, Logitech Pebble mouse, Chipolo tracker tag, Legion Pulse shaker and pre-workout, MUD\WTR coffee-alternative kit, Force of Nature cleaner, Peace Out pore strips, Pair of Thieves socks. Reruns: Glow Recipe, Native Pet, Cocolab, Loop, Olipop, Rothy's, Blueland.
- New in the app: the fixing agent (an AI that remakes a failed clip), product videos read for the planner, general planner rules.
- Run on Julian's own Mac. Crashes and a restart of the background worker disturbed it; 11 ads needed a fresh restart.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 12 of 19 (63%) | 15 of 19 |
| Good ad (quality) | not graded (named on 7 ads) | — |
| B-roll clips in the final ads that work | about 24 of 30 (80%) (see first caveat) | — |
| Every take he judged (incl. remakes) | 30 of 54 | 32 of 47 tapped |
| Talking clips | not graded (2 named: 1 works) | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words).

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Gymreapers ad had no B-roll at all, all talking ('i found that this one had scraping problem for some reasons') | 1 of 19 ads | Works | Came back |
| 2 | Glow Recipe rerun was all talking again ('all talking, don't know why') | 1 of 19 ads (3 B-roll scenes lost) | Works | Fixed (not tested yet) |
| 3 | Glow Recipe scenes 2-4: the serum grows into a thicker pink patch instead of thinning (he first called the judge too strict, then on Oct 24 agreed the judge's fails stand); scene 4's first clip and remake 'wouldn't be good' | 3 scenes, 3 clips tapped (8 tries made) | Works | Known limit |
| 4 | Cocolab scene 4 (rerun): 'the motion, how it is used, is just bad... Not how the product works... Every clip fails' | 1 scene, 2 clips tapped (4 made) | Works | Came back |
| 5 | Pointless B-roll that proves nothing: Wet Brush scene 2 ('doing nothing... Pointless'), Beautyblender scene 3 ('what's the point of the clip?'), Chipolo scene 2 ('just showing the map') | 3 clips in 3 ads | Good ad | Not fixed |
| 6 | Wet Brush: brushing motion looks AI-made and not how real life works (scene 3 kept remake, scene 5), plus the ad tapped fail | 2 clips (scene 3 and scene 4 first clips he agreed the judge failed); ad failed | Works | Known limit |
| 7 | Beautyblender scene 2 first clip: the foundation spreads unnaturally fast | 1 clip | Works | Known limit |
| 8 | The judge wrongly failed clips Julian passed, so good clips were remade or dropped: Zulay scene 2/scene 3/scene 4 ('i don't see any problems'), Legion scene 2, Olipop scene 3, Beautyblender scene 3, Beautyblender scene 2 remakes 1-3 ('they all should pass, but the judge failed them') | 9 of 31 tapped passes, plus 3 untapped remakes | Works | Came back |
| 9 | The judge passed clips Julian failed, mostly kept remakes that look AI-made: Legion scene 2 kept + remake 1 (shaking not natural), Legion scene 3 kept, Wet Brush scene 3 kept and scene 5, Cocolab scene 4 kept | 5 of 14 clips he failed (4 were kept remakes) | Works | Not fixed |
| 10 | Legion scenes 2 and 3: unnatural shaking and the kept remakes still bad | 1 ad, 3 clips (1 untapped remake) | Works | Known limit |
| 11 | KraveBeauty scene 3: the faucet is on the wrong side (starting picture) | 1 clip | Works | Partly fixed |
| 12 | His second and third fix notes on KraveBeauty failed: a safety filter blocked the fixing agent's first call and the message wrongly said the product page was blocked / start a new session; he also asked 'why taking too long' | 2 of 4 notes on 1 scene | Works | Fixed (not tested yet) |
| 13 | Force of Nature: a talking scene shows the presenter holding a different-shaped bottle, and talking scenes can't be fixed with owner notes | 1 talking scene | Works | Fixed (not tested yet) |
| 14 | Dexas scene 4: he asked for the dog's paws being cleaned instead of the presenter; the app refused twice ('A person can't do an action while talking yet') | 1 scene, 2 notes | Good ad | Fixed (not tested yet) |
| 15 | Native Pet rerun: 'Nothing broke, but not a great ad' | 1 ad | Good ad | Not fixed |
| 16 | Three products made no ad at all (Peace Out, Pair of Thieves, Blueland rerun): every page photo was refused as 'not in the ad's colour' | 3 of 22 products | Works | Fixed (proven in a later round) |
| 17 | Killed steps left ads stuck (judge never ran on some remakes; scenes marked finished too early; assembly crashed); Julian: 'why didn't the judge run for some of the things... Just rerun it' | 4 remakes unjudged; 11 ads needed a fresh restart | Works | Fixed (not tested yet) |
| 18 | 'Assemble again' asked in chat never assembled the ad | 2 ads (chili crunch, Rothy's fresh) | Works | Known limit |

Details, in the same order:

### 1. Gymreapers ad had no B-roll at all, all talking ('i found that this one had scraping problem for some reasons')

**Came back** · 1 of 19 ads

- **Why it happened:** Round 6: page reads hit the page-reading service's rate limit (10 reads a minute) at a 6-second start gap, so page reads and marked screenshots fell back. Round 7 found the deeper cause for this product: the photo picker dropped the in-use photos because they showed the straps in another colour, so the planner had no in-use picture and asked the owner.
- **What was changed:** Rate limits written down and runs spaced out; a page is read again fresh when the first read can't be used (Oct 30); the photo picker keeps every picture in the product's own gallery whatever its colour (Oct 30).
- **What happened next:** Round 7 Gymreapers still lost its in-use photos (found Oct 29), which led to the Oct 30 picker fix.

### 2. Glow Recipe rerun was all talking again ('all talking, don't know why')

**Fixed (not tested yet)** · 1 of 19 ads (3 B-roll scenes lost)

- **Why it happened:** The judge failed every serum try (gel grows instead of thinning), and the app's rule turned a scene the judge kept failing into a talking scene.
- **What was changed:** When the fixing agent gives up, the ad keeps its last B-roll try instead of switching to talking; the owner can swap in any try for free. (#208)
- **What happened next:** Glow Recipe was not in round 7; no later grade checks this.

### 3. Glow Recipe scenes 2-4: the serum grows into a thicker pink patch instead of thinning (he first called the judge too strict, then on Oct 24 agreed the judge's fails stand); scene 4's first clip and remake 'wouldn't be good'

**Known limit** · 3 scenes, 3 clips tapped (8 tries made)

- **Why it happened:** The video model invents how contents look; it was never shown how this gel looks on skin (no reference picture of the gel).
- **What was changed:** Show, don't tell: the start picture was deleted and the video model is sent reference pictures instead (spec #210, built Oct 28-29). Recorded as a video-model limit: the video model 'makes up what contents look like (foam, foundation, serum)'.
- **What happened next:** Not re-tested on Glow Recipe; contents-look faults stay listed in the video-model limits list.

### 4. Cocolab scene 4 (rerun): 'the motion, how it is used, is just bad... Not how the product works... Every clip fails'

**Came back** · 1 scene, 2 clips tapped (4 made)

- **Why it happened:** Planner/prompt invented the use; Oct 24 page notes add 'the starting pic is just wrong' and 'the video generation prompt is too complicated'.
- **What was changed:** Start picture removed (spec #210); prompt simplification proposed then deleted as a rule (Nov 2). Invented use is listed as the video model's most common fail.
- **What happened next:** Round 7: invented use again on other products (Peace Out extractor tool, Dexas twisting).

### 5. Pointless B-roll that proves nothing: Wet Brush scene 2 ('doing nothing... Pointless'), Beautyblender scene 3 ('what's the point of the clip?'), Chipolo scene 2 ('just showing the map')

**Not fixed** · 3 clips in 3 ads

- **Why it happened:** A planner problem, not a judge one: the planner picks scenes that don't sell (Oct 22 count: 0 plan fails tapped, 3 passed clips called pointless).
- **What was changed:** Proposed Oct 22 as planner rule 'every scene must sell' (weeks 6-8 of the plan). No fix tied to this complaint found in this period; later ad-quality work (Nov 6-7, e.g. #421, #438) may cover it but I did not check it against these clips.
- **What happened next:** No later graded round has checked it yet.

### 6. Wet Brush: brushing motion looks AI-made and not how real life works (scene 3 kept remake, scene 5), plus the ad tapped fail

**Known limit** · 2 clips (scene 3 and scene 4 first clips he agreed the judge failed); ad failed

- **Why it happened:** The video model can't move a brush through hair realistically.
- **What was changed:** Recorded as a video-model limit; Julian gave up on the product in round 7 ('I give up for this one... Give it a reference video').
- **What happened next:** Round 7 Wet Brush failed again ('It breaks the physics').

### 7. Beautyblender scene 2 first clip: the foundation spreads unnaturally fast

**Known limit** · 1 clip

- **Why it happened:** Video model makes contents behave too fast (listed under the video model physics faults).
- **What was changed:** Recorded as a video-model limit (the video-model limits list cites 'R6 beautyblender scene 2 (too fast)').
- **What happened next:** Round 7 Beautyblender: he said the clips were good and the judge was wrong.

### 8. The judge wrongly failed clips Julian passed, so good clips were remade or dropped: Zulay scene 2/scene 3/scene 4 ('i don't see any problems'), Legion scene 2, Olipop scene 3, Beautyblender scene 3, Beautyblender scene 2 remakes 1-3 ('they all should pass, but the judge failed them')

**Came back** · 9 of 31 tapped passes, plus 3 untapped remakes

- **Why it happened:** A bar problem (how bad a fault must be to fail), not the model's eyes: a Gemini test fixed only 4 of 14 wrong calls.
- **What was changed:** Judge asks each check once and the same-fault vote is dropped; the Gemini swap was tested and not taken. (#207)
- **What happened next:** Round 7 Beautyblender: 'all the clips... Are actually good... The judge make the wrong judgment'.

### 9. The judge passed clips Julian failed, mostly kept remakes that look AI-made: Legion scene 2 kept + remake 1 (shaking not natural), Legion scene 3 kept, Wet Brush scene 3 kept and scene 5, Cocolab scene 4 kept

**Not fixed** · 5 of 14 clips he failed (4 were kept remakes)

- **Why it happened:** The judge has no 'looks fake / not how it is really used' bar; it only sees frames.
- **What was changed:** No fix in this period; a 'used as in real life' question was tested (missed its targets). Later work: 'Make quality checks agree with Julian' (Nov 7-8).
- **What happened next:** No later graded round has checked it yet.

### 10. Legion scenes 2 and 3: unnatural shaking and the kept remakes still bad

**Known limit** · 1 ad, 3 clips (1 untapped remake)

- **Why it happened:** Video model motion fault (the video model lists 'R4 phone case scene 2 (shaking)').
- **What was changed:** Recorded as a video-model limit.
- **What happened next:** Round 7 Legion: not graded by clip in the export.

### 11. KraveBeauty scene 3: the faucet is on the wrong side (starting picture)

**Partly fixed** · 1 clip

- **Why it happened:** The starting picture model never saw the real sink layout; wrong starting pictures in 8 of 19 round-6 scenes.
- **What was changed:** His own note fixed it in the app the first time (fixing agent redrew the picture and remade the clip, judge and fix check passed). Later the B-roll starting picture was deleted (spec #210). (#191)
- **What happened next:** Fixed for this ad by note (#1). Round 7 still had picture-input problems (background and person leaking from reference pictures, Chipolo scene 3).

### 12. His second and third fix notes on KraveBeauty failed: a safety filter blocked the fixing agent's first call and the message wrongly said the product page was blocked / start a new session; he also asked 'why taking too long'

**Fixed (not tested yet)** · 2 of 4 notes on 1 scene

- **Why it happened:** False positive from the image safety filter; the app labelled the error wrongly.
- **What was changed:** Each turn the safety filter stops gets two retries; what the filter refused once is made again; owner-facing errors rewritten.
- **What happened next:** Not checked by a later round.

### 13. Force of Nature: a talking scene shows the presenter holding a different-shaped bottle, and talking scenes can't be fixed with owner notes

**Fixed (not tested yet)** · 1 talking scene

- **Why it happened:** The talking scene's starting picture drew the product wrong, and the app had no way to remake a talking scene from a note.
- **What was changed:** Later, scene helpers own talking scenes in the video model-voiced ads (Nov 3); not checked against this case. (#333)
- **What happened next:** Not checked by a later round.

### 14. Dexas scene 4: he asked for the dog's paws being cleaned instead of the presenter; the app refused twice ('A person can't do an action while talking yet')

**Fixed (not tested yet)** · 1 scene, 2 notes

- **Why it happened:** The note-fix planner could not turn a talking scene into a B-roll action.
- **What was changed:** Later the agentic chat agent got scene-editing tools and scene helpers own talking scenes; not checked against this case. (#254, #261, #333)
- **What happened next:** Round 7 Dexas: graded on invented twisting, not on this.

### 15. Native Pet rerun: 'Nothing broke, but not a great ad'

**Not fixed** · 1 ad

- **Why it happened:** Ad quality was not yet a goal of the app; only 'does it break' was checked.
- **What was changed:** No fix in this period; ad quality became the focus from round 11-12 (Nov 6).
- **What happened next:** No later graded round has checked it yet.

### 16. Three products made no ad at all (Peace Out, Pair of Thieves, Blueland rerun): every page photo was refused as 'not in the ad's colour'

**Fixed (proven in a later round)** · 3 of 22 products

- **Why it happened:** A second page read wiped the photos' colour marks after planning (Peace Out, Blueland); Pair of Thieves' only photo was a pack line-up the planner could not name.
- **What was changed:** A page read only adds photos and never wipes marks; colour marks became advice, not a gate. (#206)
- **What happened next:** Round 7: Peace Out and Pair of Thieves ads were made (both in round 7's ad list).

### 17. Killed steps left ads stuck (judge never ran on some remakes; scenes marked finished too early; assembly crashed); Julian: 'why didn't the judge run for some of the things... Just rerun it'

**Fixed (not tested yet)** · 4 remakes unjudged; 11 ads needed a fresh restart

- **Why it happened:** A scene was marked finished when its clip was saved, before the judge and fixing ended; killed steps stayed 'running' forever; the restart cleanup crashed on one bad row.
- **What was changed:** Scene finished only when its step finishes; a watcher fails steps with no heartbeat for 5 minutes; cleanup handles each step on its own. (#206)
- **What happened next:** Not checked against a later round.

### 18. 'Assemble again' asked in chat never assembled the ad

**Known limit** · 2 ads (chili crunch, Rothy's fresh)

- **Why it happened:** A fix plan that changes no scene woke nobody, yet a tap still said 'Going ahead'.
- **What was changed:** Skipped by Julian, written as a limitation.
- **What happened next:** No later graded round has checked it yet.


## Left open

- Gymreapers ad had no B-roll at all, all talking ('i found that this one had scraping problem for some reasons')
- Glow Recipe scenes 2-4: the serum grows into a thicker pink patch instead of thinning (he first called the judge too strict, then on Oct 24 agreed the judge's fails stand); scene 4's first clip and remake 'wouldn't be good'
- Cocolab scene 4 (rerun): 'the motion, how it is used, is just bad... Not how the product works... Every clip fails'
- Pointless B-roll that proves nothing: Wet Brush scene 2 ('doing nothing... Pointless'), Beautyblender scene 3 ('what's the point of the clip?'), Chipolo scene 2 ('just showing the map')
- Wet Brush: brushing motion looks AI-made and not how real life works (scene 3 kept remake, scene 5), plus the ad tapped fail
- Beautyblender scene 2 first clip: the foundation spreads unnaturally fast
- The judge wrongly failed clips Julian passed, so good clips were remade or dropped: Zulay scene 2/scene 3/scene 4 ('i don't see any problems'), Legion scene 2, Olipop scene 3, Beautyblender scene 3, Beautyblender scene 2 remakes 1-3 ('they all should pass, but the judge failed them')
- The judge passed clips Julian failed, mostly kept remakes that look AI-made: Legion scene 2 kept + remake 1 (shaking not natural), Legion scene 3 kept, Wet Brush scene 3 kept and scene 5, Cocolab scene 4 kept
- Legion scenes 2 and 3: unnatural shaking and the kept remakes still bad
- KraveBeauty scene 3: the faucet is on the wrong side (starting picture)
- Native Pet rerun: 'Nothing broke, but not a great ad'
- 'Assemble again' asked in chat never assembled the ad

## Caveats

- The data does not always say which take ended up in the ad. Scenes whose failed takes were dropped from the ad (Logitech scene 4, Garject scene 4, Beautyblender scene 2, and Glow Recipe, which ended all talking) are left out.
- 3 of 22 products made no ad (every page photo was refused). They are not counted as ads.
- New products: 5 of 13 passed. Reruns of round 5 products: 3 of 6 (both bars together).

---

← [Round 5](round-05.md) · [All rounds](README.md) · [Round 7](round-07.md) →
