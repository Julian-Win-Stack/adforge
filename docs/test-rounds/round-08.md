# Round 8: First round with one AI helper per B-roll scene (November 3, 2026)

## What this round tested

- Same 15 products as [round 7](round-07.md). Each B-roll scene gets its own AI helper: it picks photos, draws and checks a starting picture, films from it, judges the clip, and retries up to 6 times.
- Talking scenes still came from the presenter-video service.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 9 of 15 (60%) | 12 of 15 |
| Good ad (quality) | not graded (named on 3 ads) | — |
| B-roll clips in the final ads that work | 26 of 27 (96%) | — |
| Talking clips in the final ads that work | 47 of 48 (98%) | — |
| Every take shown (incl. failed tries) | 82 of 99 | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers (for example clip 3839) are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The video model made up how the cleanser's foam looks when it is rubbed on the face (Krave, scene 4). | 1 scene, all 4 tries (Krave scene 4); its last try stayed in the ad | Works | Came back |
| 2 | The scene helper never understood why the scene kept failing and never found the root cause (Krave scene 4; Beautyblender scenes 2 and 3: 'useless changes to the prompt and the starting pic'). | 3 scenes (Krave scene 4, Beautyblender scene 2, Beautyblender scene 3), 11 tries | Works | Came back |
| 3 | The makeup (foundation) turned into flat opaque patches on the face; the scene's AI helper's fix didn't make sense and it didn't know the video model's limit with makeup effects (Beautyblender clip 3543, scene 3 tries). | 2 scenes given up (Beautyblender scene 2, scene 3); 7 tries | Works | Partly fixed |
| 4 | The real woman's face from the shop's page photo was sent to the video model; Julian thought there was a face-swap step (Krave scene 4). | 1 ad (Krave) | Works | Partly fixed |
| 5 | The clip judge was too strict: it failed clips Julian would ship. | 9 of 17 clips he graded (clips: Beautyblender 3495, 3476, 3518; Gymreapers 3433; ezpz mat 3522; Legion Pulse 3494; Pair of Thieves 3619, 3630, 3638) | Works | Fixed (proven in a later round) |
| 6 | Remove the 'does what was ordered' check; not that important (ezpz mat clip 3522). | 1 clip | Works | Fixed (proven in a later round) |
| 7 | Maybe the judge is strict because it looks at too many frames; Creatify's agent looks at 12 frames (Pair of Thieves clip 3619). | 1 note | Works | Not fixed |
| 8 | New starting pictures looked just like the old ones; the agent never checked whether a new picture actually changed anything, and a new picture always replaced the old one (Beautyblender scene 3). | 1 scene (Beautyblender scene 3, 5 tries) | Works | Fixed (proven in a later round) |
| 9 | The agent asked the owner for something already in the reference video sent to the video model (Beautyblender clip 3543; it asked for a clean page video). | 1 scene | Works | Not fixed |
| 10 | Garject ad fails as a whole; ignore this product for a while, as even Creatify's own tools struggle with it (Garject). | 1 ad | Works | Dropped |
| 11 | The product changed in the pictures given to the presenter-video service (Zulay frother). | 1 ad (Zulay) | Works | Fixed (proven in a later round) |
| 12 | Wet Brush ad failed with no words (Wet Brush). | 1 ad | Works | Not fixed |
| 13 | The letters on the pre-workout tub changed during the clip (Legion clip 3506). | 1 clip | Works | Came back |
| 14 | The judge was right on the dog-paw washer twist clip: product changed and the motion was unreal (MudBuster clip 3623). | 1 clip (replaced before the ad) | Works | Came back |
| 15 | Ezpz mat clip failed with no words (clip 3545). | 1 clip (replaced before the ad) | Works | Not fixed |
| 16 | The chat agent insisted on a price the owner had asked to leave out, then left it out anyway ('this is kinda weird', Logitech). | 1 ad (Logitech mouse) | Works | Fixed (proven in a later round) |
| 17 | Faint praise on three ads: 'nothing's breaking at least' (Peace Out, Krave, Beautyblender), meaning they work but aren't good. | 3 ads | Good ad | Not fixed |

