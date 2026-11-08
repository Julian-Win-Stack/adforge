# Side tests

Small tests run between rounds to answer one question each. A "yes" and a "no" are both **results**, not failures: a "no" told Julian what not to build.

Many of these check an AI checker against Julian. There, "agreed" means the checker's verdict matched his grade. Clips or pictures he was shown and said nothing about count as agreed.

| Test | Date | Question (short) | Result | Answer |
|---|---|---|---|---|
| [Re-check old grades](#re-check-old-grades) | Oct 9 | Are 26 old grades still right? | 25 of 26 stood | Yes |
| [Rule checks vs Julian](#rule-checks-vs-julian) | Oct 9–10 | Do the AI rule checks agree with him? | 150 of 155 agreed | Yes |
| [Two new rule checks](#two-new-rule-checks) | Oct 10–11 | Do the "same place" and "close enough" checks agree? | 48 of 51 agreed | Yes |
| [Phone-flip wording](#phone-flip-wording) | Oct 10 | Does new wording stop the phone flipping? | 4 of 8 clips pass | Partly |
| [Shared "before" picture, try 1](#shared-before-picture) | Oct 10 | Does one shared picture keep scenes consistent? | 2 of 3 ads pass | Partly |
| [Shared "before" picture, try 2](#shared-before-picture) | Oct 10–11 | Same, after a camera-distance rule | 8 of 8 ads pass | Yes |
| [Picture check on wrong pictures](#picture-check-on-wrong-pictures) | Oct 10–11 | Does it catch bad start pictures without failing good ones? | 6 of 10 bad caught, 0 of 52 good failed | Mostly |
| [Picture redraw](#picture-redraw) | Oct 11 | Is a redrawn start picture right? | 5 of 6 right | Mostly |
| [Picture check value](#picture-check-value) | Oct 15–16 | Are its fails real on new products? | 6 of 6 real | Yes |
| [Reference mode, phone case](#reference-mode-phone-case) | Oct 15 | Do pictures of both sides stop made-up sides? | 7 of 12 clips pass | Partly (then built) |
| [Note-fix planner](#note-fix-planner) | Oct 17 | Does it plan the right fix for an owner's note? | 65 of 74 plans right | Mostly |
| [Fixing-agent prototype](#fixing-agent-prototype) | Oct 18–19 | Can one AI that owns a failed scene fix it? | 5 of 10 fixed | Partly (then built) |
| [Clip judge: two new fails](#clip-judge-two-new-fails) | Oct 19 | Are its new fails on 2 clips real? | 2 of 2 real | Yes |
| [Reading product videos](#reading-product-videos) | Oct 19 | Can a few frames of a page video pick useful moments? | Method accepted | Yes |
| [Blush ad with video reading](#blush-ad-with-video-reading) | Oct 20 | Does the blush ad now get an applicator scene? | 2 of 4 pass-bar items | No (ad stayed all talking) |
| [Photo and video picker](#photo-and-video-picker) | Oct 19 | Does it pick the right media on 10 pages? | Not graded by Julian | — |
| [Clip judge on hand-made clips](#clip-judge-on-hand-made-clips) | Oct 25 | Which judge is right more often? | 32 of 35 verdicts right | Yes |
| [Lean pictures prototype](#lean-pictures-prototype) | Oct 31 | Are the right pictures sent for each scene? | 2 of 9 right | No |

All dates are 2026.

## Re-check old grades

- **Question:** Before Julian's old grades on 26 early clips and pictures became the answer key for the AI rule checks, were they still right?
- **Result:** 25 of 26 stood. One phone-case clip from round 1 changed from fail to pass.
- **What it changed:** The answer key for the rule checks was corrected (#120).

## Rule checks vs Julian

- **Question:** Do the AI rule checks (one text check per B-roll rule, reading the plan, start picture and video prompt) agree with Julian?
- **Result:** First run about 127 of 160 agreed. After 3 rounds of rewording, 150 of 155. A test with deliberately broken plans: 120 of 129 caught.
- **What it changed:** The rule checks shipped (#120). They were later used to check the phone-flip sentence did not break anything (151 of 155).

## Two new rule checks

- **Question:** Do new checks for "same place across scenes" and "camera as close as the action needs" agree with Julian?
- **Result:** Same place 11 of 12, close camera 37 of 39; 5 of 5 broken copies caught. Each rule rested on only one graded fail.
- **What it changed:** Both checks added (#127).

## Phone-flip wording

- **Question:** Does new motion wording, or pictures of both sides, stop the phone case flipping to a made-up side mid-clip?
- **Result:** Pulling the phone from a pocket: passed. Dropping it: all 3 wording clips still showed a made-up side ("our wording didn't fix"). The drop with both-side pictures kept both sides right.
- **What it changed:** One fixed sentence in every B-roll prompt: the side facing the camera stays toward it (#123). Drops were left to a clip-judge flip check (#163).

## Shared "before" picture

- **Question:** Does one shared "before" picture per ad keep the same place, toilet and stains across all of an ad's scenes? (Start pictures only, no video.)
- **Try 1 result:** 2 of 3 ads passed. The bag ad failed: scene 4 kept the wide camera, so the bag was too far away.
- **Try 2 result**, after a rule "camera as close as the action needs": 8 of 8 ads passed, including the bag close-up.
- **What it changed:** The shared before-picture shipped (#125).

## Picture check on wrong pictures

- **Question:** Does the start-picture check fail the 10 start pictures Julian called wrong, without failing good ones?
- **Result:** Caught 6 of 10 (a phone, a bent bottle neck, an upside-down label, 2 bag clasps, a tool logo). Missed all 4 wrong toilets. Failed 0 of 52 good pictures.
- **What it changed:** The new picture check shipped, shown the shop's own photo. Wrong toilets were written down as a known limit ([known limits](../limitations.md)).

## Picture redraw

- **Question:** When the picture check fails a start picture and the app redraws it once, is the redraw right?
- **Result:** 5 of 6 right. The bag-clasp redraw had an extra thumb that the check passed.
- **What it changed:** No change. Julian asked to look at the picture prompt for stray hands.

## Picture check value

- **Question:** On 29 new products plus round 4, does the start-picture check catch real problems and leave good pictures alone?
- **Result:** All 6 fails were real, and each was fixed by a redraw. No complaint on about 108 passing pictures.
- **What it changed:** The picture check was switched on for good, with no off switch.

## Reference mode, phone case

- **Question:** If the video model gets the start picture plus clean pictures of both sides as references, does it still make up the hidden side?
- **Result:** All 12 started like the picture. Pocket pull and turn: 4 of 4. Floor drop: 2 of 4. Six-foot drop: 1 of 4. The clip judge agreed on 8 of 12.
- **What it changed:** Julian: "I think it is a success ... we should build it." Every B-roll clip was then sent this way, with its drawn start picture first.

## Note-fix planner

- **Question:** Given a shop owner's note on a finished ad, does the note-fix planner choose the right fix per scene (new picture, new clip, new line, go back, or "can't")?
- **Result:** 65 of 74 right. The 9 misses: a slower scene called impossible, "brighter" only redrew the picture, a new line also redrew the picture, it changed more than asked, a plain "no" was wanted for presenter look and music, an owner's correction got no reason, and two borderline cases.
- **What it changed:** The same day: a new line keeps the start picture, changes say only what was asked, and it explains when it makes no change. The planner was later replaced by the fixing agent (#262).

## Fixing-agent prototype

- **Question:** Can one AI that owns a failed B-roll scene (it may change the picture, the action and the prompt) fix it?
- **Result:** 5 of 10 fixed (soap tablets twice, cola, pet powder, shoes). Not fixed: floss used wrong 3 times (one tapped pass but "still unnatural"), an earplug laid on the ear instead of in it, twice.
- **What it changed:** The fixing agent was built into the app. It was later replaced by one AI helper per scene (round 8).

## Clip judge: two new fails

- **Question:** With the clip judge told to watch everything in the frame, are its 2 new fails on pan clips Julian once passed real?
- **Result:** Yes, both real (browning on the pan rim; oven knobs changing shape). Julian: small faults viewers may not notice.
- **What it changed:** The wider "everything in the frame" check shipped (#186).

## Reading product videos

- **Question:** Can code thin a shop's gallery video to a few frames, and one AI call pick the moments worth keeping as pictures?
- **Result:** Julian: "the current method that we are using right now works." Claude (the AI coding assistant that builds the app with Julian) then found the reader stopped at a price question in 9 of 19 videos; it was changed to never ask.
- **What it changed:** Video reading shipped for the planner (#193, #194).

## Blush ad with video reading

- **Question:** Round 5's blush ad had no B-roll because no photo showed the applicator. With video reading built, does it now get an applicator scene?
- **Result:** Not graded by Julian; checked against the plan's pass bar. No crash, but the reader kept 0 of 39 moments, the one B-roll failed the judge 3 times, and the ad ended all talking. After a wording change, a re-run on the saved video kept 2 useful moments. No ad was remade.
- **What it changed:** The reader's wording (#194).

## Photo and video picker

- **Question:** Does the new photo and video picker choose the right media on 10 real pages?
- **Result:** Not graded by Julian. In Claude's run 9 of 10 pages worked first time; one crashed on a safety filter and was fixed.
- **What it changed:** Julian kept all 7 picker wording lines and merged it (#189).

## Clip judge on hand-made clips

- **Question:** Is the new clip judge right on 20 hand-round clips, and which AI model judges better?
- **Result:** One model right on 16 of 18, the other on 16 of 17; both wrong on the serum takes. Julian: "the current version is okay."
- **What it changed:** Kept the current judge for the next rounds (no code change).

## Lean pictures prototype

- **Question:** With "send the video model only what the scene needs", are the right pictures chosen for each B-roll scene? (Pictures only, no video.)
- **Result:** 7 of 9 scenes wrong: product shown badly, no product at all in 2, presenter missing, no real face, shaker contents never shown.
- **What it changed:** Not merged. It led to dropping a long list of one-off video-model rules, and toward the one-helper-per-scene design of round 8.

---

[All rounds](README.md)
