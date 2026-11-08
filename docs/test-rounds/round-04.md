# Round 4: 10 ads, first run with a clip judge (October 12–14, 2026)

## What this round tested

- 10 products: the 4 from round 3 plus 6 new (a swivel chair, a power bank, a Great Jones pan, Momofuku chili crunch, Gorjana earrings, Merit Flush Balm colour balm).
- New: a sentence that keeps the same side of the product facing the camera, one shared "before" picture per ad, and tools to fix a scene from a note.
- A new clip judge (an AI that watches each clip) was tried on these clips afterwards. That test is in [scene tests](scene-tests.md).

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 5 of 10 (50%) | 7 of 10 |
| Good ad (quality) | not graded | — |
| B-roll clips in the final ads that work | 18 of 29 (62%) | 23 of 29 |
| Talking clips | not graded | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words).

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Toilet cleaner scene 2 (problem scene): a woman pours liquid into the toilet from another bottle | 1 clip (also in the whole-ad note) | Works | Not fixed |
| 2 | Toilet cleaner scene 3: the brush scrubs the toilet seat as well as the bowl | 1 clip | Works | Fixed (proven in a later round) |
| 3 | Toilet cleaner scene 4: the rust ring fades where the brush never went (the judge's complaint; he had passed the clip, then agreed when the remake fixed it) | 1 clip | Works | Fixed (proven in a later round) |
| 4 | Phone case scene 2: a ring and a second phone already lie on the floor before the drop; confuses the viewer (minor, borderline) | 1 clip | Works | Not fixed |
| 5 | Phone case scene 4: a second phone in the person's trousers (he passed the clip) | 1 clip | Works | Not fixed |
| 6 | Phone case scene 2: the phone on the ground shakes for no reason after landing (and grows extra camera lenses) | 1 clip | Works | Fixed (proven in a later round) |
| 7 | Phone case scene 3: as the phone comes out of the pocket the back camera disappears, and both sides show the back camera | 1 clip (3rd round in a row with phone sides wrong) | Works | Fixed (not tested yet) |
| 8 | Le Duo scene 2 and scene 4: the curls form naturally, but the woman doesn't move the tool the way it is used (no rotation) | 2 clips (every round since round 1) | Works | Known limit |
| 9 | Le Duo scene 4: her lower body is cut off and she looks filmed elsewhere and pasted in ('not critical, just weird') | 1 clip | Good ad | Not fixed |
| 10 | Le Duo scene 3: the hair being styled is unnatural, it comes from around the mannequin's chin (judge's fault; he passed the clip, then called it unnatural) | 1 clip | Works | Not fixed |
| 11 | Chili crunch scene 2: the chili sauce is put on plain white rice; not sure that's its use ('fine, just weird') | 1 clip | Works | Not fixed |
| 12 | Merit balm scene 4: lip colour spreads and fades away from the fingertip (the judge failed it; he let it go, then agreed the remake fixed it) | 1 clip | Works | Fixed (proven in a later round) |

Details, in the same order:

### 1. Toilet cleaner scene 2 (problem scene): a woman pours liquid into the toilet from another bottle

**Not fixed** · 1 clip (also in the whole-ad note)

- **Why it happened:** The prompt said no person or hand enters; the video model added a hand with a second bottle anyway. The clip judge passed it: its motion check only looks at the product and hands touching it, so an uninvited hand with another bottle is outside what it is asked.
- **What was changed:** No fix built: an 'uninvited actor' judge check was proposed and parked until a second case appears.
- **What happened next:** Not fixed in this period; the video-model limits list calls made-up use 'the most common fail in nearly every round'.

### 2. Toilet cleaner scene 3: the brush scrubs the toilet seat as well as the bowl

**Fixed (proven in a later round)** · 1 clip

