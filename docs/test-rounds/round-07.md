# Round 7: 15 ads on the "page photos as references" approach (October 29–31, 2026)

## What this round tested

- 15 products, mostly from round 6, used again in rounds 8 and 9: Peace Out pore strips, Krave cleanser, Beautyblender makeup sponge, Wet Brush hair brush, Garject garlic press, Zulay milk frother, Gymreapers lifting straps, Dexas MudBuster dog-paw washer, ezpz baby mat, Logitech mouse, Chipolo tracker tag, Legion Pulse pre-workout, MUD\WTR kit, Force of Nature cleaner, Pair of Thieves socks.
- New approach: page photos sent to the video model as labelled references, no drawn starting picture, and the fixing agent remaking failed clips.
- Run on the cloud server, nothing faked. One product (Wet Brush) was run twice, the second time after a photo-picker fix.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 4 of 15 (27%) | 12 of 15 (he tapped only 3 ads, all fail) |
| Good ad (quality) | not graded | — |
| B-roll clips in the final ads that work | 14 of 27 (52%) | — |
| Every B-roll take shown (incl. remakes) | 22 of 50 | — |
| Talking clips | not graded | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers (for example clip 3839) are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Invented use: the app told the video model to lift the pore strip off with the extractor tool; in real life you peel it off by hand. | 1 clip (Peace Out scene 3) | Works | Came back |
| 2 | Invented use: the clips twist the Dexas MudBuster; the page video shows it moving in and out, not twisting. | 5 clips (MudBuster scene 3, every try) | Works | Came back |
| 3 | Backgrounds, clothes and other people from page photos (and the page video) leaked into the clip; the app's own background picture was not used. | 3 clips by his words (Beautyblender scene 2, Chipolo scene 3 kept, MUD\WTR scene 3 kept), plus coffee-cup props on one more clip | Works | Partly fixed |
| 4 | Too many pictures sent to the video model, most not needed (Beautyblender scene 2 images 3-4, ezpz mat scene 2 nine pictures, Chipolo scene 3 image 1, Chipolo scene 4). | 5 scenes named; 3.74 pictures per clip on average, 3 clips with 9 | Works | Fixed (proven in a later round) |
| 5 | The first picture was always the main page photo and could not change in remakes; its packaging print leaked onto the Beautyblender sponge, and the open Garject photo gave a third handle. | Beautyblender scene 4 remake 2 (his words); fixing agent blamed it on Beautyblender and Garject | Works | Fixed (not tested yet) |
| 6 | The fixing agent's remake prompts were too long and detailed (296 words vs 121 for first clips) and could contradict each other. | Chipolo scene 3 remake 1 named; 23 remakes average 296 words | Works | Came back |
| 7 | The fixing agent tweaked the prompt instead of fixing the root cause it could see (for example, just remove the leaked background). | Chipolo scene 3 remake 1 | Works | Came back |
| 8 | Product came out too big (Chipolo tag), and the video model doesn't know the product's real size next to the person. | Chipolo scene 3 remake 1 and Chipolo scene 4 (3 clips) | Works | Dropped |
| 9 | A photo with another person in it was sent next to the presenter picture (risky; the model copies the person). | Chipolo scene 4 (3 clips); 10 of 13 presenter clips sent another person | Works | Came back |
| 10 | The video model invented a cable on the Zulay frother, which has none. | 1 clip (Zulay scene 2) | Works | Fixed (not tested yet) |
| 11 | A red frother picture was sent for a black product (recolour failed), plus other-colour product pictures on ezpz mat and Logitech mouse. | 8 clips sent a wrong-colour product picture | Works | Fixed (not tested yet) |
| 12 | The picker didn't send the picture of the product's contents (Legion Pulse powder), so the video model never saw it. | 1 clip (Legion Pulse scene 2) | Works | Partly fixed |
| 13 | A picture sent to the video model looked bad (ezpz mat picture 2, a drawn mat). | 1 clip (ezpz mat scene 2) | Works | Fixed (not tested yet) |
| 14 | The camera looked down into the Force of Nature flask's top, which no picture shows, so the look was made up. | 1 clip (Force of Nature scene 2) | Works | Fixed (not tested yet) |
| 15 | The page video was sent as 'movement only', never for the look; sometimes it was sent where it made no sense (Force of Nature). | 16 clips sent a page video, all 'movement only' | Works | Fixed (not tested yet) |
| 16 | The judge failed clips Julian thought were fine (all Beautyblender scene 3 tries; Chipolo scene 2's phone-screen words). | 5 clips (Beautyblender scene 3: 4 judge fails he passed; Chipolo scene 2 first) | Works | Came back |
| 17 | The judge failed Beautyblender scene 4 because the sponge 'did not blend the foundation'; Julian says that is not a fail reason. | 2 clips (Beautyblender scene 4 first and remake 1) | Works | Came back |
| 18 | The judge said 'can't tell' and skipped its checks when it could have judged (Peace Out scene 2: the strip was visible on the nose). | 9 'unsure' clips; 8 skipped both checks | Works | Came back |
| 19 | Julian could not understand the judge's ruling and many reasons on the pages. | Repeated asks during grading | Works | Came back |
| 20 | Wet Brush: the video model can't brush hair with this brush; it breaks the physics. | Wet Brush whole ad + 3 clips; picker-fix first clip | Works | Known limit |
| 21 | Garject: most page photos mislead the video model, no reference video was given, and every B-roll clip failed. | Garject whole ad + 6 clips | Works | Known limit |
| 22 | Our AI can't pick the right inputs by looking, as Julian can (he says it isn't intelligent enough). | General (Garject, Oct 31 discussion) | Works | Partly fixed |
| 23 | (found in the run, before grading) No Gemini key on the server, so page videos could not be watched | Round 7 run | Works | Fixed (proven in a later round) |
| 24 | (found in the run, before grading) Voice account full (100 designed voices): presenters could not be made | Round 7 run | Works | Fixed (not tested yet) |
| 25 | (found in the run, before grading) Website image build failed on the server; deploy script's busy check always refused | Round 7 run | Works | Fixed (not tested yet) |
| 26 | (found in the run, before grading) Ads stalled until someone typed 'Please carry on' (MudBuster, Force of Nature content filter) | Round 7 run | Works | Fixed (not tested yet) |
| 27 | (found in the run, before grading) Product cut-outs failed all 3 tries (21) and recolours couldn't be made (~27); quiet failures Julian couldn't see | Round 7 run | Works | Fixed (not tested yet) |
| 28 | (found in the run, before grading) Scenes had no room picture, so the video model invented the place (14) | Round 7 run | Works | Fixed (not tested yet) |
| 29 | (found in the run, before grading) Safety filter refused starting pictures and a whole hidden-picture grid (Wet Brush: 9 photos lost) | Round 7 run | Works | Partly fixed |
| 30 | (found in the run, before grading) AVIF photos rejected (Chipolo, 10 photos); more than 9 pictures for a clip (Logitech mouse) | Round 7 run | Works | Fixed (not tested yet) |
| 31 | (found in the run, before grading) Planner and fixing agent small faults found in the run: shop description dropped, colour rule deleting a demo, repeated hook line, refilmed passed scenes, 72 chat-agent turns that did nothing ($4.13) | Round 7 run | Works | Fixed (not tested yet) |

