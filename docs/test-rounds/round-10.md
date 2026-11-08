# Round 10: End-picture test on 5 failed round 9 scenes (November 4–5, 2026)

## What this round tested

- 5 scenes that failed in round 9, re-run so the scene's AI helper could draw an end picture (the clip's last frame) after a failure.
- A scene test: no whole ads were put together.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads | none made | — |
| Takes that work (all are speaking demo scenes) | 8 of 11 (73%) | — |
| Does the end picture work? (his verdict on the feature) | yes: "it works" | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words). Clip numbers (for example clip 3839) are the app's id for one filmed take.

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The video model invented the product's content: a small amber gel became white cream on her face (Krave scene 3, clip 4137), and the app's judge didn't fail it. | 1 of 5 scenes / 1 of 11 clips | Works | Fixed (not tested yet) |
| 2 | On the printed-mouse scene (Logitech mouse scene 4) the agent couldn't fix the changed cat artwork on tries 1 and 2 (clips 4076, 4087); Julian asked why it couldn't fix it and what it changed to finally pass. | 1 of 5 scenes / 2 of 11 clips | Works | Came back |

Details, in the same order:

### 1. The video model invented the product's content: a small amber gel became white cream on her face (Krave scene 3, clip 4137), and the app's judge didn't fail it.

**Fixed (not tested yet)** · 1 of 5 scenes / 1 of 11 clips

- **Why it happened:** The kept starting picture was an edit of an edit. Each edit only remembered the photos picked for that one edit, so the photo of the tube dropped out. The judge then had no tube photo, decided it 'could not see the product', and skipped its product check entirely, answering 'unsure'. An 'unsure' counts as a pass, so the scene ended with no second try. The video prompt also never said what the gel looks like (only 'small gel smear'), and the chat agent had asked for 'a small amount'.
- **What was changed:** Edited starting pictures now keep every product photo from the first drawing and all later edits, and those photos go to the picture check and the clip judge. Two other ideas were not built: describing the content's look in the prompt (Julian worried the agent might write wrong words the video model follows literally; parked) and retrying on 'unsure' (Julian: not a problem, because the photo fix covers this case). (#377, #378)
- **What happened next:** No later graded round re-ran this gel scene or reported the judge skipping a product check for a missing photo. Round 11 and 12 grades don't repeat the complaint.

### 2. On the printed-mouse scene (Logitech mouse scene 4) the agent couldn't fix the changed cat artwork on tries 1 and 2 (clips 4076, 4087); Julian asked why it couldn't fix it and what it changed to finally pass.

**Came back** · 1 of 5 scenes / 2 of 11 clips

- **Why it happened:** Try 2 only edited the artwork in the same picture: the mouse stayed small (about 1/8 of the frame) and still moved toward the lens, so the video model redrew the art as it grew. Only after the app said 'same fault, make a bigger change' did the agent draw a new, tighter shot with the mouse about 1/4 of the frame and held still; that passed.
- **What was changed:** Nothing new in round 10 (the existing 'make a bigger change' nudge, #367, is what worked on try 3). After round 11 showed the same habit, the agent was changed to fix failed clips with a new starting picture or an end picture instead of new prompt words (PR #415). (#408)
- **What happened next:** Round 11 showed the same pattern again: the agent re-filmed the same picture with reworded prompts (BlenderBottle scene 2, Chemical Guys clip 4301, Revlon clip 4239). See round 11 for the later fix.


## Left open

- On the printed-mouse scene (Logitech mouse scene 4) the agent couldn't fix the changed cat artwork on tries 1 and 2 (clips 4076, 4087); Julian asked why it couldn't fix it and what it changed to finally pass.

## Caveats

- He graded the feature as a whole and asked not to wait for per-clip grades, so the 6 Beautyblender takes count as pass.
- Two Logitech takes count as fails only from his questions ("why wasn't the agent able to fix?"): borderline.
- A first batch run from wrong copies is not counted.

---

← [Round 9](round-09.md) · [All rounds](README.md) · [Round 11](round-11.md) →
