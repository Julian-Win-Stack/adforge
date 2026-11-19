# Round 10: End-picture test on 5 failed scenes (November 4-5, 2026)

**Result: 8 of 11 scene attempts worked.** No whole ads were made. Julian's verdict on end pictures: "it works".

How we counted: he graded the feature as a whole, so the 6 Beautyblender attempts count as pass. The 2 Logitech fails count only from his questions, so they are borderline. See [how we grade](README.md#how-we-grade).

## What we tried
- 5 scenes that failed in [round 9](round-09.md), filmed again.
- After a fail, the scene AI could draw an end picture (a still image the video model must end on) as well as the starting picture (a still image the video model starts from).
- A scene test only, all scenes with the presenter talking while using the product.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Krave: a small amber gel became white cream on her face, and the clip checker didn't fail it | An edited starting picture lost the tube photo, so the checker skipped the product check | Edited pictures keep every product photo for the checks (#378) | Fixed (not tested yet) |
| Logitech mouse: the scene AI couldn't fix the changed cat artwork on attempts 1 and 2 | Attempt 2 only edited the art; the mouse stayed small and kept moving toward the lens | The existing "make a bigger change" push worked on attempt 3; later, fix with a new picture, not new words (#415) | Came back |

## What we learned
- End pictures helped: Beautyblender scene 3 passed on attempt 3. Scene 2 still gave up.
- A bigger change, like a new, tighter shot with the product held still, works where rewording doesn't.

---

← [Round 9](round-09.md) · [All rounds](README.md) · [Round 11](round-11.md) →
