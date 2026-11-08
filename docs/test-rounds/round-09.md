# Round 9: Every scene filmed on the video model (November 4–5, 2026)

## What this round tested

- Same 15 products as [round 7](round-07.md).
- Every scene, talking ones too, now filmed by Boreal-H3 with its own voice. The presenter-video service was dropped.
- Also: face swap back on starting pictures, the helper compares new and old starting pictures, plans without prices, and only the product check can fail a clip.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 12 of 15 (80%) | 14 of 15 |
| Good ad (quality) | not graded (3 named too long) | — |
| B-roll clips in the final ads that work | 3 of 3 (100%) | — |
| Talking clips in the final ads that work | 54 of 56 (96%) | — |
| Every take shown (incl. failed tries) | 87 of 104 | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers (for example clip 3839) are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Ads too long; he wants no more than 30 s, 35 s at most (Peace Out, Krave, Beautyblender). | Named on 3 ads; 14 of 15 ads ran 37-46 s | Good ad | Fixed (proven in a later round) |
| 2 | The judge said 'unsure' on a clip where the video model turned the cleanser into white lather nobody asked for, so it went into the ad (Krave clip 3896). | 1 clip, in the final ad | Works | Came back |
| 3 | The foundation on her face came out wrong (Beautyblender scene 2: clips 3839, 3903, plus 3946 and 3975 tapped fail). | 4 tries in 1 scene (scene given up); scene 3 also given up | Works | Partly fixed |
| 4 | The real woman from the shop's page picture was being sent toward the video model (Beautyblender clip 3839). | 1 scene (Beautyblender scene 2; likely scene 3 too) | Works | Dropped |
| 5 | 'Product' versus 'products' in the spoken line should be okay (Beautyblender clips 3903, 4014). | 2 clips | Works | Fixed (not tested yet) |
| 6 | The spoken line was obviously wrong (MudBuster clip 3876). | 1 clip (part of 24 word-check fails in the round) | Works | Fixed (not tested yet) |
| 7 | The same failure happened twice in a row; 'there might be something... In the input that we send to video model' (MudBuster clips 3905, 3937). | 2 clips (MudBuster scene 1 failed 3 of 3 on the same picture and line, then gave up) | Works | Fixed (not tested yet) |
| 8 | The paw-washing clip invents how the product is used (twist it, or move it up and down the dog's leg), said and shown, though no product page says so (MudBuster clip 3936, in the final ad). | 1 clip in the final ad (round 8's twist try 3623 was the same habit) | Works | Known limit |
| 9 | 'What actually failed in here? I don't get it' on clips that looked fine (clips: MudBuster 3986, 4000, 4012, 4021; Logitech 3993). | 5 good takes thrown away, 2 scenes given up | Works | Fixed (not tested yet) |
| 10 | The agent only tuned the prompt for tries 1-4; after a second similar failure it should make a bigger change (end picture, new clip, an action that hides the fault). Creatify fixed a misplaced logo by changing the starting picture so the hand holds the brush over it (Logitech clip 3943; 3769, 3856, 3913 tapped fail). | 4 tries in 1 scene; same-fault repeats in 7 scenes | Works | Came back |
| 11 | The sock brand name 'BOWO' was heard as 'BOOHOO'; it should pass, no one will notice (Pair of Thieves clip 3862; 4003 and 4024 'not that bad'). | 3 clips; the scene was removed and the ad lost its closing line | Works | Fixed (not tested yet) |
| 12 | Garject fails again; 'this product is normal to fail' (Garject). | 1 ad | Works | Dropped |
| 13 | Round note: 'almost all the ads pass. It's just because we didn't film the risky and hard actions.' | Whole round (about 3 in 4 scenes only hold or show the product) | Good ad | Known limit |
| 14 | Why was the judge 'unsure' on a fine clip? (Peace Out clip 3826). | 1 clip (also Beautyblender clip 4019, Pair of Thieves clip 4003) | Works | Dropped |

Details, in the same order:

### 1. Ads too long; he wants no more than 30 s, 35 s at most (Peace Out, Krave, Beautyblender).

**Fixed (proven in a later round)** · Named on 3 ads; 14 of 15 ads ran 37-46 s

