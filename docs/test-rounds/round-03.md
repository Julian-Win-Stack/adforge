# Round 3: 4 ads after the "B-roll must sell" fixes (October 10, 2026)

## What this round tested

- Every B-roll scene must prove its line (else ask the owner, else use a talking scene). Results are filmed as they happen; drops use the height the page claims.
- Products: 4 from round 1 (toilet cleaner, phone case, handbag, Le Duo hair styler).
- The start-picture check was switched off just before the run. First round graded by taps on a page.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 1 of 4 (25%) | 1 of 4 |
| Good ad (quality) | not graded | — |
| B-roll clips in the final ads that work | 7 of 13 (54%) | 7 of 13 |
| Talking clips | not graded | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words).

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The phone flips mid-clip and shows the back camera on both sides (phone case scene 2, scene 3), although the prompt said not to turn it | 2 clips | Works | Came back |
| 2 | Phone case scene 4: an impossible phone, a selfie screen on the camera side ('this specific phone doesn't exist') | 1 clip | Works | Fixed (not tested yet) |
| 3 | Toilet cleaner scene 3: a different toilet with different stains and dirt from the other scenes; he wants the dirt kept the same across scenes | 1 clip (and a note on scene 4) | Works | Fixed (proven in a later round) |
| 4 | Toilet cleaner scene 3: the cleaner pours like water, but the page says it's a gel | 1 clip | Works | Came back |
| 5 | Toilet cleaner scene 3: the liquid comes out on the side opposite the spout, and the cap is never opened | 1 clip | Works | Came back |
| 6 | Le Duo scene 2: the hair jumps from straight to curly right at the end ('broke the laws of physics') | 1 clip (scene 4 similar) | Works | Not fixed |
| 7 | Le Duo scene 4: the tool rotates in a weird, fake AI-like way; the hair looks forced into it; not how real curls are made | 2 clips (scene 2, scene 4) | Works | Known limit |
| 8 | Le Duo scene 2: no hair inside the tool at the start of the clip | 1 clip | Works | Not fixed |
| 9 | Le Duo scene 2/scene 4: he expected the tool to make the hair straight but it made curls/waves | 2 clips | Works | Dropped |

Details, in the same order:

### 1. The phone flips mid-clip and shows the back camera on both sides (phone case scene 2, scene 3), although the prompt said not to turn it

**Came back** · 2 clips

- **Why it happened:** The video model sees only the start picture, so when the phone turns it makes up the other side; the planner rule against turning was ignored by the video model (random slip, not in the prompt).
- **What was changed:** Code adds one fixed sentence to every B-roll video prompt: 'Whatever side faces the camera at the start stays toward the camera the whole time.' Later: the clip judge checks sides at 10 frames a second and decides the turn direction first. (#123, #163)
- **What happened next:** Round 4 phone case scene 3 again showed the back camera on both sides (the phone left a pocket). The 10-frames judge then caught that clip 3 of 3 times and 3 of 3 flips on 110 old clips with 0 wrong fails. The video-model limits list: drops and tumbles still flip, so they are not filmed.

### 2. Phone case scene 4: an impossible phone, a selfie screen on the camera side ('this specific phone doesn't exist')

**Fixed (not tested yet)** · 1 clip

- **Why it happened:** The prompt writer ordered a view no camera can give (her face, the case on the phone's back, and the selfie screen all facing the camera); the impossible phone was already in the start picture, and the start-picture check was switched off.
- **What was changed:** A new start-picture judge for impossible objects (later shown the shop photo) replaced the old 7-check picture check; on a fail the picture is redrawn with the reason; no new prompt rule, by Julian's call. (#124, #169)
- **What happened next:** No impossible-phone complaint in round 4, but round 4 ran with the picture checks off by default, so the judge didn't run there. Measured later: caught 6 of 10 bad pictures, failed 0 of 52 good. It misses wrong background objects like a misshapen toilet (the known-limits list).

### 3. Toilet cleaner scene 3: a different toilet with different stains and dirt from the other scenes; he wants the dirt kept the same across scenes

**Fixed (proven in a later round)** · 1 clip (and a note on scene 4)

- **Why it happened:** Each B-roll start picture was drawn on its own from its own prompt; nothing tied the scenes of one ad to one room or one set of stains.
- **What was changed:** One shared 'before-picture' per ad: every B-roll start picture is drawn from it, keeping the same room, light and every stain. (#125)
- **What happened next:** Round 4 toilet cleaner: no mismatched-toilet complaint; scene 4 passed. Side effect: things meant for other scenes now showed up in a scene (round 4 phone case scene 2 'a ring and another phone on the ground').

### 4. Toilet cleaner scene 3: the cleaner pours like water, but the page says it's a gel

**Came back** · 1 clip

- **Why it happened:** No pour was asked for: the prompt had a hand 'set the bottle on the tank lid' (already there in the start picture) and made 'ripples' with no cause, so the model filled the gap with a pour nobody described. Product facts (gel) never reach the prompt writer.
- **What was changed:** Problem scenes: nobody touches the product; it stands still, label to the camera; the one movement is something natural already in the scene (water rippling, steam). (#127)
- **What happened next:** Round 4 toilet cleaner scene 2 (a problem scene) failed again: 'the woman pours the liquid... Into the toilet with another bottle'. The judge missed it; an 'uninvited actor' check was parked.

### 5. Toilet cleaner scene 3: the liquid comes out on the side opposite the spout, and the cap is never opened

**Came back** · 1 clip

- **Why it happened:** Same as above: an unasked-for pour, never described, so the model made it up physically wrong.
- **What was changed:** Same fix: nobody touches the product in a problem scene. (#127)
- **What happened next:** Round 4 toilet cleaner scene 2: another unasked-for pour (from a second bottle).

### 6. Le Duo scene 2: the hair jumps from straight to curly right at the end ('broke the laws of physics')

**Not fixed** · 1 clip (scene 4 similar)

- **Why it happened:** Asking the model to grow a curl under a turning tool; in real life the hair is wrapped round the tool first, so the cause is visible. The video model is worst at this kind of gradual shape change.
- **What was changed:** No fix found. A 'hard part in the picture, easy part in the video' fix (hair already wrapped in the start picture) and a $1.50 test were proposed but not built.
- **What happened next:** Round 4 Le Duo scene 2 and scene 4: 'the curls appear naturally' (so the sudden pop did not repeat), but the movement was still wrong.

### 7. Le Duo scene 4: the tool rotates in a weird, fake AI-like way; the hair looks forced into it; not how real curls are made

**Known limit** · 2 clips (scene 2, scene 4)

- **Why it happened:** The planner keeps the page's 'twist while sliding' how-to (Julian's Oct 8 rule); the video model can't turn a tool while gliding it.
- **What was changed:** No prompt fix worked; written down as a video-model limit (the planner avoids the twist).
- **What happened next:** Round 4 scene 2 and scene 4 failed the same way; 3 ever-stronger prompts in the loop test gave a partial turn at best.

### 8. Le Duo scene 2: no hair inside the tool at the start of the clip

**Not fixed** · 1 clip

- **Why it happened:** The start picture showed the tool beside the hair section, not clamped on it.
- **What was changed:** No fix found (the wrap-in-the-picture proposal above was never built).
- **What happened next:** Not repeated in round 4 (no such complaint).

### 9. Le Duo scene 2/scene 4: he expected the tool to make the hair straight but it made curls/waves

**Dropped** · 2 clips

- **Why it happened:** The lines promised curls and waves; on re-watching he said the real problem is the curl appearing suddenly and the unnatural movement, and 'I might be wrong'.
- **What was changed:** No change; folded into the two Le Duo entries above.
- **What happened next:** Not raised again.


## Left open

- The phone flips mid-clip and shows the back camera on both sides (phone case scene 2, scene 3), although the prompt said not to turn it
- Toilet cleaner scene 3: the cleaner pours like water, but the page says it's a gel
- Toilet cleaner scene 3: the liquid comes out on the side opposite the spout, and the cap is never opened
- Le Duo scene 2: the hair jumps from straight to curly right at the end ('broke the laws of physics')
- Le Duo scene 4: the tool rotates in a weird, fake AI-like way; the hair looks forced into it; not how real curls are made
- Le Duo scene 2: no hair inside the tool at the start of the clip

## Caveats

- Only B-roll clips and whole ads were on the page. Talking scenes were not graded.
- Quality was not graded this round.
- His taps and the strict rule agree here: every complaint came with a fail tap.

---

← [Round 2](round-02.md) · [All rounds](README.md) · [Round 4](round-04.md) →
