# Round 8: One AI per product shot scene (November 3, 2026)

**Result: 9 of 15 ads worked.** 12 of 15 if only Julian's pass/fail taps count. In the final ads, 26 of 27 product shots and 47 of 48 talking shots worked.

Any complaint counts as a fail. See [how we grade](README.md#how-we-grade).

## What we tried
- The same 15 products as [round 7](round-07.md).
- Product shots (the product in use, nobody talking to camera) got one AI per scene that draws, films and checks it.
- That AI picks photos, draws a starting picture (a still image the video model starts from), films it, and checks the clip. Up to 6 attempts.
- Talking shots (an AI presenter talking to camera) still came from an outside presenter-video service.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Krave: the video model made up how the cleanser foam looks on the face | No page photo shows the foam, so the video model guessed | End pictures (a still image the video model must end on); clip checker checks unasked-for product looks | Came back |
| Krave, Beautyblender: the scene AI never found why 3 scenes kept failing (11 attempts) | The right rule was buried in its instructions; it knew nothing of the video model's weak spots | After 2 fails in a row it must make a bigger change; later, fix with a new picture, not new words | Came back |
| Beautyblender: foundation turned into flat patches on the face; 2 scenes given up | The video model is weak at makeup blending into skin, and the scene AI didn't know | End pictures that show the blended result | Partly fixed |
| Krave: a real woman's face from the shop's photo was sent to the video model | The face swap step had been switched off by accident | Face swap back on for any stranger in a chosen photo | Partly fixed |
| The clip checker (an AI that watches each clip and passes or fails it) failed 9 of 17 graded clips Julian would ship | Its motion and sudden-change checks failed almost every clip | Only the product check can fail a clip now | Fixed (proven later) |
| ezpz mat: the "does what was ordered" check isn't needed | It compared the clip to the scene's description and failed too often | Dropped with the other motion checks | Fixed (proven later) |
| Pair of Thieves: maybe the checker is strict because it looks at too many frames | It looks at up to 50 frames per question | No change | Not fixed |
| Beautyblender: new starting pictures looked just like the old ones | Nothing compared a new picture with the old; the new one always won | The scene AI sees new next to old and says if the change shows | Fixed (proven later) |
| Beautyblender: the AI asked the user for a page video it already had | Not looked into | None | Not fixed |
| Garject: the whole ad fails | The garlic press's scraper and handles keep coming out wrong | Product set aside | Dropped |
| Zulay frother: the product changed in pictures given to the presenter service | That service worked from pictures that changed the product | Service removed; every scene now made by the video model from a checked starting picture | Fixed (proven later) |
| Wet Brush: ad failed, no reason given | Unknown | None | Not fixed |
| Legion: letters on the tub changed during the clip | The video model redraws printed words | Prompts can't quote on-screen text; a product shown to camera is held close | Came back |
| MudBuster: the checker rightly failed a clip where the cup is twisted, an unreal use | The video model made up how the cup is used | None; another attempt went in the ad | Came back |
| ezpz mat: a clip failed, no reason given | The checker saw product and motion faults | None; a later attempt passed | Not fixed |
| Logitech mouse: the main AI insisted on a price the user asked to leave out | The planning step always needed a price line | If the user says no price, the plan has none | Fixed (proven later) |
| Peace Out, Krave, Beautyblender: "nothing's breaking at least", so they work but aren't good | There were no rules for ad quality yet | Quality work started after round 11 | Not fixed |

## What we learned
- Letting only the product check fail a clip removed most of the wrong fails.
- The scene AI doesn't know what the video model can't do, so it keeps retrying with words that can't help.
- An ad that doesn't break is not yet a good ad.

---

← [Round 7](round-07.md) · [All rounds](README.md) · [Round 9](round-09.md) →