Details, in the same order:

### 1. The video model made up how the cleanser's foam looks when it is rubbed on the face (Krave, scene 4).

**Came back** · 1 scene, all 4 tries (Krave scene 4); its last try stayed in the ad

- **Why it happened:** No page photo shows the foam, so the video model invents it. Nothing told the scene helper that this is a known video-model weakness; it kept retrying with new words.
- **What was changed:** First, the clip judge was changed so an unrequested product state is checked instead of waved through (round 9 fix). Then the scene helper got an end picture: it can draw what the end of the clip must look like and send it as the clip's last frame. Rule added: a result shows only where the product touched. (#375, #370–#374, #369)
- **What happened next:** Round 9 Krave clip 3896: white lather invented again (before the end-picture fix). Round 10 Krave clip 4137: 'the product content was invented' again (Julian, Nov 4 23:25), because an edited starting picture dropped the tube photo from the checks; fixed by PR #378. Round 11 Bissell scene 2: 'the video model invented the spray'.

### 2. The scene helper never understood why the scene kept failing and never found the root cause (Krave scene 4; Beautyblender scenes 2 and 3: 'useless changes to the prompt and the starting pic').

**Came back** · 3 scenes (Krave scene 4, Beautyblender scene 2, Beautyblender scene 3), 11 tries

