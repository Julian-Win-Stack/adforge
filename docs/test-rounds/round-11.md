# Round 11: Practice round on 10 new products (November 5–6, 2026)

## What this round tested

- 10 products never tested before, from the product link only, with end pictures and the round 9–10 fixes.
- Products: AeroPress coffee maker, Bondi Sands self-tan foam, BlenderBottle shaker, SodaStream Terra, Chemical Guys car-wash foam, Hypervolt 2 massage gun, Ooni Koda 2 pizza oven, Liquid I.V. drink mix, Revlon One-Step hair-dryer brush, Bissell Little Green spot cleaner.
- First round where Julian judged ad quality as well as "works".

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 6 of 10 (60%); 6 of 8 made (75%) | 8 of 8 made ads tapped pass |
| Good ad (quality) | 0 of 8 | — |
| B-roll clips in the final ads that work | 2 of 4 (50%) | — |
| Talking clips in the final ads that work | 28 of 28 (100%) | — |
| Every take shown (works bar) | 31 of 45 | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers (for example clip 3839) are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | Ads feel too slow: the presenter speaks slowly. First said on AeroPress, then in chat: "it happens for almost all the ads. I want it to be a little bit faster". | 8 of 8 made ads (he said almost all; measured 1.9-2.1 words a second in all 8) | Good ad | Partly fixed |
| 2 | The presenter looks pasted onto the background, like an edited picture, and the video copies it (AeroPress whole ad; Bissell scene 1 clip 4252; chat). | 2 ads named (AeroPress, Bissell); he described it as general in chat | Good ad | Fixed (not tested yet) |
| 3 | No B-roll, or far too little: "why no b roll?" (Bondi), "no b roll scene" (BlenderBottle), and in chat "we should add more b rolls... As many b roll as possible and make each b roll to have a purpose and meaning". | 8 of 8 made ads: 28 of 32 scenes were talking; B-roll was 13% of ad time; 4 ads had none | Good ad | Partly fixed |
| 4 | Talking scenes are boring and all look the same: "a lot of boring talking scenes that does nothing"; "the person just plainly keep talking" (Bondi); "we just hold the same exact product in the hand in the same exact position in the same exact style of the camera angle". | 8 of 8 made ads (25 of 28 talking scenes just hold the product up to the lens) | Good ad | Partly fixed |
| 5 | When a real demo is hard, the ad falls back to more of the same talking; Julian wants at least one scene built around the product and the planner to "think of a creative way to show and advertise the product" (Bondi; chat, floss example). | Bondi named; general in chat | Good ad | Partly fixed |
| 6 | Make great B-roll even when the product page has few photos that show the product's effect (Bondi note; chat). | General request (Bondi named) | Good ad | Not fixed |
| 7 | When a clip fails, the agent keeps rewording the prompt instead of changing the starting picture or using an end picture, so it re-films the same fault (AeroPress scene 3, BlenderBottle scene 2, Chemical Guys clip 4301, Revlon clip 4239). "The prompts it's not going to fix, and the agent will always lean into tuning the prompt." | 4 of 8 made ads / about 8 failed tries | Works | Fixed (not tested yet) |
| 8 | AeroPress scene 3: the filter cap grows into a long cylinder as the plunger presses, on tries 1 and 2 (clips 4235, 4264): "this is the same in all the clips for this one". | 1 ad / 2 clips | Works | Fixed (not tested yet) |
| 9 | AeroPress scene 3's passing take dropped the clear cup used in tries 1 and 2: "it would be better if we could do this with the clear cup like clip 1 and 2". | 1 clip (4274) | Good ad | Not fixed |
| 10 | BlenderBottle scene 2: a made-up printed ruler on the bottle on tries 1-3 (clips 4327, 4331, 4334); "why it doesn't see the fault is in the starting pic". | 1 ad / 3 clips | Works | Fixed (not tested yet) |
| 11 | Chemical Guys B-roll (clip 4301, used in the ad): bad movement; the bottle moves along with the camera as it closes in on the car; starting picture and prompt don't agree. The judge passed it. | 1 ad / 1 clip | Works | Fixed (not tested yet) |
| 12 | Chemical Guys: the round skull-and-tools logo and product name on the label are redrawn or lose parts (clips 4292, 4299, 4296, 4303; tapped fail, no words). | 1 ad / 4 clips (all were retried and replaced before the ad) | Works | Known limit |
| 13 | Liquid I.V. clip 4198: the pack is held in the middle distance, so its letters garble (and the judge saw the wrong stick count). Julian: show labels very close or very far, never in between; letters change even on passing clips; try spelling the label letters to the video model. | 1 ad / 1 clip named; he said 'we are facing a lot of those failures' | Works | Known limit |
| 14 | Liquid I.V. B-roll (clip 4250, used in the ad): powder is poured from a stick pack that was never torn open. "It started from the starting picture." | 1 ad / 1 clip | Works | Partly fixed |
| 15 | Revlon scene 4: the barrel's long air slots turn into round holes as the brush comes toward the lens (clips 4189 tapped fail, 4239 with note); the agent should have used an end picture instead of tuning the prompt. | 1 ad / 2 clips | Works | Fixed (not tested yet) |
| 16 | Two ads (SodaStream Terra, Ooni Koda 2) gave up at planning and made no video. | 2 of 10 ads | Works | Fixed (proven in a later round) |

