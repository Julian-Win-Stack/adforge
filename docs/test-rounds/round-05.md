# Round 5: 15 new products with the clip checker and auto-remakes (October 17 to 19, 2026)

**Result: 7 of 15 ads worked.** 13 of 15 by his taps alone. Product shots in the final ads: 23 of 33 worked.

## What we tried
- 15 products the app had never seen, nothing faked (serum, blush, soda, tinned fish, mug, pan, shoes, sunglasses, polo, balance board, pet powder, floss, soap tablets, earplugs, soap).
- First big run with the clip checker (an AI that watches each clip and passes or fails it) switched on.
- Failed clips were remade on their own. Users could fix a scene with a note.
- A 16th product (an electric toothbrush) was blocked by a safety filter and made no ad.

How we counted: any complaint counts as a fail. See [the summary](README.md#how-we-grade). Status words: [here](README.md#status-words).

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Glow Recipe: no product shots, every scene a talking shot | Test user declined a photo; the beauty rules then allowed no product shots | Planning rules, and later kept the last product shot instead of a talking shot ([#208](https://github.com/Julian-Win-Stack/adforge/issues/208)) | Came back |
| Olipop: invented use (fridge shelf slides out, can slides over it) | The planning step filmed storing the drink, not using it | Rule: storing or care is not a scene; show real use | Fixed (proven later) |
| Olipop: shirt changes colour mid-clip | The video model drifted from the starting picture (a still image the video model starts from) | Nothing for the drift; clip checker now looks at clothes too | Fixed (not tested yet) |
| Clip checker missed the Olipop shirt and the Loop backwards ear | It looked only at product and hands; it never grades the starting picture | Clip checker now looks at clothes, hair and body | Partly fixed |
| Fishwife: two clips "a little bit unnatural" | Small motion flaws | None needed: checker caught both, remakes passed | Dropped |
| Rothy's: shoes in a washing machine, all three attempts bad | Asked the video model to invent the inside of a running washer | Rule: never film inside a running machine | Fixed (proven later) |
| True Classic: one clip "borderline" | Not looked into; no fault named | Nothing | Not fixed |
| Native Pet: powder poured into air, scoop refills itself | A 1.5-second sprinkle stretched over a 7.3-second clip | Rule: the action must fill the clip; an AI that remade failed clips may change the action | Fixed (proven later) |
| Remakes repeated the same action and kept the newest fail (4 scenes) | Remakes only reworded the prompt; ties kept the newest | That remake AI may change action, framing, picture and length | Partly fixed |
| Clip checker too strict: failed 3 clips Julian passed | Two unrelated complaints counted as agreement | Fail only when two answers name the same fault, later each check asked once | Came back |
| Cocolab: floss changes shape, used wrong (3 clips) | Asked for a flossing move nobody does; the video model can't keep a string whole | Real-use rule plus the remake AI | Came back |
| Cocolab: floss string appears from the hand and splits in two | Same, plus floss held in one hand in the picture | Same; starting pictures later removed from product shots | Came back |
| Cocolab: wrong starting picture | The picture check never sees what the prompt asked for | Fixing a scene from the user's note; later picture removed | Came back |
| Cocolab: invented refill | The planning step sees only page text and photos, so it guesses | Shop product videos now feed the planning step | Came back |
| The planning step invents how products are used (3 of 15 ads) | Page words taken as licence; it guesses the use | Planning rules plus product videos | Partly fixed |
| Blueland: a pipe in the bottle in the starting picture | Our own picture orders contradicted each other | User's note fix; later picture removed | Fixed (not tested yet) |
| Blueland: liquid drips for no reason, dropped tablet stays in hand | A half-second drop stretched over a 5.2-second clip | Rule: the action fills the clip | Fixed (not tested yet) |
| Loop: ear is backwards | Impossible ask: ear and plug visible, framed from behind | Fix how scenes are written, not the checker | Fixed (proven later) |
| Price scene cut to 0 to 2 seconds at the end (11 of 15 ads, 2 at 0 s) | Product shots run past their lines, so pictures fall behind the voice | Nothing; Julian chose not to fix it | Dropped |

## What we learned
- Across all 37 product shots, one-moment actions failed and lasting actions passed.
- The biggest new problem: the app makes up how a product is used.
- The clip checker both missed real faults and failed good clips.

---

← [Round 4](round-04.md) · [All rounds](README.md) · [Round 6](round-06.md) →
