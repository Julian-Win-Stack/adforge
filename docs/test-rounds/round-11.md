# Round 11: 10 new products, first quality grades (November 5-6, 2026)

**Result: 6 of 10 ads worked** (6 of the 8 that were made). Good ads: 0 of 8. In the final ads, 2 of 4 product shots and 28 of 28 talking shots worked.

How we counted: Julian tapped all 8 made ads pass, but 2 hold a clip he failed, so they count as fails. SodaStream and Ooni made no video and count as fails. See [how we grade](README.md#how-we-grade).

## What we tried
- 10 new products, from just the product link: AeroPress, Bondi Sands self-tan foam, BlenderBottle, SodaStream Terra, Chemical Guys car-wash foam, Hypervolt 2, Ooni Koda 2, Liquid I.V., Revlon One-Step, Bissell Little Green.
- End pictures (a still image the video model must end on) plus the round 9 and 10 fixes.
- First round where Julian graded ad quality, not just "does it work".

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| All 8 ads: the presenter speaks too slowly (1.9-2.1 words a second) | Clips were longer than their lines; voice asked for an unhurried pace | Lines planned at 2.8 words a second; silence cut; slow-voice plans sent back | Partly fixed |
| AeroPress, Bissell: the presenter looks pasted onto the background | Presenter and room were two pictures merged; every talking shot copied it | Presenter and room drawn together in one picture | Fixed (not tested yet) |
| All 8 ads: few or no product shots (28 of 32 scenes talking; 4 ads had none) | The planning step needed a talking opening and preferred talking | Middle scenes are product shots by default; at least one per ad | Partly fixed |
| All 8 ads: talking shots boring and alike (25 of 28 just hold the product to the lens) | Every talking picture was edited from one picture of her standing still | Each talking shot gets its own style and product state; no two in a row match | Partly fixed |
| Bondi: when a real demo is hard, the ad falls back to more talking | Nothing asked for a scene built around the product | At least one product scene; pick the moment before, a close-up, or the feeling after | Partly fixed |
| Bondi: wants great product shots even when the page has few useful photos | With only plain product photos, planning had nothing to show | No targeted fix | Not fixed |
| AeroPress, BlenderBottle, Chemical Guys, Revlon: the scene AI keeps rewording the prompt and films the same fault again (about 8 failed attempts) | Remakes on the same picture failed 5 of 6; a new picture fixed 4 of 6 | A failed starting picture can't be filmed again; end pictures allowed from the first attempt (#415) | Fixed (not tested yet) |
| AeroPress: the filter cap grows into a long cylinder on attempts 1 and 2 | The video model reshaped the cap; the AI only reworded | Covered by the new fix order above | Fixed (not tested yet) |
| AeroPress: the passing attempt dropped the clear cup from attempts 1 and 2 | Nothing keeps props the same between attempts | None yet | Not fixed |
| BlenderBottle: a made-up printed ruler on the bottle, attempts 1-3 | The fault was in the starting picture; the AI changed only the prompt | Failed pictures can't be reused; picture check fails parts no photo shows | Fixed (not tested yet) |
| Chemical Guys: in the ad, the bottle moves with the camera; the clip checker passed it | Picture and prompt described different set-ups; the checker had no motion questions | Same fix order; motion questions not brought back | Fixed (not tested yet) |
| Chemical Guys: logo and name on the label redrawn (4 attempts, all replaced) | The video model redraws small printed logos as the product moves | Recorded as a limit; prompts never spell out text | Known limit |
| Liquid I.V.: pack held at middle distance, so its letters garble | The video model can't keep small letters steady while moving | A product shown to the camera is held close; spelling the letters was tried and dropped | Known limit |
| Liquid I.V.: in the ad, powder pours from a pack never torn open | The starting picture showed the sealed pack | The starting picture must show the product ready for its action | Partly fixed |
| Revlon: the barrel's long air slots turn into round holes | The video model redrew the pattern as the brush came close; the AI only reworded | Same fix order (#415) | Fixed (not tested yet) |
| SodaStream Terra, Ooni Koda 2: gave up at planning, no video | The plan kept breaking rules, with one retry and no rule named | One clear rule; the planning step gets 3 tries | Fixed (proven later) |

## What we learned
- Ads that don't break can still be bad: 0 of 8 passed on quality.
- Too much talking, too slow, always the same pose. Product shots need to carry the ad.
- Rewording a prompt rarely fixes a clip; a new starting picture does. Julian: "The prompts it's not going to fix, and the agent will always lean into tuning the prompt."

---

← [Round 10](round-10.md) · [All rounds](README.md) · [Round 12](round-12.md) →