- **Why it happened:** The agent's instructions held the right rule ('if words don't fix the clip... Don't guess missing evidence') but buried in ~45 lines; it had no facts about the video model's weak spots; 'think again' gave it no new fact.
- **What was changed:** Julian put the big design off (Nov 4 01:21: 'we don't need to do this yet... The prompts are a mess'). The judge was loosened first (only the product check can fail). Round 9 then added: after 2 product fails in a row, code tells the agent to make a bigger change (new shot, hide the failing part, simpler action, end picture). Round 11 added: fix failed clips with a new picture, not new words. (#369, #415)
- **What happened next:** Round 10 Logitech mouse scene 4: after the push, try 3 used a new tighter shot and passed. Round 11: Julian again: 'why did the agent kept doing useless fixes with the prompt in the clip 1 and 2' (BlenderBottle scene 2), 'this is very useless' (Revlon clip 4239).

### 3. The makeup (foundation) turned into flat opaque patches on the face; the scene's AI helper's fix didn't make sense and it didn't know the video model's limit with makeup effects (Beautyblender clip 3543, scene 3 tries).

**Partly fixed** · 2 scenes given up (Beautyblender scene 2, scene 3); 7 tries

- **Why it happened:** The video model is weak at showing makeup blending into skin, and the scene helper was never told; it kept changing words and pictures that weren't the cause.
- **What was changed:** End pictures (the agent draws the blended result and sends it as the clip's last frame) and the result-only-where-touched rule. (#375, #370–#374)
- **What happened next:** Round 9: same fault again on Beautyblender clips 3839 and 3903 (before the fix). End-picture test (Nov 4): start+end-picture takes passed on Beautyblender scene 2 and scene 3; positive-only wording broke physics. Round 10: Beautyblender scene 3 passed on try 3 with end pictures, Beautyblender scene 2 gave up again; Julian: 'it works' (Nov 5 04:10).

### 4. The real woman's face from the shop's page photo was sent to the video model; Julian thought there was a face-swap step (Krave scene 4).

**Partly fixed** · 1 ad (Krave)

- **Why it happened:** The face swap had been switched off by accident: PR #321 deleted its only call when the old B-roll sender was retired. Nothing checked for a stranger's face.
- **What was changed:** Face swap restored: any photo the scene helper picks that shows a stranger gets the presenter's face, checked, up to 3 tries; if every try fails the photo is still sent with a red notice. (#345, #347)
- **What happened next:** Round 9 Krave: 1 swap, check passed. Round 9 Beautyblender clip 3839: the page woman went to the picture drawer through page-video frames, which the swap skips; she did not reach the video model. Julian (Nov 4 18:08): 'i think we can ignore this if the woman didn't get to the video model'.

### 5. The clip judge was too strict: it failed clips Julian would ship.

**Fixed (proven in a later round)** · 9 of 17 clips he graded (clips: Beautyblender 3495, 3476, 3518; Gymreapers 3433; ezpz mat 3522; Legion Pulse 3494; Pair of Thieves 3619, 3630, 3638)

- **Why it happened:** The 'real motion' and 'sudden change' checks failed on almost every clip (8 of the 9 too-strict cases and all 4 real fails), so they didn't separate good from bad.
- **What was changed:** Only the product check can fail a clip. The motion questions were dropped from the verdict; the motion call stays only to describe the clip and check owner changes. (#350, #353)
- **What happened next:** Round 9: 104 clips ruled, all 28 fails came from the product check, and Julian wrote no 'too strict' note. The opposite then showed: the judge let an invented lather through as 'unsure' (Krave clip 3896), fixed separately.

### 6. Remove the 'does what was ordered' check; not that important (ezpz mat clip 3522).

**Fixed (proven in a later round)** · 1 clip

- **Why it happened:** That check compared the clip with the scene's 'shows' sentence and over-failed.
- **What was changed:** Dropped with the other motion checks: only the product check can fail a clip. (#350, #353)
- **What happened next:** Round 9: no complaint about it.

### 7. Maybe the judge is strict because it looks at too many frames; Creatify's agent looks at 12 frames (Pair of Thieves clip 3619).

**Not fixed** · 1 note

- **Why it happened:** Judge looks at up to 50 frames per question.
- **What was changed:** No change to the frame count.
- **What happened next:** No later graded round has checked it yet.

### 8. New starting pictures looked just like the old ones; the agent never checked whether a new picture actually changed anything, and a new picture always replaced the old one (Beautyblender scene 3).

**Fixed (proven in a later round)** · 1 scene (Beautyblender scene 3, 5 tries)

- **Why it happened:** No step compared a new picture with the one it was meant to fix; a new picture always became the default.
- **What was changed:** The agent sees the new picture next to the old one, says whether the change shows, then films it, films an earlier one, or draws again without filming (caps stay 6 pictures, 6 clips). (#346, #349)
- **What happened next:** Round 9 records: of new pictures after a scene's first, 29 filmed, 6 drew again, 1 filmed an earlier picture; Julian did not raise this again.

### 9. The agent asked the owner for something already in the reference video sent to the video model (Beautyblender clip 3543; it asked for a clean page video).

**Not fixed** · 1 scene

- **Why it happened:** Not investigated.
- **What was changed:** No fix found.
- **What happened next:** No later graded round has checked it yet.

### 10. Garject ad fails as a whole; ignore this product for a while, as even Creatify's own tools struggle with it (Garject).

**Dropped** · 1 ad

- **Why it happened:** The garlic press's scraper and handles keep coming out wrong; the hard action is beyond the video model.
- **What was changed:** Product parked.
- **What happened next:** Round 9 Garject failed again: 'this product is normal to fail'.

### 11. The product changed in the pictures given to the presenter-video service (Zulay frother).

**Fixed (proven in a later round)** · 1 ad (Zulay)

- **Why it happened:** Talking scenes were made by the presenter-video service from pictures that changed the product.
- **What was changed:** The presenter-video service was removed; every scene, talking and B-roll, is now made by the video model from a checked starting picture. (#342, #339, #340)
- **What happened next:** Round 9 Zulay passed with no words.

### 12. Wet Brush ad failed with no words (Wet Brush).

**Not fixed** · 1 ad

- **Why it happened:** Unknown: no words given.
- **What was changed:** No fix found.
- **What happened next:** Round 9 Wet Brush passed with no words.

### 13. The letters on the pre-workout tub changed during the clip (Legion clip 3506).

**Came back** · 1 clip

- **Why it happened:** The video model redraws printed words and they drift.
- **What was changed:** Prompts may not quote on-screen text (code refuses quoted text); a hand can cover a badly placed logo; later a label rule: a product shown to the lens is held close so letters are big. (#369, #427, #428)
- **What happened next:** Round 11 Liquid I.V. clip 4198: 'the logo letters... Change... Even for the clips that pass'.

### 14. The judge was right on the dog-paw washer twist clip: product changed and the motion was unreal (MudBuster clip 3623).

**Came back** · 1 clip (replaced before the ad)

- **Why it happened:** The video model made up how the cup is used (the twist).
- **What was changed:** None specific; the judge caught it and a new try went in the ad. Recorded as a video-model habit.
- **What happened next:** Round 9 MudBuster clip 3936: the twist use came back in the final ad and Julian failed it (Nov 5).

### 15. Ezpz mat clip failed with no words (clip 3545).

**Not fixed** · 1 clip (replaced before the ad)

- **Why it happened:** Judge saw product, motion and sudden-change faults.
- **What was changed:** None; the judge caught it and a later try passed.
- **What happened next:** No later graded round has checked it yet.

### 16. The chat agent insisted on a price the owner had asked to leave out, then left it out anyway ('this is kinda weird', Logitech).

**Fixed (proven in a later round)** · 1 ad (Logitech mouse)

- **Why it happened:** The planner's instructions required a price line and a question when no price was known; nothing allowed 'the owner wants no price'.
- **What was changed:** When the owner says to leave the price out, the plan has no price line or overlay and the planner doesn't ask. (#348, #351)
- **What happened next:** Round 9 notes: Logitech mouse's price stayed out of every line, overlay, caption and transcript.

### 17. Faint praise on three ads: 'nothing's breaking at least' (Peace Out, Krave, Beautyblender), meaning they work but aren't good.

**Not fixed** · 3 ads

- **Why it happened:** Before round 11 the rounds only checked that nothing broke; ad quality had no rules yet.
- **What was changed:** No fix in this round. Ad-quality work started after round 11 (quality spec, tickets #402-#411).
- **What happened next:** Round 12 graded quality: 0 of 12 ads passed quality.


## Left open

- The video model made up how the cleanser's foam looks when it is rubbed on the face (Krave, scene 4).
- The scene helper never understood why the scene kept failing and never found the root cause (Krave scene 4; Beautyblender scenes 2 and 3: 'useless changes to the prompt and the starting pic').
- The makeup (foundation) turned into flat opaque patches on the face; the scene's AI helper's fix didn't make sense and it didn't know the video model's limit with makeup effects (Beautyblender clip 3543, scene 3 tries).
- The real woman's face from the shop's page photo was sent to the video model; Julian thought there was a face-swap step (Krave scene 4).
- Maybe the judge is strict because it looks at too many frames; Creatify's agent looks at 12 frames (Pair of Thieves clip 3619).
- The agent asked the owner for something already in the reference video sent to the video model (Beautyblender clip 3543; it asked for a clean page video).
- Wet Brush ad failed with no words (Wet Brush).
- The letters on the pre-workout tub changed during the clip (Legion clip 3506).
- The judge was right on the dog-paw washer twist clip: product changed and the motion was unreal (MudBuster clip 3623).
- Ezpz mat clip failed with no words (clip 3545).
- Faint praise on three ads: 'nothing's breaking at least' (Peace Out, Krave, Beautyblender), meaning they work but aren't good.

## Caveats

- Most of the 99 takes had no grade box; takes with no feedback count as pass.
- "Nothing's breaking at least" on three ads counts as a quality fail, not a works fail.
- Logitech fails "works" because the chat agent asked for a price the owner had refused; the video itself was fine.

---

← [Round 7](round-07.md) · [All rounds](README.md) · [Round 9](round-09.md) →
