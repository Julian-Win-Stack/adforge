# Round 4: 10 ads, first with an AI clip checker (October 12 to 14, 2026)

**Result: 5 of 10 ads worked** (7 of 10 by his taps alone). Product shots that worked: 18 of 29 (23 of 29 by his taps).

How we counted: 5 clips he tapped pass count as fails because he named a fault. Talking shots and quality were not graded. See [how we grade](README.md#how-we-grade).

## What we tried
- 10 products: the 4 from [round 3](round-03.md) plus 6 new (a swivel chair, a power bank, a Great Jones pan, Momofuku chili crunch, Gorjana earrings, Merit Flush Balm).
- New: the same side of the product stays facing the camera, one shared "before" picture per ad, and fixing a scene from the user's note.
- Afterwards we tried the clip checker (an AI that watches each clip and passes or fails it) on these clips, with automatic remakes. See [scene tests](scene-tests.md).

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Toilet cleaner: a woman pours liquid in from another bottle (problem scene) | The video model added a hand. The clip checker doesn't look for extra people | None. A check for uninvited people was parked | Not fixed |
| Toilet cleaner: the brush scrubs the seat too | The video model moved the brush off the bowl | Clip checker plus up to 2 automatic remakes ([#157](https://github.com/Julian-Win-Stack/adforge/pull/157)) | Fixed (proven later) |
| Toilet cleaner: rust fades where the brush never went | The video model's physics | Clip checker plus automatic remake | Fixed (proven later) |
| Phone case: a ring and a second phone already on the floor before the drop | The shared "before" picture holds things from every scene | None. Leaving out other scenes' things was proposed | Not fixed |
| Phone case: a second phone in the person's trousers | Same extra objects in the starting picture (a still image the video model starts from) | None | Not fixed |
| Phone case: phone shakes after landing, grows extra lenses | The video model's fault. The prompt was right | Clip checker plus automatic remake | Fixed (proven later) |
| Phone case: back camera on both sides as it leaves the pocket (3rd round in a row) | The checker looked at only 5 still frames | Checker now looks at 10 frames a second ([#163](https://github.com/Julian-Win-Stack/adforge/pull/163)) | Fixed (not tested yet) |
| Le Duo: curls form, but she never turns the tool (2 clips) | The video model can't turn a tool while gliding it | 3 stronger prompts gave a partial turn at best. Listed as a limit | Known limit |
| Le Duo: lower body cut off, she looks pasted in | The starting picture's framing | None this round | Not fixed |
| Le Duo: the hair comes from the mannequin's chin | The starting picture put the hair in the wrong place | 2 automatic remakes kept the same hair | Not fixed |
| Chili crunch: sauce on plain white rice, odd use | The planning step picked a use the page doesn't show | None | Not fixed |
| Merit balm: lip colour spreads away from the fingertip | The video model's physics | Clip checker plus automatic remake | Fixed (proven later) |

## What we learned
- A clip checker plus automatic remakes fixed most video model slips (seat scrub, shaking, colour spread).
- The 10 frames a second check caught the phone flip 3 of 3 times, and 3 of 3 flips on 110 old clips with 0 wrong fails.
- It can't fix faults already in the starting picture. Those need a fix before filming.