- **Why it happened:** The prompt asked for one scrub along the bowl ring and the start picture had the brush on the ring; the video model moved it onto the seat (a model slip).
- **What was changed:** The new clip judge catches it and the auto-redo remakes the clip (up to 2 tries, judged again). (#157, #155, #159)
- **What happened next:** Round 4 loop test: the first remake had no seat scrub; Julian 'fixed = yes', best clip. (His own note-fix the day before changed only the direction and kept the seat scrub: fix-loop test.)

### 3. Toilet cleaner scene 4: the rust ring fades where the brush never went (the judge's complaint; he had passed the clip, then agreed when the remake fixed it)

**Fixed (proven in a later round)** · 1 clip

- **Why it happened:** Video-model physics: the stain fades across the whole bowl instead of only where the brush acts.
- **What was changed:** Clip judge + auto-redo (existing rule: a result shows only where the product touched). (#157)
- **What happened next:** Loop test remake 1: Julian 'fixed = yes', best clip. The fix check wrongly said 'not fixed' on it.

### 4. Phone case scene 2: a ring and a second phone already lie on the floor before the drop; confuses the viewer (minor, borderline)

**Not fixed** · 1 clip

- **Why it happened:** The new shared before-picture put every object any scene acts on into one room (the bare phone the case fits, jeans for the pocket scene), and the scene rule said keep every thing in it exactly as it is.
- **What was changed:** No fix found: a one-sentence change (leave out loose things meant for other scenes) was proposed but no build was found.
- **What happened next:** Loop test remake 2 still had the bare phone and jeans in its start picture (Julian tapped it fixed for the shake, not the clutter).

### 5. Phone case scene 4: a second phone in the person's trousers (he passed the clip)

**Not fixed** · 1 clip

- **Why it happened:** Same as above: an extra object in the start picture, which he blamed on the start picture.
- **What was changed:** No fix found (same proposal as above).
- **What happened next:** Not re-tested.

### 6. Phone case scene 2: the phone on the ground shakes for no reason after landing (and grows extra camera lenses)

**Fixed (proven in a later round)** · 1 clip

- **Why it happened:** Video-model fault; the prompt was right.
- **What was changed:** Clip judge + auto-redo. (#157)
- **What happened next:** Loop test: remake 1 not fixed, remake 2 fixed (Julian 'yes', best). The video-model limits list lists unnatural shaking.

### 7. Phone case scene 3: as the phone comes out of the pocket the back camera disappears, and both sides show the back camera

**Fixed (not tested yet)** · 1 clip (3rd round in a row with phone sides wrong)

- **Why it happened:** The video model invents the unseen side; the old judge looked at only 5 still frames and none fell on the glitch.
- **What was changed:** The clip judge's sides check looks at 10 frames a second and decides the turn direction first. (#163)
- **What happened next:** On this clip it caught the fault 3 of 3 times; on 110 old clips it caught 3 of 3 flips with 0 wrong fails on 66 good ones. Not re-tested on a new graded phone ad in this period.

### 8. Le Duo scene 2 and scene 4: the curls form naturally, but the woman doesn't move the tool the way it is used (no rotation)

**Known limit** · 2 clips (every round since round 1)

- **Why it happened:** The planner keeps the page's 'twist while sliding' how-to; the video model only tilts the tool and can't turn it while gliding.
- **What was changed:** Julian asked to test hand-written prompts first; his loop-test taps on 3 ever-stronger wordings were that test (partial turn at best), so it was written down as a video-model limit, not a rule.
- **What happened next:** Le Duo was not tested again; hair tools are listed as a video-model weakness (the video-model limits list names hair tools in rounds 3, 4 and 6, and says Wet Brush was given up in round 7).

### 9. Le Duo scene 4: her lower body is cut off and she looks filmed elsewhere and pasted in ('not critical, just weird')

**Not fixed** · 1 clip

- **Why it happened:** The start picture's framing: the presenter beside the station with her lower body out of frame.
- **What was changed:** No fix found for this round. (A later fix for a presenter who looks pasted onto the room, drawing her and the room in one picture, came in round 11: the video-model limits list.)
- **What happened next:** Not re-tested in this period.

### 10. Le Duo scene 3: the hair being styled is unnatural, it comes from around the mannequin's chin (judge's fault; he passed the clip, then called it unnatural)

**Not fixed** · 1 clip

- **Why it happened:** The start picture put the hair section at the chin instead of on the back of the head from the scalp.
- **What was changed:** Auto-redo tried twice; both remakes kept the same hair. No other fix.
- **What happened next:** No: both remakes 'fixed = no'.

### 11. Chili crunch scene 2: the chili sauce is put on plain white rice; not sure that's its use ('fine, just weird')

**Not fixed** · 1 clip

- **Why it happened:** The planner picked a use the page doesn't clearly show (the owner was not asked).
- **What was changed:** No fix found.
- **What happened next:** Not re-tested.

### 12. Merit balm scene 4: lip colour spreads and fades away from the fingertip (the judge failed it; he let it go, then agreed the remake fixed it)

**Fixed (proven in a later round)** · 1 clip

- **Why it happened:** Video-model physics: colour appears where the finger isn't.
- **What was changed:** Clip judge + auto-redo. (#157)
- **What happened next:** Loop test remake 1: Julian 'fixed = yes', best clip.


## Left open

- Toilet cleaner scene 2 (problem scene): a woman pours liquid into the toilet from another bottle
- Phone case scene 2: a ring and a second phone already lie on the floor before the drop; confuses the viewer (minor, borderline)
- Phone case scene 4: a second phone in the person's trousers (he passed the clip)
- Le Duo scene 2 and scene 4: the curls form naturally, but the woman doesn't move the tool the way it is used (no rotation)
- Le Duo scene 4: her lower body is cut off and she looks filmed elsewhere and pasted in ('not critical, just weird')
- Le Duo scene 3: the hair being styled is unnatural, it comes from around the mannequin's chin (judge's fault; he passed the clip, then called it unnatural)
- Chili crunch scene 2: the chili sauce is put on plain white rice; not sure that's its use ('fine, just weird')

## Caveats

- 5 clips he tapped pass become fails under the strict rule (for example a stray second phone he named but let go).
- Two ads he tapped pass (chili crunch, Merit balm) fail only because one of their clips fails.
- Talking scenes were not on the page. Quality was judged on one clip only.

---

← [Round 3](round-03.md) · [All rounds](README.md) · [Round 5](round-05.md) →
