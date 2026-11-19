# Round 12: First round with the ad-quality rules (November 6–8, 2026)

**Result: 3 of 12 ads worked** (9 of 12 by Julian's taps alone). Product shots that worked: 10 of 20. Talking shots: 18 of 25. Good ads: 0 of 12.

## What we tried
- New quality rules: product shots (the product in use, nobody talking to camera) went from 13% to 45% of ad time, speech at 2.8 words a second, label checks, and an end card on every ad.
- 12 products: 8 from round 11 plus 4 new (KONG dog toy, Crocs, Gorilla Glue, CATAN). Graded as whole ads, plus a quality page.

How we counted: any complaint counts as a fail, see [the summary](README.md#how-we-grade). Julian says his pass taps were generous this round.

## What broke, and what we did
Status words: [the summary](README.md#status-words).

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| All 12 ads: hooks too long, slow, low energy | Hooks were 14-18 words | 12-word hook, excited delivery | Fixed (not tested yet) |
| All 12 ads: presenter (an AI person talking to camera) doesn't feel real | Page copy as lines, told to hold still | Chatty lines, one feeling per scene, natural movement | Fixed (not tested yet) |
| All 12 ads: no emotional "after" moment | Nothing asks for a reaction shot | None | Not fixed |
| Bondi: 3 talking shots look too alike | Only the camera mode changes between them | None | Not fixed |
| Bondi, Liquid I.V.: product shots show no real use | A rewrite made one just "raises the bottle" | Every product shot must sell by doing something | Partly fixed |
| AeroPress: 1-2 s silent stall at the end of a clip | Fast speech left spare time | Keep at most 0.5 s after the last word | Fixed (proven later) |
| BlenderBottle, Liquid I.V.: product pinched by one finger | Rules favoured a readable label over a real grip | Natural-grip rule, not merged | Not fixed |
| KONG: toy drawn too small in one scene | One-off miss by the picture model | None | Not fixed |
| KONG: stuffed by pinching one kibble, not the page's method | Page's how-to photos never opened | None | Not fixed |
| KONG: toy at different sizes across scenes | Each scene set size on its own | One version and real size for all scenes | Fixed (not tested yet) |
| Crocs: clogs held beside a dress, not worn | A rule allowed "hands and product only" | Any body part but the face; copy the page's outfit | Fixed (proven later) |
| Crocs: heel strap passes through the leg (2 clips) | Nothing checks for impossible motion | None | Not fixed |
| Liquid I.V.: only the powder moves in a pour | Prompt asked for a still hand | Describe the real hand movement | Fixed (proven later) |
| Revlon: hair passes through the brush | The video model can't do hair tools | None | Known limit |
| Bissell: the video model made up a sprayer | A 7 s still shot, so it filled time | None | Not fixed |
| Gorilla Glue: hook shows a different bottle | Each scene picked its own photos | One product version for the whole ad | Fixed (proven later) |
| Gorilla Glue: boards and clamps change between scenes | Scenes can't see each other | Shared objects described once, mismatches refused | Fixed (proven later) |
| CATAN: sold as if the story island were real | Page never says "board game" | Name the kind of product by scene 2 | Fixed (proven later) |
| Pictures fail too often: 52 of 132 failed their check | Several causes | See the 5 rows below | Fixed (not tested yet) |
| Chemical Guys: a failed end picture got filmed | Starting and end pictures (stills the video model starts from / must end on) shared 6 attempts | 6 each | Fixed (not tested yet) |
| Picture check misread very tiny print as misspelled | No exception for fine print | Tiny letters don't fail | Fixed (not tested yet) |
| Edits dropped or scrambled the logo (20 of 52 failed pictures) | Every edit request ended "No text." | Removed it | Fixed (not tested yet) |
| Products small in their photo come out wrong (10 of 20 logo fails) | Product is a corner of the photo | Close-up crops tried: fails 28 to 25, chance | Not fixed |
| End pictures drawn without a product photo (6 of 20 logo fails) | Only the starting picture was sent | Send the product photos too | Fixed (not tested yet) |

## What we learned
- More product shots means many more chances for a clip to break.
- Each scene's AI works alone, so product and props drift unless the plan pins them.
- Most picture fails came from our own instructions.

---

[Round 11](round-11.md) · [All rounds](README.md) · [Round 13](round-13.md)
