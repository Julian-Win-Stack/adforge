# Round 7: Page photos sent straight to the video model (October 29 to 31, 2026)

**Result: 4 of 15 ads worked.** Product shots in the final ads: 14 of 27 worked. All attempts shown: 22 of 50.

## What we tried
- 15 products, mostly from [round 6](round-06.md). Run on the Azure machine, nothing faked.
- Page photos went to the video model as labelled references. No drawn starting picture (a still image the video model starts from).
- An AI remade failed clips (the remake AI). The clip checker (an AI that passes or fails each clip) judged every clip.
- Wet Brush ran twice, the second time after a photo picker fix.

How we counted: any complaint counts as a fail. Julian tapped only 3 whole ads (all fail); 8 more fail because a clip in them got a complaint, so 12 of 15 work by his taps alone. Gymreapers had no product shots and was not graded. See [the summary](README.md#how-we-grade) and [status words](README.md#status-words).

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Peace Out: strip lifted off with the tool, not peeled by hand | The planning step guessed the use from a two-step photo | Rules: one action per clip, shown as the page shows it | Came back |
| Dexas MudBuster: twisted in all 5 clips; really it moves in and out | The AI that watches page videos said "twist"; the planning step copied it | No direct fix | Came back |
| Backgrounds, clothes and people from page photos leaked into 3 clips | Photos sent whole, people and backgrounds included | Clean every picture; one AI per scene picks photos and draws a checked starting picture | Partly fixed |
| Too many pictures sent (3.74 per clip on average, 3 clips with 9) | Fixed sending rules that nothing trimmed | The scene AI picks only what the scene needs | Fixed (proven later) |
| Main page photo always sent first; its print leaked onto the Beautyblender sponge | A rule forced it first, and remakes couldn't swap it | Rule removed | Fixed (not tested yet) |
| Remake prompts too long (296 words vs 121 for first clips) | No length limit on the remake AI | Fixed short form; fix with a new picture, not new words | Came back |
| Remake AI reworded the prompt instead of fixing the cause it could see | It had no way to clean a picture | Gave it moves to clean or swap pictures | Came back |
| Chipolo: tag came out too big (3 clips) | Nothing tells the video model the real size | Julian: fix with user notes if it happens | Dropped |
| Another person's photo sent next to the presenter (10 of 13 presenter clips) | Model photos went whole | Swap strangers' faces for the presenter | Came back |
| Zulay: video model invented a cable | It invents parts it never saw | Starting picture pins the look; prompt names parts that must stay | Fixed (not tested yet) |
| Wrong-colour product pictures sent (8 clips) | All 24 recolour attempts fell back to the original photo | Cut-outs and recolours redesigned | Fixed (not tested yet) |
| Legion Pulse: powder picture never sent | Contents pictures sent only if named | Send contents pictures on scenes that show them | Partly fixed |
| ezpz mat: a bad-looking drawn picture was sent | Drawn pictures went out unchecked | Starting picture checked before filming | Fixed (not tested yet) |
| Force of Nature: camera looks into the flask top, which no photo shows | Prompt asked for an unseen angle | Rule: never show a side no photo shows | Fixed (not tested yet) |
| Page video always sent as "movement only", sometimes where it made no sense (16 clips) | A fixed prompt line | Send it only when its movement matches, and let it guide the look | Fixed (not tested yet) |
| Clip checker failed 5 clips Julian passed (Beautyblender, Chipolo) | Its bar was too strict; a second AI model agreed on all 9 | Only the product check can fail a clip | Came back |
| Clip checker failed Beautyblender for "did not blend the foundation" (2 clips) | Motion check failed results the video model can't film | Same; later an end picture (a still the clip must end on) | Came back |
| Clip checker said "can't tell" and skipped its checks (9 clips, 8 skipped both) | One "not shown" answer skipped every check | Split "product not shown" from "detail not shown" | Came back |
| Julian couldn't understand the checker's reasons | Raw answers; "unsure" shown as "not run" | Plain-words summaries on the grading pages | Came back |
| Wet Brush: the video model can't brush hair; "It breaks the physics" | Video model limit since round 6 | Julian gave up on it; show the end result instead | Known limit |
| Garject: page photos mislead the video model; every product shot failed (6 clips) | Open press sent first; page video couldn't be downloaded | More video players download now; product set aside | Known limit |
| Our AI can't pick the right inputs by looking, as Julian can | Fixed rules chose inputs; the prompt writer saw no pictures | One AI per scene sees, picks and checks pictures | Partly fixed |
| Found in the run: no Gemini key on the server | Key never set on the server | Key added | Fixed (proven later) |
| Found in the run: voice account full (100 voices) | Not written up | 45 deleted by hand; code frees one when full | Fixed (not tested yet) |
| Found in the run: website build failed on the server; deploy busy check always refused | Not written up | Fixed in code | Fixed (not tested yet) |
| Found in the run: ads stalled until someone typed "Please carry on" | The chat AI stopped and nothing woke it | App wakes it on its own | Fixed (not tested yet) |
| Found in the run: cut-outs failed all 3 attempts (21), recolours failed (~27), quietly | Not written up | Redesigned | Fixed (not tested yet) |
| Found in the run: no room picture, so the place was invented (14) | Room drawn too late | Room drawn first | Fixed (not tested yet) |
| Found in the run: safety filter refused pictures (Wet Brush: 9 photos lost) | Filter refused the whole photo grid | Split refused grids in half; remake refused pictures | Partly fixed |
| Found in the run: AVIF photos rejected (10); over 9 pictures for one clip | Not written up | Keep any readable format; cap pictures | Fixed (not tested yet) |
| Found in the run: small planning faults, 72 chat AI turns that did nothing ($4.13) | Not written up | 13 fixes Julian approved were built | Fixed (not tested yet) |

## What we learned
- Sending raw page photos made things worse. The video model copies whatever it is given.
- This dip led straight to the [round 8](round-08.md) approach: back to a drawn, checked starting picture.

---

← [Round 6](round-06.md) · [All rounds](README.md) · [Round 8](round-08.md) →
