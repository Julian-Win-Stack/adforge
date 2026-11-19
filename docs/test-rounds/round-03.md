# Round 3: 4 ads after the "product shots must sell" fixes (October 10, 2026)

**Result: 1 of 4 ads worked.** Product shots that worked: 7 of 13. Talking shots and quality were not graded.

## What we tried
- 4 repeat products from round 1: toilet cleaner, phone case, handbag, Le Duo hair tool.
- Every product shot (the product in use, nobody talking to camera) must prove its line. Results filmed as they happen. Drops use the height the page claims.
- The check on starting pictures (a still image the video model starts from) was off. First round graded by taps.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Phone case: phone flips mid-clip, back camera on both sides (2 clips) | The video model invents the side it can't see | One fixed sentence: the side facing the camera stays facing it ([#123](https://github.com/Julian-Win-Stack/adforge/pull/123)) | Came back |
| Phone case: impossible phone, selfie screen on the camera side | The prompt asked for a view no camera gives. Picture check was off | A new check on starting pictures for impossible objects ([#124](https://github.com/Julian-Win-Stack/adforge/issues/124)) | Fixed (not tested yet) |
| Toilet cleaner: a different toilet and different dirt in one scene | Each starting picture was drawn on its own | One shared "before" picture per ad that every scene starts from ([#125](https://github.com/Julian-Win-Stack/adforge/issues/125)) | Fixed (proven later) |
| Toilet cleaner: cleaner pours like water, but it's a gel | Nobody asked for a pour. The video model made one up | In problem scenes nobody touches the product ([#127](https://github.com/Julian-Win-Stack/adforge/issues/127)) | Came back |
| Toilet cleaner: liquid comes out opposite the spout, cap never opened | Same made-up pour | Same fix | Came back |
| Le Duo: hair jumps from straight to curly at the end ("broke the laws of physics") | The video model is worst at slow shape changes | None. Hair already wrapped in the picture was proposed, not built | Not fixed |
| Le Duo: tool rotates in a fake, AI-like way (2 clips) | The video model can't turn a tool while gliding it | No prompt worked. Written down as a video model limit | Known limit |
| Le Duo: no hair inside the tool at the start | The starting picture put the tool beside the hair | None | Not fixed |
| Le Duo: he expected straight hair, got curls (2 clips) | The lines promised curls. He took it back ("I might be wrong") | Folded into the two Le Duo rows above | Dropped |

## What we learned
- Filler is gone, but the video model still adds actions nobody asked for (an unasked pour).
- Hair tools are a hard limit of the video model, not a prompt problem.