Details, in the same order:

### 1. Invented use: the app told the video model to lift the pore strip off with the extractor tool; in real life you peel it off by hand.

**Came back** · 1 clip (Peace Out scene 3)

- **Why it happened:** The planner and prompt guessed the tool's use from a two-step page photo (strip worn / extractor used) and wrote the wrong instruction.
- **What was changed:** No code fix for this case; Julian said he would fix it himself through an owner note (never tested). Later rules: one action per clip, show the action the way the page shows it, and don't film actions the video model can't do.
- **What happened next:** Round 8 Peace Out whole ad passed ('nothing's breaking at least'). The same kind of invented use came back in round 9 on Dexas (clip 3936).

### 2. Invented use: the clips twist the Dexas MudBuster; the page video shows it moving in and out, not twisting.

**Came back** · 5 clips (MudBuster scene 3, every try)

- **Why it happened:** The word 'twist' first appears in the video watcher's (an AI that watches the page video) description of the page video (10-17 s); the planner copied it into the scene, the prompt and every remake. The planner never watches the video itself.
- **What was changed:** No direct fix found. Julian: 'the owner should fix this' and 'No worry is if we cannot fix this'. Later the scene helper (with eyes) writes the prompt with the picture in view, but the watcher text still feeds the plan.
- **What happened next:** Came back in round 9: Dexas clip 3936 failed for twisting / up-and-down use that no product page says (changed to fail Nov 5).

### 3. Backgrounds, clothes and other people from page photos (and the page video) leaked into the clip; the app's own background picture was not used.

**Partly fixed** · 3 clips by his words (Beautyblender scene 2, Chipolo scene 3 kept, MUD\WTR scene 3 kept), plus coffee-cup props on one more clip

- **Why it happened:** In-use, content and result photos were sent whole, people and backgrounds included; only side/state photos were cut out. 10 of 13 presenter clips also sent a page photo of another person.
- **What was changed:** Julian's rules (Oct 31): clean every picture first and send only what the scene needs. Built as the lean pictures prototype (see side tests), then replaced on Nov 2-3 by the drawn-starting-picture approach: one scene helper per B-roll scene picks the photos it needs, draws a starting picture, has it checked, and films from it; strangers' faces swapped for the presenter. (#324, #347)
- **What happened next:** Round 8 grades have no background-leak complaint. But the real woman's face from page photos still reached the video model in round 8 (Krave scene 4) and round 9 (Beautyblender clip 3839).

### 4. Too many pictures sent to the video model, most not needed (Beautyblender scene 2 images 3-4, ezpz mat scene 2 nine pictures, Chipolo scene 3 image 1, Chipolo scene 4).

**Fixed (proven in a later round)** · 5 scenes named; 3.74 pictures per clip on average, 3 clips with 9

- **Why it happened:** The send list was fixed tiers that nothing trimmed: the front page photo always first, a drawn room always, every back/side/top photo always, up to 9.
- **What was changed:** 'Send only what the scene needs' (his rule 1). Scene helpers now choose the photos for each scene and film from one checked starting picture (6 pictures cap per run). (#324)
- **What happened next:** Scene-agent prototype round 1 (Nov 3): Julian passed 15 of 15 takes. Round 8 notes have no 'too many pictures' complaint.

### 5. The first picture was always the main page photo and could not change in remakes; its packaging print leaked onto the Beautyblender sponge, and the open Garject photo gave a third handle.

**Fixed (not tested yet)** · Beautyblender scene 4 remake 2 (his words); fixing agent blamed it on Beautyblender and Garject

- **Why it happened:** A rule forced the page's front photo as the first picture in every clip, and the fixing agent was not allowed to swap it.
- **What was changed:** Rule removed (Julian: 'I think we should remove this rule'). In the drawn-starting-picture approach the checked starting picture is the first picture, and the scene helper can redraw it. (#324)
- **What happened next:** No packaging-leak complaint in round 8; round 8 Beautyblender clips were mostly passed by Julian (judge too strict).

### 6. The fixing agent's remake prompts were too long and detailed (296 words vs 121 for first clips) and could contradict each other.

**Came back** · Chipolo scene 3 remake 1 named; 23 remakes average 296 words

- **Why it happened:** No length limit on the fixing agent; it wrote second-by-second timelines and long settings on every try.
- **What was changed:** Short prompts in the first-clip format for remakes too (his rule 6). Scene helpers fill a fixed video form instead of free text; Nov 6 rule: fix a failed clip with a new picture, not new words. (#324, #415)
- **What happened next:** Came back in round 8 (Beautyblender: 'the agent kept doing useless changes to the prompt') and round 9 (Logitech mouse: 'all the agent tried to change was prompt change'), which led to PR #415; not graded since.

### 7. The fixing agent tweaked the prompt instead of fixing the root cause it could see (for example, just remove the leaked background).

**Came back** · Chipolo scene 3 remake 1

- **Why it happened:** The fixing agent had no move to clean a picture, and remade by rewording.
- **What was changed:** 'The only lesson... Fix the root cause' (his words). Fixing agent / scene helper got moves to clean or swap a picture; later 'new picture, not new words'. (#324, #415)
- **What happened next:** Came back in round 8 (Beautyblender clip 3543: 'the fix that the agent did didn't touch the root cause').

### 8. Product came out too big (Chipolo tag), and the video model doesn't know the product's real size next to the person.

**Dropped** · Chipolo scene 3 remake 1 and Chipolo scene 4 (3 clips)

- **Why it happened:** Nothing tells the video model the product's size; a product-alone photo gives no scale.
- **What was changed:** Dropped by Julian: 'Maybe just ignore it... If we face failures, we will fix it with owners' notes'.
- **What happened next:** Not tested.

### 9. A photo with another person in it was sent next to the presenter picture (risky; the model copies the person).

**Came back** · Chipolo scene 4 (3 clips); 10 of 13 presenter clips sent another person

- **Why it happened:** Page photos of models went whole with the drawn presenter picture.
- **What was changed:** Strangers' faces swapped for the presenter before a photo is used (built Oct 30, after round 7); start pictures face-swapped (PR #347, Nov 3).
- **What happened next:** Came back: round 8 Krave 'we keep inputting the real girl's face into the video model'; round 9 Beautyblender 'why is the real woman from the page pic being inputted'.

### 10. The video model invented a cable on the Zulay frother, which has none.

**Fixed (not tested yet)** · 1 clip (Zulay scene 2)

- **Why it happened:** The video model invents parts it has never seen; the judge said 'can't tell' because no picture showed a cable, and skipped its checks.
- **What was changed:** Start picture pins the product's look; every prompt has a lock line naming parts that must stay; the product check looks at 10 frames a second. Julian's idea (judge lists everything not in the inputs) was not built ('I'm not so sure whether we should actually implement it'). (#324)
- **What happened next:** No later graded Zulay clip with this fault found; round 8 Zulay whole ad failed for the product changing in the talking-scene photos.

### 11. A red frother picture was sent for a black product (recolour failed), plus other-colour product pictures on ezpz mat and Logitech mouse.

**Fixed (not tested yet)** · 8 clips sent a wrong-colour product picture

- **Why it happened:** All 24 recolour tries in round 7 fell back to the original photo; the checker rejected even good recolours on framing.
- **What was changed:** Cut-outs and recolours redesigned (#273), merged with the round 7 branch. (#286)
- **What happened next:** The video-model limits list: the redesign hasn't been run on real pictures yet.

### 12. The picker didn't send the picture of the product's contents (Legion Pulse powder), so the video model never saw it.

**Partly fixed** · 1 clip (Legion Pulse scene 2)

- **Why it happened:** Only the closed tub and a drawn room were sent; contents pictures went only when the planner named them.
- **What was changed:** Send content or result pictures on scenes that show them (his rule 9); scene helpers pick the photos the scene needs. (#324)
- **What happened next:** Same complaint on the lean pictures prototype (see side tests) (Nov 1, Legion Pulse: 'What is the content of the product?'). Round 8 Legion Pulse whole ad passed.

### 13. A picture sent to the video model looked bad (ezpz mat picture 2, a drawn mat).

**Fixed (not tested yet)** · 1 clip (ezpz mat scene 2)

- **Why it happened:** The app's drawn picture of the product went out unchecked for looks.
- **What was changed:** The drawn-starting-picture approach: the starting picture is drawn and checked by a separate picture check before filming. (#324)
- **What happened next:** Round 8 ezpz mat whole ad passed; one ezpz mat clip failed with no words.

### 14. The camera looked down into the Force of Nature flask's top, which no picture shows, so the look was made up.

**Fixed (not tested yet)** · 1 clip (Force of Nature scene 2)

- **Why it happened:** The prompt asked for an angle the inputs never covered.
- **What was changed:** 'Never film an angle the pictures don't show' (his rule 8); in code as 'Never show a side or part no photo shows', and the picture check fails a starting picture that does. (#406)
- **What happened next:** Round 8 Force of Nature whole ad passed; no later complaint of this kind found.

### 15. The page video was sent as 'movement only', never for the look; sometimes it was sent where it made no sense (Force of Nature).

**Fixed (not tested yet)** · 16 clips sent a page video, all 'movement only'

- **Why it happened:** A fixed prompt line 'Video 1 is only for the movement, not the look', and piece lengths that didn't match the clip.
- **What was changed:** Send the page video when its movement is the clip's movement and let it guide the look (his rule 5). Scenes with a page video now film in reference mode with the starting picture plus the video.
- **What happened next:** Not checked by a later grade specifically.

### 16. The judge failed clips Julian thought were fine (all Beautyblender scene 3 tries; Chipolo scene 2's phone-screen words).

**Came back** · 5 clips (Beautyblender scene 3: 4 judge fails he passed; Chipolo scene 2 first)

- **Why it happened:** The judge's fail bar was too strict, not the model: a second AI model (Gemini Flash) re-judged the 9 Beautyblender clips and gave the same verdict on all 9.
- **What was changed:** Gemini re-judge tested (no gain). Then only the product check can fail a clip (PR #353, Nov 3).
- **What happened next:** Came back: round 8 Julian said 'the judge is too strict' on 7 clips (Beautyblender, Gymreapers, Legion Pulse, Pair of Thieves).

### 17. The judge failed Beautyblender scene 4 because the sponge 'did not blend the foundation'; Julian says that is not a fail reason.

**Came back** · 2 clips (Beautyblender scene 4 first and remake 1)

- **Why it happened:** The motion check failed results that appear where the sponge didn't touch, which the video model can't really film.
- **What was changed:** Only the product check can fail a new clip (PR #353); later an end picture can be sent as the clip's last frame for result effects. (#375)
- **What happened next:** Came back in round 8 (Beautyblender clip 3495 'the judge is being too strict') and round 9 (Beautyblender clips 3839/3903 fail on foundation; he asked for an end picture).

### 18. The judge said 'can't tell' and skipped its checks when it could have judged (Peace Out scene 2: the strip was visible on the nose).

**Came back** · 9 'unsure' clips; 8 skipped both checks

- **Why it happened:** When the first question said the photos don't show something, the product and motion checks were skipped entirely.
- **What was changed:** 'Cannot tell' split into 'product not shown' and 'detail not shown' (detail: checks still run); motion check still runs when the product isn't shown (#290).
- **What happened next:** Came back: round 9 Peace Out 'I don't get it. Why unsure', Krave 'why did the judge said unsure? The content of the cleaner changed'.

### 19. Julian could not understand the judge's ruling and many reasons on the pages.

**Came back** · Repeated asks during grading

- **Why it happened:** Judge answers were raw fields; the grading page labelled 'unsure' as 'not run'.
- **What was changed:** Plain-words summaries of why the judge failed or was unsure on the grading pages; traces in Langfuse (a tool that logs each AI call) named per ad, with failed verdicts tagged (#288).
- **What happened next:** Came back in round 9: 'wait. What actually failed in here? I don't get it' on several MudBuster and Logitech mouse clips.

### 20. Wet Brush: the video model can't brush hair with this brush; it breaks the physics.

**Known limit** · Wet Brush whole ad + 3 clips; picker-fix first clip

- **Why it happened:** A video-model limit seen since round 6 and hand round 1 (invented bristles, two brushes, hand jumps). The one pass filmed the brush's smooth back to the camera.
- **What was changed:** Julian gave up on the product: 'I give up for this one'. Rule: show the end result when the action can't pass (one of Julian's written design rules).
- **What happened next:** Round 8 Wet Brush whole ad failed; round 9 Wet Brush whole ad passed (round 9: 'we didn't film the risky and hard actions').

### 21. Garject: most page photos mislead the video model, no reference video was given, and every B-roll clip failed.

**Known limit** · Garject whole ad + 6 clips

- **Why it happened:** The two things that made it pass by hand (closed press as main picture, the page video) were missing: the first picture was the open press, and the page video is a Vimeo player the app couldn't download.
- **What was changed:** Vimeo/Wistia/Vidyard page videos now download (#300, Nov 3); forced the first picture removed. Product then set aside.
- **What happened next:** Round 8 Garject failed ('ignore this specific product for a while. Even creatify fails this'); round 9 Garject failed ('this product is normal to fail').

### 22. Our AI can't pick the right inputs by looking, as Julian can (he says it isn't intelligent enough).

**Partly fixed** · General (Garject, Oct 31 discussion)

- **Why it happened:** Code chose inputs by fixed rules; the prompt writer saw no pictures; the planner reads the watcher's text, not the video.
- **What was changed:** One scene helper per B-roll scene that sees the pictures, picks them, draws and checks the start picture and writes the prompt with it in view. (#324)
- **What happened next:** Round 8: 9 of 15 whole ads work (12 of 15 by his taps alone); round 9 'almost all the ads pass' (because risky actions weren't filmed). Invented use still came back in round 9.

### 23. (found in the run, before grading) No Gemini key on the server, so page videos could not be watched

**Fixed (proven in a later round)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** Key added, redeployed (Oct 29) (#286)
- **What happened next:** After the fix Gemini watched every page video in round 7 (Beautyblender, MudBuster, ezpz mat, Force of Nature), 0 failures.

### 24. (found in the run, before grading) Voice account full (100 designed voices): presenters could not be made

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** 45 unused voices deleted by hand; then code frees a voice when the account is full and waits out busy services (#286)
- **What happened next:** Voices later moved to the video model speech (PR #342, Nov 3), so the voice service is no longer used.

### 25. (found in the run, before grading) Website image build failed on the server; deploy script's busy check always refused

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** Fixed locally on the box in round 7; website build fixed in code Nov 5 (PR #387) (#286)
- **What happened next:** No later report of the same break found.

### 26. (found in the run, before grading) Ads stalled until someone typed 'Please carry on' (MudBuster, Force of Nature content filter)

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** The app wakes a stopped chat agent itself; a safety refusal of something just made is made again; open questions stay open until answered (#286)
- **What happened next:** Not checked by a later round.

### 27. (found in the run, before grading) Product cut-outs failed all 3 tries (21) and recolours couldn't be made (~27); quiet failures Julian couldn't see

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** Cut-outs and recolours redesigned (#273, in PR #286)
- **What happened next:** The video-model limits list: not run on real pictures yet.

### 28. (found in the run, before grading) Scenes had no room picture, so the video model invented the place (14)

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** The room is drawn before any clip's picture list is built (#286)
- **What happened next:** The drawn-starting-picture approach later draws the person in the room once for all scenes (#416).

### 29. (found in the run, before grading) Safety filter refused starting pictures and a whole hidden-picture grid (Wet Brush: 9 photos lost)

**Partly fixed** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** Picker halves a refused grid (#267) and remakes what the filter refused (#286)
- **What happened next:** Wet Brush rerun: the halving found the one bad photo, but the combined ask was still refused, so all 9 were still dropped.

### 30. (found in the run, before grading) AVIF photos rejected (Chipolo, 10 photos); more than 9 pictures for a clip (Logitech mouse)

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** Photos in any format the image library reads are kept; scene helpers cap pictures (#286)
- **What happened next:** Not checked by a later round.

### 31. (found in the run, before grading) Planner and fixing agent small faults found in the run: shop description dropped, colour rule deleting a demo, repeated hook line, refilmed passed scenes, 72 chat-agent turns that did nothing ($4.13)

**Fixed (not tested yet)** · Round 7 run

- **Why it happened:** Found in the run's error log; the causes were fixed together and not written up one by one here.
- **What was changed:** 13 fixes Julian approved were built. (#286)
- **What happened next:** Not checked one by one.


## Left open

- Invented use: the app told the video model to lift the pore strip off with the extractor tool; in real life you peel it off by hand.
- Invented use: the clips twist the Dexas MudBuster; the page video shows it moving in and out, not twisting.
- Backgrounds, clothes and other people from page photos (and the page video) leaked into the clip; the app's own background picture was not used.
- The fixing agent's remake prompts were too long and detailed (296 words vs 121 for first clips) and could contradict each other.
- The fixing agent tweaked the prompt instead of fixing the root cause it could see (for example, just remove the leaked background).
- A photo with another person in it was sent next to the presenter picture (risky; the model copies the person).
- The picker didn't send the picture of the product's contents (Legion Pulse powder), so the video model never saw it.
- The judge failed clips Julian thought were fine (all Beautyblender scene 3 tries; Chipolo scene 2's phone-screen words).
- The judge failed Beautyblender scene 4 because the sponge 'did not blend the foundation'; Julian says that is not a fail reason.
- The judge said 'can't tell' and skipped its checks when it could have judged (Peace Out scene 2: the strip was visible on the nose).
- Julian could not understand the judge's ruling and many reasons on the pages.
- Wet Brush: the video model can't brush hair with this brush; it breaks the physics.
- Garject: most page photos mislead the video model, no reference video was given, and every B-roll clip failed.
- Our AI can't pick the right inputs by looking, as Julian can (he says it isn't intelligent enough).
- (found in the run, before grading) Safety filter refused starting pictures and a whole hidden-picture grid (Wet Brush: 9 photos lost)

## Caveats

- Julian tapped only 3 whole ads. 8 more fail because a clip inside them got a bad word (borderline).
- Many of his notes are about what was sent to the video model (too many or wrong pictures), not only what he saw. They still name a problem with that clip, so they count.
- Gymreapers had no B-roll and was not on the grading page; not counted.
- This dip led straight to the round 8 approach (back to a drawn, checked starting picture).

---

← [Round 6](round-06.md) · [All rounds](README.md) · [Round 8](round-08.md) →