- **Why it happened:** With no length given, the planner had to plan 3-4 scenes of 20-25 words (8-10 s each), and each clip adds a second after the line, so every ad came out about 42 s.
- **What was changed:** Default ad aims at 30 s and never goes over 35 s, using shorter lines (12-16 words) so all 3-4 scenes stay; an owner-named length still wins. Later: a named length is measured in whole clips, and lines are planned at 2.8 words a second. (#369, #383, #420)
- **What happened next:** Round 11: ads ran 30-35 s and Julian did not call any too long (he did call one 'too slow', a pace problem fixed separately).

### 2. The judge said 'unsure' on a clip where the video model turned the cleanser into white lather nobody asked for, so it went into the ad (Krave clip 3896).

**Came back** · 1 clip, in the final ad

- **Why it happened:** When the judge decided the shop photos never show that state, code skipped the product check and kept the clip as 'unsure'; the scene helper counts unsure as a pass.
- **What was changed:** The judge now also says whether the scene asked for that state. If not, the product check runs as on any clip. Later, an edited starting picture keeps the earlier photos for the checks. (#369, #377, #378)
- **What happened next:** Round 10 Krave clip 4137: invented content again with the judge unsure (Julian: 'the judge should definitely fail this'), caused by the dropped tube photo; fixed by #378. Not graded since.

### 3. The foundation on her face came out wrong (Beautyblender scene 2: clips 3839, 3903, plus 3946 and 3975 tapped fail).

**Partly fixed** · 4 tries in 1 scene (scene given up); scene 3 also given up

- **Why it happened:** The video model can't show makeup blending into skin from words alone; the same fault happened in round 8.
- **What was changed:** End pictures: after a product-check fail, the scene helper can draw the finished state (makeup blended) and send it as the clip's last frame; prompts may back an order with a short 'no X'. Later the tool is open from the first try. (#370–#374, #375, #470)
- **What happened next:** End-picture test on these exact scenes: both start-and-end-picture takes passed on Beautyblender scene 2 and on scene 3; positive-only wording failed on scene 2. Round 10: Beautyblender scene 3 passed on try 3 with end pictures, Beautyblender scene 2 gave up again; Julian 'it works' (Nov 5 04:10).

### 4. The real woman from the shop's page picture was being sent toward the video model (Beautyblender clip 3839).

**Dropped** · 1 scene (Beautyblender scene 2; likely scene 3 too)

- **Why it happened:** The face swap restored after round 8 skips frames taken from page videos, so the woman's face went to the picture drawer unswapped. She did not reach the video model.
- **What was changed:** Left as is.
- **What happened next:** No later graded round has checked it yet.

### 5. 'Product' versus 'products' in the spoken line should be okay (Beautyblender clips 3903, 4014).

**Fixed (not tested yet)** · 2 clips

- **Why it happened:** The word check compared transcript text and was told to fail any different name or claim, so a plural slip failed while a worse garble passed.
- **What was changed:** Small slips a listener wouldn't notice pass; captions show the words as said. (#369, #433)
- **What happened next:** Round 11 grades raise no word-check complaint; no round has counted word-check results against his grades.

### 6. The spoken line was obviously wrong (MudBuster clip 3876).

**Fixed (not tested yet)** · 1 clip (part of 24 word-check fails in the round)

- **Why it happened:** The video model often adds garbled words before the line (14 of 24 word fails), then says the line right.
- **What was changed:** Code cuts junk words before the line using the transcript's word times, then re-checks; no new take is paid. The prompt's speech part was rewritten to describe the voice once. (#369)
- **What happened next:** Round 11 grades raise no spoken-line complaint.

### 7. The same failure happened twice in a row; 'there might be something... In the input that we send to video model' (MudBuster clips 3905, 3937).

**Fixed (not tested yet)** · 2 clips (MudBuster scene 1 failed 3 of 3 on the same picture and line, then gave up)

- **Why it happened:** Mostly the video model's random garbled lead-in. Our input was proven at fault only where prompts quoted on-screen text (MUD\WTR: '$80 | Free US shipping', 'eight point four seconds'), and the voice was described twice in every prompt.
- **What was changed:** Junk before the line is trimmed; prompts may not quote any text that isn't the line (code sends the form back); the voice is described once. (#369)
- **What happened next:** Round 11 grades raise no repeat word failures.

### 8. The paw-washing clip invents how the product is used (twist it, or move it up and down the dog's leg), said and shown, though no product page says so (MudBuster clip 3936, in the final ad).

**Known limit** · 1 clip in the final ad (round 8's twist try 3623 was the same habit)

- **Why it happened:** The video model makes up how a product is used; nothing checks a spoken or shown use against the page once the line passed the fact check.
- **What was changed:** No fix. Julian's thought that Creatify might make the same mistake was kept as a note only. Listed as a known video-model habit.
- **What happened next:** No later graded round has checked it yet.

### 9. 'What actually failed in here? I don't get it' on clips that looked fine (clips: MudBuster 3986, 4000, 4012, 4021; Logitech 3993).

**Fixed (not tested yet)** · 5 good takes thrown away, 2 scenes given up

- **Why it happened:** After an owner change, the owner-change check looked only at frames, so it couldn't hear whether the new line or a left-out price was spoken; it failed 5 takes that passed the product and word checks.
- **What was changed:** The owner-change check now gets the words heard in the clip (the transcript), and small transcript slips don't count. (#369)
- **What happened next:** Not graded since: no later round ran owner changes and was graded on this.

### 10. The agent only tuned the prompt for tries 1-4; after a second similar failure it should make a bigger change (end picture, new clip, an action that hides the fault). Creatify fixed a misplaced logo by changing the starting picture so the hand holds the brush over it (Logitech clip 3943; 3769, 3856, 3913 tapped fail).

**Came back** · 4 tries in 1 scene; same-fault repeats in 7 scenes

- **Why it happened:** Nothing pushed the agent past small fixes; the video model pulled the mouse to the lens in every try whatever the prompt said.
- **What was changed:** From the 2nd product fail in a row, code adds guidance to the failed clip's result: small fixes are done, make a bigger change (new shot, failing part large and still, failing part hidden, simpler action, end picture) and say how it targets the fault. The agent decides. Round 11 added 'fix with a new picture, not new words'. (#369, #415)
- **What happened next:** Round 10 Logitech mouse scene 4: after the push, try 3 used a new tighter shot and passed. Round 11: 'why did the agent kept doing useless fixes with the prompt' (BlenderBottle scene 2), 'this is very useless' (Revlon clip 4239), 'tuning the prompt... It doesn't really work' (Chemical Guys clip 4301).

### 11. The sock brand name 'BOWO' was heard as 'BOOHOO'; it should pass, no one will notice (Pair of Thieves clip 3862; 4003 and 4024 'not that bad').

**Fixed (not tested yet)** · 3 clips; the scene was removed and the ad lost its closing line

- **Why it happened:** The word check failed any name that sounded different; then the safety filter stopped the scene helper twice and the chat agent removed scene 4.
- **What was changed:** The product's own name said a little wrong passes; captions spell it right. (#369, #429)
- **What happened next:** Not graded since on a brand-name case.

### 12. Garject fails again; 'this product is normal to fail' (Garject).

**Dropped** · 1 ad

- **Why it happened:** The garlic press's scraper and handles are beyond the video model.
- **What was changed:** Product parked since round 8.
- **What happened next:** No later graded round has checked it yet.

### 13. Round note: 'almost all the ads pass. It's just because we didn't film the risky and hard actions.'

**Known limit** · Whole round (about 3 in 4 scenes only hold or show the product)

- **Why it happened:** Scenes avoided demos the video model can't do, so ads held up but showed little.
- **What was changed:** Recorded as a known limit ('Don't film actions the video model can't do'); end pictures were added to make hard demos possible; round 12's quality work raised B-roll to 45% of ad time. (#375, #402–#421)
- **What happened next:** Round 11: 'why no b roll?... The quality is really bad' (Bondi Sands). Round 12: 0 of 12 ads passed quality.

### 14. Why was the judge 'unsure' on a fine clip? (Peace Out clip 3826).

**Dropped** · 1 clip (also Beautyblender clip 4019, Pair of Thieves clip 4003)

- **Why it happened:** The safety filter refused every frame, so the judge never saw the clip; 'unsure' there means 'not looked at'.
- **What was changed:** Filter-blocked clips left alone. Separately, the 'please watch this clip' note became clickable, opening the clip with optional Looks right / Looks wrong buttons. (#369)
- **What happened next:** No later graded round has checked it yet.


## Left open

- The judge said 'unsure' on a clip where the video model turned the cleanser into white lather nobody asked for, so it went into the ad (Krave clip 3896).
- The foundation on her face came out wrong (Beautyblender scene 2: clips 3839, 3903, plus 3946 and 3975 tapped fail).
- The paw-washing clip invents how the product is used (twist it, or move it up and down the dog's leg), said and shown, though no product page says so (MudBuster clip 3936, in the final ad).
- The agent only tuned the prompt for tries 1-4; after a second similar failure it should make a bigger change (end picture, new clip, an action that hides the fault). Creatify fixed a misplaced logo by changing the starting picture so the hand holds the brush over it (Logitech clip 3943; 3769, 3856, 3913 tapped fail).
- Round note: 'almost all the ads pass. It's just because we didn't film the risky and hard actions.'

## Caveats

- From this round on, demo scenes where the presenter speaks while using the product count as "talking". Only 3 pure B-roll scenes ran, so the B-roll number is tiny.
- Julian's own note: "almost all the ads pass" because risky, hard actions were not filmed.
- 14 of 15 ads ran 37–46 seconds; he named only 3 as too long, so only those count.

---

← [Round 8](round-08.md) · [All rounds](README.md) · [Round 10](round-10.md) →
