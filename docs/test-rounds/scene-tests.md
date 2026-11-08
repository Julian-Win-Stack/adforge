# Scene tests

Tests of single scenes or one feature, not whole rounds of ads. Their clip counts are kept apart from the ad rounds, because they test one hard scene or one idea, often many takes of the same prompt.

Grading rule and words: see [the summary](README.md#how-it-was-graded). Status words: [the summary](README.md#status-words). [Round 10](round-10.md) is also a scene test; it has its own page.

| Test | Date | Result |
|---|---|---|
| [Fix-from-a-note test](#fix-from-a-note-test) | October 12–14, 2026 | Items that work: 2 of 3 (67%) |
| [Round 4 clip-judge loop](#round-4-clip-judge-loop) | October 13–14, 2026 | Remakes that fixed the complaint: 4 of 11 (36%) |
| [Live ad: owner-notes test](#live-ad-owner-notes-test) | October 17, 2026 | Clips that work (strict rule): 2 of 3 (67%) |
| [Hand round 1: show, don't tell](#hand-round-1-show-dont-tell) | October 24, 2026 | Clips that work: 3 of 22 (14%) |
| [Hand round 2: reference video](#hand-round-2-reference-video) | October 25, 2026 | Clips that work: 0 of 11 (0%) |
| [Scene-helper prototype](#scene-helper-prototype) | November 3, 2026 | Takes that work: 15 of 15 (100%) |
| [Round 9 end-picture test](#round-9-end-picture-test) | November 4, 2026 | Takes that work: 14 of 20 (70%) |

## Fix-from-a-note test

*October 12–14, 2026*

- First test of fixing one scene from a shop owner's note: the app restates the fix and its cost, waits for a yes, remakes only that scene, and rebuilds the ad.
- Run on the round 4 toilet-cleaner ad: 2 fixes, 3 graded items (1 picture, 2 clips).

| Measure | Result |
|---|---|
| Items that work | 2 of 3 (67%) |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The note-fix changed the scrub direction as the note asked, but the brush still scrubs the toilet seat | 1 of 2 fixes | Works | Fixed (proven in a later round) |

Details, in the same order:

#### 1. The note-fix changed the scrub direction as the note asked, but the brush still scrubs the toilet seat

**Fixed (proven in a later round)** · 1 of 2 fixes

- **Why it happened:** The note only named the direction, so the fix rewrote only 'left to right' into 'right to left'; the seat scrub was a video-model slip the note never mentioned.
- **What was changed:** The clip judge + auto-redo caught and remade the seat scrub without a note; later the scene-fix flow was redesigned so a note-fix planner agent looks at the scene before deciding the fix (fix redesign, Oct 16-17). (#157, #155, #159)
- **What happened next:** Round 4 loop test: the auto-redo's first remake had no seat scrub, Julian 'fixed = yes' (Oct 14 15:10).


### Caveats

- Tests the note-fix feature, not a new ad. The two rebuilt ads were not graded.
- Fix 2 passed, but Julian said the old clip was already good enough.

## Round 4 clip-judge loop

*October 13–14, 2026*

- The new clip judge watched all 29 round 4 B-roll clips. Each clip it failed (7) was remade automatically up to twice, judged again, and a "fix check" compared old and new.
- Julian tapped whether each remake fixed the complaint and which take was best.

| Measure | Result |
|---|---|
| Remakes that fixed the complaint | 4 of 11 (36%) |
| Scenes where the app kept the take he would pick | 7 of 7 |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Le Duo: remakes with ever-stronger wording still can't turn the tool while gliding (scene 2, scene 4) | 4 of 4 Le Duo twist remakes partly or not fixed | Works | Known limit |
| 2 | Le Duo scene 3: every remake keeps the unnatural hair coming from the mannequin's chin | 2 of 2 remakes | Works | Not fixed |
| 3 | Phone case scene 2: the first remake still had the phone fault (extra lenses after the drop) | 1 remake (remake 2 fixed it) | Works | Fixed (proven in a later round) |
| 4 | The fix check (old clip vs new clip) said 'not fixed' on toilet cleaner scene 4 and Merit balm scene 4, where Julian says the remake fixed it; he tapped that he disagrees on Merit balm scene 4 | 2 of 7 fix checks wrong | Works | Fixed (not tested yet) |

Details, in the same order:

#### 1. Le Duo: remakes with ever-stronger wording still can't turn the tool while gliding (scene 2, scene 4)

**Known limit** · 4 of 4 Le Duo twist remakes partly or not fixed

- **Why it happened:** A video-model limit: it tilts the tool instead of turning it during a glide; the prompt was already explicit.
- **What was changed:** Stop rewording; record it as a video-model limit (the planner avoids filming the twist).
- **What happened next:** Le Duo not tested again; hair tools are on the video-model weakness list (Wet Brush given up in round 7).

#### 2. Le Duo scene 3: every remake keeps the unnatural hair coming from the mannequin's chin

**Not fixed** · 2 of 2 remakes

- **Why it happened:** The fault is in the start picture, and the auto-redo remakes from the same start picture.
- **What was changed:** No fix found in this period (remakes from the same picture can't fix a picture fault).
- **What happened next:** Not fixed; Le Duo was not tested again.

#### 3. Phone case scene 2: the first remake still had the phone fault (extra lenses after the drop)

**Fixed (proven in a later round)** · 1 remake (remake 2 fixed it)

- **Why it happened:** Random video-model fault; each remake is a new roll.
- **What was changed:** The auto-redo's second try (cap of 2 remakes). (#155, #159, #157)
- **What happened next:** Remake 2 'fixed = yes', his pick.

#### 4. The fix check (old clip vs new clip) said 'not fixed' on toilet cleaner scene 4 and Merit balm scene 4, where Julian says the remake fixed it; he tapped that he disagrees on Merit balm scene 4

**Fixed (not tested yet)** · 2 of 7 fix checks wrong

- **Why it happened:** The fix check was its own stricter model call, separate from the judge.
- **What was changed:** The fix check became the clip judge itself with one extra question (was the owner's change made). (#165)
- **What happened next:** No graded re-test in this period. Later owner-photo test: fix check right 8 of 8.


### Caveats

- Not a new ad round: 11 automatic remakes of 7 round 4 clips.
- A "yes, fixed" tap counts as pass; "partly" and "no" count as fail.
- The judge on first clips: of the 7 it failed, he had failed 4 and passed 3 (he later agreed on 2 of those 3). It missed 2 of his fails.

## Live ad: owner-notes test

*October 17, 2026*

- One real ad (the toilet cleaner) with three owner notes run through the new note-fix planner. It tests whether notes get fixed, not ad quality.
- Talking scenes were faked as still pictures with a voice, to save money.

| Measure | Result |
|---|---|
| Clips that work (strict rule) | 2 of 3 (67%) |
| Owner notes that did what was asked | 3 of 3 (all tapped pass) |
| Whole ad | 0 of 1 |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Scene 2: the toilet cleaner pours down like water; it should look like a gel | 1 scene | Works | Known limit |
| 2 | The presenter does not seem to speak; it looks like a still picture | All talking scenes | Works | Dropped |

Details, in the same order:

#### 1. Scene 2: the toilet cleaner pours down like water; it should look like a gel

**Known limit** · 1 scene

- **Why it happened:** The video model makes up what contents look like; nothing told it the cleaner's texture.
- **What was changed:** Not fixed for this test (Julian: 'this is not ad creation testing... Don't stress'). The same kind of fault is recorded as a video-model limit ('makes up what contents look like'), and the later show-don't-tell work sends reference pictures instead of a start picture.
- **What happened next:** Contents-look faults came back in round 6 (Glow Recipe serum, Beautyblender foundation).

#### 2. The presenter does not seem to speak; it looks like a still picture

**Dropped** · All talking scenes

- **Why it happened:** Test setup, not the app: talking scenes were faked on purpose as a still picture plus voice to save money (local-only patch).
- **What was changed:** No app fix needed; real talking scenes are made in normal runs.
- **What happened next:** No later graded round has checked it yet.


### Caveats

- Julian called it "not ad creation testing".
- The scene 2 clip fails because his whole-ad note says the cleaner pours like water.
- The "presenter does not speak" complaint comes from the faked talking scenes, a test shortcut, not an app fault.

## Hand round 1: show, don't tell

*October 24, 2026*

- Single B-roll clips sent to the video model by hand, outside the app: no starting picture, raw page photos as labelled references, one-sentence prompts.
- Julian edited prompts and re-ran them on 4 products (Cocofloss, Glow Recipe, Wet Brush, Zulay).

| Measure | Result |
|---|---|
| Clips that work | 3 of 22 (14%) |
| By his first grades only | 6 of 22 |
| By product | Glow Recipe 3 of 5 · Cocofloss 0 of 4 · Wet Brush 0 of 11 · Zulay 0 of 2 |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Floss did not behave like floss: it came out as a stiff stick, a strand lying still, or the whole spool sitting on the tooth model. | 4 of 4 Cocofloss clips (3 prompts) | Works | Known limit |
| 2 | Glow serum: the video model made up what the bottle looks like with its lid off. | 1 clip (scene 3, take 2) | Works | Partly fixed |
| 3 | Wet Brush: the clip brushed the hank of hair that was drawn inside the room picture; no woman brushing her own hair. | 2 clips (scene 3, prompt 1, takes 1 and 2) | Works | Came back |
| 4 | Wet Brush prompt 2: the hair from the room picture merged with the woman's own hair; 'kind of natural but not that natural'. | 2 clips (scene 3, prompt 2, takes 1 and 2) | Works | Came back |
| 5 | Wet Brush: the video model invented the brush's back, with bristles on both sides (the real brush has a plain back). | 3 clips (prompt 2 takes 1 and 2, prompt 3 take 1) | Works | Partly fixed |
| 6 | Wet Brush: two brushes appeared at once; the second brush had bristles on both sides. | 2-3 clips (prompt 4 takes) | Works | Known limit |
| 7 | Wet Brush: bristles appeared on the back when she lifted the brush and disappeared when it came down. | Prompt 4 takes (counted with the line above) | Works | Partly fixed |
| 8 | Wet Brush: the brush jumped from her left hand to her right hand after one stroke. | 3 clips (prompt 5 takes, near identical) | Works | Known limit |
| 9 | Clips sometimes come out too fast when the prompt doesn't say the speed. | General remark, not tied to one clip | Works | Fixed (proven in a later round) |
| 10 | Clips with no complaint at first that Julian later agreed were failing (Zulay rinse under the tap, both takes; Glow prompt 2 take 1). The judge named the product changing and unreal motion. | 3 clips | Works | Not fixed |
| 11 | The photo picker missed or filtered out important page photos (the Glow serum-on-skin collage and the glowing face). | 1 product (Glow), found while preparing the round | Works | Partly fixed |

Details, in the same order:

#### 1. Floss did not behave like floss: it came out as a stiff stick, a strand lying still, or the whole spool sitting on the tooth model.

**Known limit** · 4 of 4 Cocofloss clips (3 prompts)

- **Why it happened:** The video model does not know how a thin, limp thread behaves between fingers. Rewording (short prompt, 'show it working', a line describing the thread) changed nothing.
- **What was changed:** No prompt fix. Julian's rule: when the video model cannot do an action, the scene shows another selling action or only the end result (two of Julian's written design rules). Later the app stopped filming actions the video model can't do.
- **What happened next:** Floss was not filmed again in a graded round. Round 9: "almost all the ads pass. It's just because we didn't film the risky and hard actions."

#### 2. Glow serum: the video model made up what the bottle looks like with its lid off.

**Partly fixed** · 1 clip (scene 3, take 2)

- **Why it happened:** No picture of the open bottle was sent, so the model guessed the pump and drip.
- **What was changed:** Send the page's lid-off photo, labelled ('picture 2 is the same bottle with the lid off'). Became one of Julian's written design rules (send every product state the page shows); on Oct 28 Julian narrowed it to sides plus the states a scene needs.
- **What happened next:** Same round: Glow prompt 2 with the lid-off photo had no lid complaint; 2 of 3 takes pass, take 1 failed later in the judge test (no words, judge named product and motion faults). Glow was not in a later graded round.

#### 3. Wet Brush: the clip brushed the hank of hair that was drawn inside the room picture; no woman brushing her own hair.

**Came back** · 2 clips (scene 3, prompt 1, takes 1 and 2)

- **Why it happened:** The round 6 room picture still had a hanging hank of hair in it, and the prompt never said so. The video model copies what is in the pictures.
- **What was changed:** By hand: a clean room picture with no hair (prompt 3). In the app, two written design rules: know what is in each picture (marked 'maybe'), and the fixing agent may edit the room picture. From Nov 3 every B-roll clip starts from a drawn and checked starting picture instead of raw pictures. (#324)
- **What happened next:** Came back in round 7: backgrounds and people from page pictures leaked into clips (Beautyblender scene 2, Chipolo scene 3). Round 8 (drawn-starting-picture build) has no background-leak complaint.

#### 4. Wet Brush prompt 2: the hair from the room picture merged with the woman's own hair; 'kind of natural but not that natural'.

**Came back** · 2 clips (scene 3, prompt 2, takes 1 and 2)

- **Why it happened:** Same cause: a thing inside the room picture (the hank) was copied into the clip.
- **What was changed:** Same as above: room picture with no hair (prompt 3); later the fixing agent may edit the room picture; later the drawn-starting-picture design. (#324)
- **What happened next:** Prompt 3 had no hair merge. Same family came back in round 7 (page-photo backgrounds leaking).

#### 5. Wet Brush: the video model invented the brush's back, with bristles on both sides (the real brush has a plain back).

**Partly fixed** · 3 clips (prompt 2 takes 1 and 2, prompt 3 take 1)

- **Why it happened:** First the back photo was not labelled as the back. With labels, the model still invented it. Creatify's automatic prompt rewrite also seemed to drop the priority line.
- **What was changed:** Two written design rules: label every product picture by its side, and lead the prompt with the one fact as a priority line. With Creatify's rewrite off, the double-sided bristles went away. Rewrite turned off in the app Nov 2. Later: a 'lock line' in every prompt naming parts that must stay, and the clip judge's product check. (#355)
- **What happened next:** Prompt 4 kept the back plain at rest; prompt 5 (rewrite off) had no double-sided bristles. Wet Brush still failed in round 7 and round 8 for other reasons.

#### 6. Wet Brush: two brushes appeared at once; the second brush had bristles on both sides.

**Known limit** · 2-3 clips (prompt 4 takes)

- **Why it happened:** The video model has no object permanence: when the brush is hidden or blurred it can come back as a second brush.
- **What was changed:** No prompt fix found ('We have tried everything we can'). Left to the clip judge to catch; Wet Brush was given up in round 7.
- **What happened next:** Round 7 Wet Brush whole ad: "I give up for this one". Round 8 Wet Brush whole ad failed.

#### 7. Wet Brush: bristles appeared on the back when she lifted the brush and disappeared when it came down.

**Partly fixed** · Prompt 4 takes (counted with the line above)

- **Why it happened:** Same object-permanence habit of the video model; the look flickers between frames.
- **What was changed:** Turning off Creatify's prompt rewrite removed the double-sided bristles in prompt 5. Clip judge's product check looks at frames for a changed product. (#355)
- **What happened next:** Prompt 5: 'the spikes appearing on both sides... Has disappeared'. Not seen again in a graded Wet Brush clip; product dropped.

#### 8. Wet Brush: the brush jumped from her left hand to her right hand after one stroke.

**Known limit** · 3 clips (prompt 5 takes, near identical)

- **Why it happened:** The video model draws each moment as a likely picture; it does not track one brush in one hand. Not a wording problem.
- **What was changed:** No fix; a judge check ('the product changes hands or jumps'). Product later given up.
- **What happened next:** No later test.

#### 9. Clips sometimes come out too fast when the prompt doesn't say the speed.

**Fixed (proven in a later round)** · General remark, not tied to one clip

- **Why it happened:** The video model speeds up and crams steps unless told to film in real time.
- **What was changed:** Every prompt opens with only 'Film this in real-time speed.' (a written design rule); now in code as the opener of every video prompt.
- **What happened next:** It is a proven rule ('Only real-time speed from the opener'); no speed complaint in the round 7 grades.

#### 10. Clips with no complaint at first that Julian later agreed were failing (Zulay rinse under the tap, both takes; Glow prompt 2 take 1). The judge named the product changing and unreal motion.

**Not fixed** · 3 clips

- **Why it happened:** Not stated by Julian; only the judge's checks (product, real motion, sudden change). These clips are why he wanted an automatic judge.
- **What was changed:** No product-specific fix. The judge test led to the clip judge being kept in the app ('the current version is okay... Try with GPT first'); judge rules changed later (compare with the product references).
- **What happened next:** No later test of these scenes.

#### 11. The photo picker missed or filtered out important page photos (the Glow serum-on-skin collage and the glowing face).

**Partly fixed** · 1 product (Glow), found while preparing the round

- **Why it happened:** Not a fetch bug: the collage was fetched but the photo picker's rules dropped photos without the product in them (before/after, skin close-ups).
- **What was changed:** Picker keeps every gallery picture except word-only cards and labels what each shows (three written design rules), built with the new video-model inputs. (#210, #286)
- **What happened next:** Round 7: Julian still flagged the photo picker missing a needed content picture (Legion Pulse powder).


### Caveats

- Many clips are repeat takes of one prompt, so 22 clips are fewer than 22 independent tries.
- A day later he agreed with the judge that 3 clips he had left ungraded were failing. The later grade wins.
- Quality was not graded.

## Hand round 2: reference video

*October 25, 2026*

- First test of sending the product page's own video as a movement reference, by hand, on one hard scene (the Garject garlic press, scene 2).
- Then a colour control: the same scene with the product photo recoloured to match the video.
- Clips are named by input set: A (one photo plus the video), A2 (black photo, red video frames), A3 (everything in red, plus two frames cut from the video) and B (A plus extra page photos of other states).

| Measure | Result |
|---|---|
| Clips that work | 0 of 11 (0%) |
| By his chat grades at the time | 4 of 11 ("perfect", "all the clips pass") |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The video model made up an extra handle on top of the Garject (it has one long handle on top). | 1 clip (A) | Works | Came back |
| 2 | Garlic came out in big pieces instead of small fine bits through the grill. | 1 clip (A) | Works | Came back |
| 3 | Extra page photos of other states (chamber open, scraper clearing, peel pushed out) made the model invent how the Garject is used and ignore the reference video. | 1 clip (B) | Works | Came back |
| 4 | With a black photo and red video frames, the model invented a different product (wrong press head). | 1 clip (A2), plus the black-recolour control: 4 of 4 not passed (2 fail taps, 1 unsure, 1 untapped) | Works | Partly fixed |
| 5 | The 'perfect' all-red clips (A3 and its repeats): later the same day Julian agreed with the judge that they fail (judge named the product changing and unreal motion); on one he said pass or fail are both okay. | 4 clips | Works | Known limit |

Details, in the same order:

#### 1. The video model made up an extra handle on top of the Garject (it has one long handle on top).

**Came back** · 1 clip (A)

- **Why it happened:** Only one photo (the black press, side view) went with a red page video; the model blended the two and had no picture of the handles in use.
- **What was changed:** Add two frames Julian cut from the page video (hands on the long handles; mince coming through) and make every reference the same colour as the video, with 'the first picture owns the look, Video 1 only the movement'.
- **What happened next:** Same round: A3 and 3 repeats, 4 of 4 pass by his chat ('perfect'); the same day in the judge test he agreed with the judge's fails on 3 of them and said either was okay on the 4th. In the app (round 7) the Garject got a third handle again (the open press was forced as the first picture, and no video was sent) and every B-roll clip failed.

#### 2. Garlic came out in big pieces instead of small fine bits through the grill.

**Came back** · 1 clip (A)

- **Why it happened:** No picture showed how the mince comes out.
- **What was changed:** A frame from the page video showing the small mince, labelled 'small fine bits through the grill'.
- **What happened next:** A3 and repeats passed (4 of 4). Round 7 Garject: large slices fell (scene 4 first clip), so it came back in the app.

#### 3. Extra page photos of other states (chamber open, scraper clearing, peel pushed out) made the model invent how the Garject is used and ignore the reference video.

**Came back** · 1 clip (B)

- **Why it happened:** The video model gives photos more weight than the video; state photos that are not the scene's action mislead it.
- **What was changed:** For a gadget, send only the states the action needs (a written design rule). On Oct 28 states stopped being sent by default at all. From Nov 3 the scene helper picks the few pictures it needs and films from a checked starting picture. (#324)
- **What happened next:** Round 7 Garject: Julian again said the photos sent were 'really misleading'. Round 8 Garject failed again (Julian: 'even creatify fails this').

#### 4. With a black photo and red video frames, the model invented a different product (wrong press head).

**Partly fixed** · 1 clip (A2), plus the black-recolour control: 4 of 4 not passed (2 fail taps, 1 unsure, 1 untapped)

- **Why it happened:** References in two colours disagree; the model takes the look from the video and blends.
- **What was changed:** New one-colour rule: every picture and the page video show the product in one colour; the video's colour wins; other photos are recoloured with the picture model, never dropped. Built as an automatic recolour step; redesigned after round 7 (#273). (#286)
- **What happened next:** By hand, first grades: all-red A3 4 of 4 pass vs black control 0 of 4; Julian called the one-colour rule proven. In the later judge test he agreed with the judge's fails on the all-red clips too, which weakens that proof. In the app round 7: all 24 recolours fell back, so wrong-colour pictures were sent (red frother for a black product). The redesign has not been run on real pictures (the video-model limits list).

#### 5. The 'perfect' all-red clips (A3 and its repeats): later the same day Julian agreed with the judge that they fail (judge named the product changing and unreal motion); on one he said pass or fail are both okay.

**Known limit** · 4 clips

- **Why it happened:** Not stated by Julian. Shows how hard the Garject is for the video model even with the best hand-picked inputs.
- **What was changed:** No fix for these clips. Garject later treated as a product the video model can't do: round 8 'ignore this specific product for a while. Even creatify fails this'; round 9 'this product is normal to fail'.
- **What happened next:** Garject failed again in rounds 7, 8 and 9.


### Caveats

- One product, one scene, made by hand.
- Later that day he agreed with the judge that the 4 "perfect" clips fail; the later grade wins. He did not say what was wrong; the judges named product and motion faults.
- The 4 "perfect" clips are one prompt filmed 4 times, to rule out luck.

## Scene-helper prototype

*November 3, 2026*

- First test, outside the app, of giving each B-roll scene its own AI helper: it draws and checks a starting picture, writes the video prompt, and the video model films from that picture only.
- 5 old round 7 scenes, 3 takes each.

| Measure | Result |
|---|---|
| Takes that work | 15 of 15 (100%) |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Junk photos were sent to the picture model: every library photo went in with the same line, and with a big library only the first 15 by number (the pre-workout got a comparison table, a certificate, a label panel). Julian: 'this is junk. We are sending junk photos to the image gen api'. | 1 of 5 scenes seen (pre-workout); the rule applied to every scene | Works | Fixed (not tested yet) |

Details, in the same order:

#### 1. Junk photos were sent to the picture model: every library photo went in with the same line, and with a big library only the first 15 by number (the pre-workout got a comparison table, a certificate, a label panel). Julian: 'this is junk. We are sending junk photos to the image gen api'.

**Fixed (not tested yet)** · 1 of 5 scenes seen (pre-workout); the rule applied to every scene

- **Why it happened:** The approved design said 'every product photo goes to the picture model', and code cut the list at 15 by position. Nothing chose photos by what the scene needed.
- **What was changed:** The scene helper now picks every photo it sends after looking at them, and writes one line per photo on what to take from it: every side and every part the picture or clip shows. At most 15, refused (never cut) when over. The picture check and clip judge get exactly the same set. (#299, #324, #314)
- **What happened next:** Rounds 8 and 9 raised no complaint about junk photos. Round 10 found a related bug: an edited starting picture kept only that edit's photos, so the judge lost the tube photo (fixed by PR #378).


### Caveats

- No whole ads, no clip judge, no retries: every first picture passed its check.
- All 15 grades come from one chat message ("all clips are good and pass").
- This result led directly to the round 8 build.

## Round 9 end-picture test

*November 4, 2026*

- 3 round 9 scenes that failed on how the product looks once used (foundation, concealer, cleansing gel), re-filmed with an end picture of the finished look.
- Four ways compared: start + end picture with bans in the prompt (A), reference mode (B), start + end with positive-only wording (P), and Creatify's suggested prompt (C).

| Measure | Result |
|---|---|
| Takes that work | 14 of 20 (70%) |
| By method | A: 6 of 6 · B: 2 of 6 · P: 4 of 6 · C: 2 of 2 |

### Every failure, and what happened to it

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Round 9's makeup and gel scenes failed on the product's state (foundation turned into patches; gel turned into white lather). | 3 scenes from round 9 | Works | Partly fixed |
| 2 | With an end picture, the foundation just disappeared to match the end, with the hand not causing it: 'basically breaking physics' (Beautyblender scene 2, P takes). | 2 takes (both P takes on Beautyblender scene 2) | Works | Partly fixed |
| 3 | Reference mode (start and end sent as example pictures) follows the end picture only sometimes. | General note on the B takes | Works | Fixed (proven in a later round) |

Details, in the same order:

#### 1. Round 9's makeup and gel scenes failed on the product's state (foundation turned into patches; gel turned into white lather).

**Partly fixed** · 3 scenes from round 9

- **Why it happened:** The video model has to invent the finished state of the product on skin; nothing showed it the end.
- **What was changed:** End pictures in the app: after a product-check fail the scene helper edits its starting picture into the finished state and sends it as the video model's last frame (start+end mode, which Julian found 'guaranteed' to follow, unlike reference mode). Later the tool is open from the first try. (#370–#374, #375)
- **What happened next:** Round 10 (same scenes, in the app): Beautyblender scene 3 passed on try 3 with end pictures, Beautyblender scene 2 gave up again; Krave scene 3 ended unsure with invented content (clip 4137, a photo bug). Julian: 'it works' (Nov 5 04:10).

#### 2. With an end picture, the foundation just disappeared to match the end, with the hand not causing it: 'basically breaking physics' (Beautyblender scene 2, P takes).

**Partly fixed** · 2 takes (both P takes on Beautyblender scene 2)

- **Why it happened:** Not proven. Best guess (one scene): nothing put the sponge on the foundation in the pictures or the prompt, and the positive end-state wording kept the foundation on screen until after the sponge had left, so the end picture wiped it with no cause. Julian: 'using this ending pic is really risky and the prompt really need to compliment the things'.
- **What was changed:** The code ban on negative words in video prompts was dropped: the prompt describes the result as a finished state backed by a short 'no X'. Creatify's prompt advice (cause placed under the action) was tested: both C takes passed ('it works'). (#375)
- **What happened next:** C takes passed (Nov 4 19:48). Round 10 Beautyblender scene 2 still gave up with end pictures; no later grade names this exact fault.

#### 3. Reference mode (start and end sent as example pictures) follows the end picture only sometimes.

**Fixed (proven in a later round)** · General note on the B takes

- **Why it happened:** In reference mode the video model redraws the first frame and treats the pictures as loose guides.
- **What was changed:** Use start+end-picture mode for end pictures; reference mode only for page-video scenes. (#375)
- **What happened next:** Round 10: every end-picture try was sent as start + end picture.


### Caveats

- Takes of the same input come out nearly identical, so 20 takes are closer to 7 samples.
- 4 untapped reference-mode takes count as fails from his note that reference mode follows the end picture "only sometimes" (borderline).
- The prompts in A and B used banned words the app then refused; that rule was dropped after this test.

---

[All rounds](README.md)
