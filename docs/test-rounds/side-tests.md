# Side tests

Small tests between rounds, one question each. A "no" is a result too: it told Julian what not to build. "Agreed" means an AI checker matched Julian's grade; items he saw and said nothing about count as agreed.

| Test | Date | What it asked | Result |
|---|---|---|---|
| [Re-check old grades](#re-check-old-grades) | Oct 9 | Are 26 old grades still right? | 25 of 26 stood |
| [Rule checks vs Julian](#rule-checks-vs-julian) | Oct 9–10 | Do AI rule checks agree with him? | 150 of 155 agreed |
| [Two new rule checks](#two-new-rule-checks) | Oct 10–11 | Same for 2 new rules? | 48 of 51 agreed |
| [Phone-flip wording](#phone-flip-wording) | Oct 10 | Does wording stop the phone flipping? | 4 of 8 clips pass |
| [Shared "before" picture](#shared-before-picture) | Oct 10–11 | Does one shared picture keep scenes alike? | 2 of 3 ads, then 8 of 8 |
| [Picture check on wrong pictures](#picture-check-on-wrong-pictures) | Oct 10–11 | Does it catch bad pictures only? | 6 of 10 bad caught, 0 of 52 good failed |
| [Picture redraw](#picture-redraw) | Oct 11 | Is a redrawn picture right? | 5 of 6 right |
| [Picture check value](#picture-check-value) | Oct 15–16 | Are its fails real? | 6 of 6 real |
| [Reference mode, phone case](#reference-mode-phone-case) | Oct 15 | Do photos of both sides stop made-up sides? | 7 of 12 clips pass |
| [Note-fix planner](#note-fix-planner) | Oct 17 | Does it plan the right fix for a user's note? | 65 of 74 right |
| [Fixing-agent prototype](#fixing-agent-prototype) | Oct 18–19 | Can one AI fix a failed scene? | 5 of 10 fixed |
| [Clip judge: two new fails](#clip-judge-two-new-fails) | Oct 19 | Are its 2 new fails real? | 2 of 2 real |
| [Reading product videos](#reading-product-videos) | Oct 19 | Can video frames give useful pictures? | Method accepted |
| [Blush ad with video reading](#blush-ad-with-video-reading) | Oct 20 | Does the blush ad get an applicator shot? | No |
| [Photo and video picker](#photo-and-video-picker) | Oct 19 | Right media on 10 pages? | Not graded by Julian |
| [Clip judge on hand-made clips](#clip-judge-on-hand-made-clips) | Oct 25 | Which clip checker is right more? | 32 of 35 right |
| [Lean pictures prototype](#lean-pictures-prototype) | Oct 31 | Right pictures sent per scene? | 2 of 9 right |
| [Clip judge: hidden parts](#clip-judge-hidden-parts) | Nov 14 | Does a hidden-part rule cut wrong fails? | 6 of 6 wrong fails down to 3 of 6 |
| [Starting-picture hands](#starting-picture-hands) | Nov 18 | Do new hand wordings look natural? | 63 of 65, then all pass |

All dates are 2026. A starting picture is a still image the video model starts from. The clip checker is an AI that watches each clip and passes or fails it.

## Re-check old grades

- Before 26 old grades became the answer key, Julian re-graded them. One round 1 phone-case clip went from fail to pass. The key was corrected.

## Rule checks vs Julian

- One AI text check per product-shot rule. First about 127 of 160 agreed; after 3 rewordings, 150 of 155. It caught 120 of 129 broken plans. Shipped (#120).

## Two new rule checks

- "Same place across scenes" 11 of 12, "camera as close as the action needs" 37 of 39, 5 of 5 broken copies caught. Each rested on one graded fail. Both added.

## Phone-flip wording

- Pocket pull passed. Drops: all 3 clips still flipped to a made-up side. The drop with both-side photos stayed right.
- Changed: every prompt keeps the camera-facing side toward the camera. Drops left to the clip checker. **Partly fixed.**

## Shared "before" picture

- Pictures only. Attempt 1: 2 of 3 ads; the bag ad's scene 4 kept a wide camera. Attempt 2, with "camera as close as the action needs": 8 of 8. Shipped.

## Picture check on wrong pictures

- Caught 6 of 10 pictures Julian called wrong. Missed all 4 wrong toilets: **Known limit** ([known limits](../limitations.md)). Shipped, shown the shop's own photo.

## Picture redraw

- 5 of 6 redraws right. The bag-clasp redraw had an extra thumb the check passed. Julian asked to look at stray hands. **Not fixed.**

## Picture check value

- 29 new products plus round 4: all 6 fails real, each fixed by a redraw. No complaint on about 108 passing pictures. Now always on.

## Reference mode, phone case

- Starting picture plus clean photos of both sides. Pocket pull 4 of 4, floor drop 2 of 4, six-foot drop 1 of 4. Julian: "we should build it." Built.

## Note-fix planner

- Misses included calling a slower scene impossible, redrawing the picture for a new line, and changing more than asked. Fixed the same day; later replaced.

## Fixing-agent prototype

- An AI that remade failed clips: floss used wrong 3 times, earplug laid on the ear twice. Built, then replaced in round 8 by one AI per scene.

## Clip judge: two new fails

- Told to watch the whole frame, it failed 2 pan clips Julian passed. Both real (browning on the rim, knobs changing shape), but small. Shipped.

## Reading product videos

- One AI call picks moments from a few video frames. Julian: it works. It also stopped to ask about price in 9 of 19 videos; now it never asks.

## Blush ad with video reading

- The reader kept 0 of 39 moments, the product shot failed 3 times, and the ad stayed all talking. Reworded; a re-run kept 2 moments. **Not fixed.**

## Photo and video picker

- In Claude's run (the AI coding assistant), 9 of 10 pages worked; one crashed on a safety filter and was fixed. Merged.

## Clip judge on hand-made clips

- Two AI models on 20 clips: 16 of 18 and 16 of 17 right, both wrong on the serum. Kept the current one.

## Lean pictures prototype

- "Send only what the scene needs": 7 of 9 scenes wrong (product badly shown or missing, no presenter). Not merged; led to round 8's design.

## Clip judge: hidden parts

- **Tried:** before failing a changed part, the clip checker asks if covering, angle or movement only makes it look different. Cost $6.92 of Azure.
- **Safety:** 25 clips Julian failed: all 5 fails caught before were still caught.
- **Benefit:** JLab failed 3 of 3, then 2 of 3; LifeStraw 3 of 3, then 1 of 3.
- **Known limit:** on JLab it thinks the hook's back is in clear view.
- Built (#658). **Fixed (not tested yet).**

## Starting-picture hands

- **Tried:** 7 of 11 failed Nov 17 clips broke physics with hands. New wording, pictures only, 17 products, same plans.
- **Attempt 1:** Julian passed 63 of 65. Hatch and Crocs failed: bulky products on a flat palm.
- **Attempt 2** (adds "fingers wrapped around its side"): all pass.
- **Counts:** hands covering in fix steps 33, then 4, then 3. Palm-under orders 28 of 109, then 16 of 104.
- Built (#716). **Fixed (not tested yet).**

---

[All rounds](README.md)