Details, in the same order:

### 1. Ads feel too slow: the presenter speaks slowly. First said on AeroPress, then in chat: "it happens for almost all the ads. I want it to be a little bit faster".

**Partly fixed** · 8 of 8 made ads (he said almost all; measured 1.9-2.1 words a second in all 8)

- **Why it happened:** Each clip was given more seconds than its line needed, and the video model stretches the speech to fill the clip. The voice description often asked for 'an easy, unhurried pace' (4 of 8 ads). Clips also opened on silence (Bondi: 1.7 s before the first word).
- **What was changed:** Lines are planned at 2.8 words a second; clip length is set from the line with little padding; plans whose voice asks for a slow pace are sent back; silence before the first word is cut. Later (after round 12): short, high-energy hooks, dead air after the last word cut, and warmer, chattier voice words. (#401, #407, #420, #435, #446, #458–#463)
- **What happened next:** Round 12 hooks measured about 2.5 words a second (up from about 2.0). But Julian still wrote on round 12: Revlon hook "spoken too slow", round note "the hook should be punchy, and shorter... Higher energy", and AeroPress had "a few awkward stall 1-2 seconds at the end of the clip 3". The later fixes (hooks, dead air) are not graded yet.

### 2. The presenter looks pasted onto the background, like an edited picture, and the video copies it (AeroPress whole ad; Bissell scene 1 clip 4252; chat).

**Fixed (not tested yet)** · 2 ads named (AeroPress, Bissell); he described it as general in chat

- **Why it happened:** The shared 'presenter in her room' picture was made by merging a finished portrait with a finished room picture, with the pose 'stands straight and still, hands at their sides'. Every talking starting picture is an edit of that one picture, so the pasted look spread to every talking scene.
- **What was changed:** The presenter and her room are now drawn together in one picture call (her portrait used only for her face), with realism words (the room's own light, no ring light, no beauty filter) and a relaxed pose. The B-roll room is that picture with her taken out. (#401, #405, #416)
- **What happened next:** Round 12 (12 ads, made after the fix): Julian's notes don't mention a pasted look. But round 12 was graded whole-ad only and he said he hadn't looked deeply; the video-model limits doc still says 'Not graded yet since the fix'.

### 3. No B-roll, or far too little: "why no b roll?" (Bondi), "no b roll scene" (BlenderBottle), and in chat "we should add more b rolls... As many b roll as possible and make each b roll to have a purpose and meaning".

**Partly fixed** · 8 of 8 made ads: 28 of 32 scenes were talking; B-roll was 13% of ad time; 4 ads had none

- **Why it happened:** The planner allowed only 3-4 whole-clip scenes, required scene 1 to be talking, and turned any line where she could hold the product into a talking scene. B-roll, when present, was always scene 3.
- **What was changed:** Middle scenes are now B-roll by default and every ad must have at least one B-roll scene centred on the product; plans that break this are sent back. A 'every scene proves its line' check was added (PR #418) and later removed when Julian went back to the round 2 B-roll rule (PR #438). Silent B-roll shots laid over her voice were discussed and approved, then rejected in the spec (Creatify's own ads had 3-4 clips). (#401, #403, #421, #404, #411, #422)
- **What happened next:** Round 12 ads had B-roll in the middle, and Julian called the whole set 'a huge improvement'. But Bondi round 12: "the one b roll scene is useless. It means nothing."

### 4. Talking scenes are boring and all look the same: "a lot of boring talking scenes that does nothing"; "the person just plainly keep talking" (Bondi); "we just hold the same exact product in the hand in the same exact position in the same exact style of the camera angle".

**Partly fixed** · 8 of 8 made ads (25 of 28 talking scenes just hold the product up to the lens)

- **Why it happened:** Every talking picture was an edit of one picture of her standing still, and the planner let 'show the product to the lens' count as a talking scene's action anywhere in the ad.
- **What was changed:** Each talking scene now names a talking style (selfie, propped waist-up, seated close-up, talking while doing it) and a product state (in its box, out of the box, in use, beside its box); two talking scenes in a row can't share either. 'Show to the lens' is allowed only in the first or last scene. No mirrors, no walking. (#401, #403, #421, #411, #422)
- **What happened next:** Round 12 Bondi: "there were 3 talking scenes and the three talking scenes different a bit but that's all there is." Better, not solved; later quality work (Nov 7 PRs #458-#468) not graded yet.

### 5. When a real demo is hard, the ad falls back to more of the same talking; Julian wants at least one scene built around the product and the planner to "think of a creative way to show and advertise the product" (Bondi; chat, floss example).

**Partly fixed** · Bondi named; general in chat

- **Why it happened:** Nothing in the planner asked for a product-centred scene when the product's use is hard to film, so it chose a talking scene.
- **What was changed:** Every ad needs at least one B-roll scene centred on the product; when a real demo is hard, the planner picks the moment before, the feature up close, or the feeling after (the floss pulled out of its box is the example). (#403, #421)
- **What happened next:** Round 12 Bondi's one B-roll was 'useless... Means nothing'; CATAN round 12 failed because the ad never made clear it was a board game.

### 6. Make great B-roll even when the product page has few photos that show the product's effect (Bondi note; chat).

**Not fixed** · General request (Bondi named)

- **Why it happened:** With only packshots, the planner had nothing to build a showing scene from, so it chose talking.
- **What was changed:** No fix found that targets thin pages specifically. Creatify's agents gave a 'thin pages' shot menu (texture close-up, hands using it, reaction); the plan noted it as mostly already built, and the new 'moment before / feature up close / feeling after' wording in PR #421 is the nearest change.
- **What happened next:** Not checked separately.

### 7. When a clip fails, the agent keeps rewording the prompt instead of changing the starting picture or using an end picture, so it re-films the same fault (AeroPress scene 3, BlenderBottle scene 2, Chemical Guys clip 4301, Revlon clip 4239). "The prompts it's not going to fix, and the agent will always lean into tuning the prompt."

**Fixed (not tested yet)** · 4 of 8 made ads / about 8 failed tries

- **Why it happened:** The scene helper was free to retry with the same starting picture; same-picture retries failed 5 of 6 times while a new picture fixed 4 of 6. The end-picture tool only opened after a product-check fail, so it was rarely offered.
- **What was changed:** Filming now refuses a starting picture (and end picture) whose last clip the judge failed, so a retry needs a new picture. The fix order (new starting picture first, then end picture or a different action) is told once at the first fail; end pictures work from the first try. The 6-try cap stays. (#401, #408, #415, #417, #470)
- **What happened next:** Round 12 had no clip-by-clip grades, so retries weren't checked. Julian's round 12 note says failures "made from the starting pic" are still important to fix.

### 8. AeroPress scene 3: the filter cap grows into a long cylinder as the plunger presses, on tries 1 and 2 (clips 4235, 4264): "this is the same in all the clips for this one".

**Fixed (not tested yet)** · 1 ad / 2 clips

- **Why it happened:** The video model reshaped the cap during the press; the agent retried with reworded prompts on the same picture. Julian thought an end picture on tries 1 and 2 would have fixed it.
- **What was changed:** Covered by the new fix order (new picture or end picture after a judge fail, end pictures from the first try). (#415, #408)
- **What happened next:** Round 12 AeroPress passed; his only note on scene 3 was a 1-2 s stall at the end, not the cap. Clips weren't graded one by one.

### 9. AeroPress scene 3's passing take dropped the clear cup used in tries 1 and 2: "it would be better if we could do this with the clear cup like clip 1 and 2".

**Not fixed** · 1 clip (4274)

- **Why it happened:** The agent drew a new starting picture with a different cup; nothing keeps props the same between tries or scenes.
- **What was changed:** No fix found. Round 12 raised the bigger version (props and product size drifting between scenes) and a continuity sheet was proposed, not built.
- **What happened next:** Round 12 Gorilla Glue and KONG failed partly on drifting props and product size between scenes.

### 10. BlenderBottle scene 2: a made-up printed ruler on the bottle on tries 1-3 (clips 4327, 4331, 4334); "why it doesn't see the fault is in the starting pic".

**Fixed (not tested yet)** · 1 ad / 3 clips

- **Why it happened:** The fault was already in the starting picture (molded scale drawn as a printed ruler), and the agent kept the picture and changed only the prompt.
- **What was changed:** New fix order (a judge-failed picture can't be filmed again, PR #415); the picture check now fails a picture showing a side or part no photo shows (PR #414). (#408, #406)
- **What happened next:** Round 12 BlenderBottle note was about an unnatural finger grip in the last scene, not the ruler. Not checked clip by clip.

### 11. Chemical Guys B-roll (clip 4301, used in the ad): bad movement; the bottle moves along with the camera as it closes in on the car; starting picture and prompt don't agree. The judge passed it.

**Fixed (not tested yet)** · 1 ad / 1 clip

- **Why it happened:** The picture and the prompt described different set-ups, and the video model followed both; the judge then (Nov 4) had no motion questions, which had been removed as too strict (PR #350).
- **What was changed:** Same fix order as above (fix the starting picture, not just the words). The judge's motion questions were not restored for this round. (#415)
- **What happened next:** Round 12 Chemical Guys passed with no note on its B-roll (he asked about scene 2). Round 12 Crocs then failed on physics the judge missed: "our clip judge didn't catch this because we removed the physics question."

### 12. Chemical Guys: the round skull-and-tools logo and product name on the label are redrawn or lose parts (clips 4292, 4299, 4296, 4303; tapped fail, no words).

**Known limit** · 1 ad / 4 clips (all were retried and replaced before the ad)

- **Why it happened:** The video model redraws small printed logos and letters as the product moves.
- **What was changed:** Recorded as a video-model limit: prompts never spell out text and lock the product's printed words; retries with new pictures replaced these takes.
- **What happened next:** The kept takes passed. Letters still drift (the video-model limits doc: 'Letters drift, even on clips that otherwise pass').

### 13. Liquid I.V. clip 4198: the pack is held in the middle distance, so its letters garble (and the judge saw the wrong stick count). Julian: show labels very close or very far, never in between; letters change even on passing clips; try spelling the label letters to the video model.

**Known limit** · 1 ad / 1 clip named; he said 'we are facing a lot of those failures'

- **Why it happened:** The video model can't keep small printed letters stable while the product moves.
- **What was changed:** First a picture-check gate (label faces the camera only if the product fills a third of the frame, PR #414) — it failed 43 of 63 round 10-11 pictures in a free test, so it was dropped (PR #428). Now only words: a product shown to the lens is held close. Spelling the letters to the video model was tested in round 12 and dropped ("doesn't really work"). (#406, #427, #410)
- **What happened next:** Round 12 notes don't mention garbled labels, but the spelling test failed and the doc still lists drifting letters as a limit.

### 14. Liquid I.V. B-roll (clip 4250, used in the ad): powder is poured from a stick pack that was never torn open. "It started from the starting picture."

**Partly fixed** · 1 ad / 1 clip

- **Why it happened:** The starting picture showed the sealed pack, and nothing told the planner or picture step that a pack must be shown open before pouring.
- **What was changed:** Open-state rule: the starting picture shows the product ready for its action (pack torn, cap off) only when a page photo shows it open; otherwise the planner picks an action that doesn't need it opened. The picture check fails a product not ready for its action. (#406, #414)
- **What happened next:** Round 12 Liquid I.V. didn't repeat the sealed-pack fault, but the pour still failed in a new way: "the way that the powder is being poured is not natural. Nothing is moving except from the powder dropping."

### 15. Revlon scene 4: the barrel's long air slots turn into round holes as the brush comes toward the lens (clips 4189 tapped fail, 4239 with note); the agent should have used an end picture instead of tuning the prompt.

**Fixed (not tested yet)** · 1 ad / 2 clips

- **Why it happened:** The video model redrew the barrel pattern as the product grew toward the camera; the agent retried with reworded prompts.
- **What was changed:** Same fix order (PR #415): new picture or end picture after a judge fail. (#408)
- **What happened next:** Round 12 Revlon notes were about a slow hook and hair passing unnaturally through the tool, not the barrel. Round 12's 'grade these first' list shows Revlon scene 4 as given up or unsure; not graded clip by clip.

### 16. Two ads (SodaStream Terra, Ooni Koda 2) gave up at planning and made no video.

**Fixed (proven in a later round)** · 2 of 10 ads

- **Why it happened:** The plan kept breaking code rules ('plan keeps repeating product actions', 'doesn't meet the required short-ad structure') and the planner had only one retry; the warning didn't name the broken rule.
- **What was changed:** One planner rule for scene count and repeated actions (prompt and refusal use the same words); later the planner gets 3 tries. (#390, #431)
- **What happened next:** Round 12: all 12 ads got past planning (SodaStream and Ooni were not re-run).


## Left open

- Ads feel too slow: the presenter speaks slowly. First said on AeroPress, then in chat: "it happens for almost all the ads. I want it to be a little bit faster".
- No B-roll, or far too little: "why no b roll?" (Bondi), "no b roll scene" (BlenderBottle), and in chat "we should add more b rolls... As many b roll as possible and make each b roll to have a purpose and meaning".
- Talking scenes are boring and all look the same: "a lot of boring talking scenes that does nothing"; "the person just plainly keep talking" (Bondi); "we just hold the same exact product in the hand in the same exact position in the same exact style of the camera angle".
- When a real demo is hard, the ad falls back to more of the same talking; Julian wants at least one scene built around the product and the planner to "think of a creative way to show and advertise the product" (Bondi; chat, floss example).
- Make great B-roll even when the product page has few photos that show the product's effect (Bondi note; chat).
- AeroPress scene 3's passing take dropped the clear cup used in tries 1 and 2: "it would be better if we could do this with the clear cup like clip 1 and 2".
- Chemical Guys: the round skull-and-tools logo and product name on the label are redrawn or lose parts (clips 4292, 4299, 4296, 4303; tapped fail, no words).
- Liquid I.V. clip 4198: the pack is held in the middle distance, so its letters garble (and the judge saw the wrong stick count). Julian: show labels very close or very far, never in between; letters change even on passing clips; try spelling the label letters to the video model.
- Liquid I.V. B-roll (clip 4250, used in the ad): powder is poured from a stick pack that was never torn open. "It started from the starting picture."

## Caveats

- 2 products (SodaStream, Ooni) gave up at planning and made no video. They count as ads that failed "works".
- He tapped every made ad pass ("nothing is breaking"); quality notes make them all fail the good-ad bar.
- Chemical Guys and Liquid I.V. were tapped pass but a clip he failed is inside each ad, so they fail "works".
- Only 4 pure B-roll scenes reached the final ads: 28 of 32 scenes were talking.

---

← [Round 10](round-10.md) · [All rounds](README.md) · [Round 12](round-12.md) →
