# Scene tests

Small tests of one hard scene or one idea. Counts stay apart from the rounds, because many clips are repeat attempts of one prompt. How we grade: [the summary](README.md#how-we-grade). Status words: [the summary](README.md#status-words). [Round 10](round-10.md) is also a scene test.

| Test | Date | What it asked | Result |
|---|---|---|---|
| [Fix-from-a-note test](#fix-from-a-note-test) | Oct 12–14 | Can the app fix a scene from the user's note? | 2 of 3 items work |
| [Round 4 clip-judge loop](#round-4-clip-judge-loop) | Oct 13–14 | Do automatic remakes fix failed clips? | 4 of 11 remakes fixed it |
| [Live ad: user-notes test](#live-ad-user-notes-test) | Oct 17 | Do 3 user's notes on a real ad get done? | 2 of 3 clips work |
| [Hand round 1: show, don't tell](#hand-round-1-show-dont-tell) | Oct 24 | Do short prompts plus labelled photos work? | 3 of 22 clips work |
| [Hand round 2: reference video](#hand-round-2-reference-video) | Oct 25 | Does the page's video guide the motion? | 0 of 11 clips work |
| [Scene-helper prototype](#scene-helper-prototype) | Nov 3 | Does one AI per scene fix old failed scenes? | 15 of 15 attempts work |
| [Round 9 end-picture test](#round-9-end-picture-test) | Nov 4 | Does an end picture fix the used-product look? | 14 of 20 attempts work |
| [Calm hook face test](#calm-hook-face-test) | Nov 9 | Does a calm face stop acted hooks? | 5 of 5 clips work |
| [User-note test, Nov 17 ads](#user-note-test-nov-17-ads) | Nov 18 | Does the latest app fix failed scenes from a user's note? | 6 of 11 notes fully fixed |

All dates are 2026. The clip checker is an AI that watches each clip and passes or fails it. A product shot shows the product in use, nobody talking to camera.

## Fix-from-a-note test

*Oct 12–14.* **2 of 3 items work.** On the round 4 toilet-cleaner ad, the app restated each of 2 fixes and its cost, waited for a yes, and remade only that scene.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Scrub direction changed as asked, but the brush still scrubs the seat | The note named only the direction | Clip checker now catches it; a planning step looks at the scene first | Fixed (proven later) |

## Round 4 clip-judge loop

*Oct 13–14.* **4 of 11 remakes fixed it.** The clip checker failed 7 of 29 round 4 product shots; each was remade up to twice. The app kept Julian's pick in 7 of 7 scenes.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Le Duo: tool never turns while gliding (4 of 4 remakes) | The video model tilts instead of turning | Stop rewording; avoid filming the twist | Known limit |
| Le Duo scene 3: odd hair from the mannequin's chin | Fault is in the reused starting picture | Nothing found | Not fixed |
| Phone case scene 2: first remake still had extra lenses | Random video model fault | Second remake fixed it | Fixed (proven later) |
| Old-vs-new check said "not fixed" on 2 of 7 fixed clips | It was a separate, stricter AI call | Made it one question to the clip checker | Fixed (not tested yet) |

**Learned:** a remake from the same starting picture can't fix a fault in that picture.

## Live ad: user-notes test

*Oct 17.* **2 of 3 clips work** (any complaint counts as a fail). 3 of 3 notes did what was asked. Talking shots were faked as stills with a voice, to save money.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Scene 2: cleaner pours like water, not gel | The video model makes up how contents look | Nothing; came back in round 6 | Known limit |
| Presenter doesn't seem to speak | Talking shots were faked on purpose | No app fix needed | Dropped |

## Hand round 1: show, don't tell

*Oct 24.* **3 of 22 clips work** (Glow Recipe 3 of 5, Cocofloss 0 of 4, Wet Brush 0 of 11, Zulay 0 of 2). Made by hand: no starting picture, labelled page photos, one-sentence prompts.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Cocofloss: floss is a stiff stick, still strand or whole spool | The video model can't move thin thread | Stop filming actions it can't do | Known limit |
| Glow: made-up bottle with lid off | No lid-off photo sent | Send it, labelled | Partly fixed |
| Wet Brush: brushed or merged the hair drawn in the room picture | The video model copies what's in pictures | Every shot now starts from a drawn, checked picture | Came back |
| Wet Brush: made-up back with bristles on both sides | Unlabelled back photo; the service's prompt rewrite dropped the key line | Label sides; rewrite off | Partly fixed |
| Wet Brush: two brushes at once | The video model loses hidden objects | Left to the clip checker | Known limit |
| Wet Brush: bristles appear on the back, then vanish | Same tracking habit | Rewrite off; checker looks for a changed product | Partly fixed |
| Wet Brush: brush jumps to the other hand | It doesn't track one brush in one hand | Product given up | Known limit |
| Clips too fast when speed isn't stated | The video model crams steps | Every prompt opens with "Film this in real-time speed." | Fixed (proven later) |
| Zulay (2) and Glow (1): Julian later agreed they fail | Product changes, unreal motion | None; why the clip checker was kept | Not fixed |
| Photo picker dropped key Glow photos | It dropped photos without the product | Keep every photo except word-only cards | Partly fixed |

## Hand round 2: reference video

*Oct 25.* **0 of 11 clips work.** Julian passed 4 in chat ("perfect"), then later agreed they fail. Garject garlic press, by hand, with the page's video as a motion guide.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Garject: made-up extra handle | One black photo with a red video | Add video frames; all references one colour | Came back |
| Garlic in big pieces, not fine bits | No picture showed the mince | Add a frame of the fine mince | Came back |
| Photos of other states made it ignore the video | Photos outweigh video | Send only the states the action needs | Came back |
| Black photo plus red video: a different press head | Two colours get blended | Auto recolour step, redesigned, not run on real pictures | Partly fixed |
| The 4 "perfect" clips fail too | Too hard for the video model | Garject now "normal to fail" | Known limit |

## Scene-helper prototype

*Nov 3.* **15 of 15 attempts work.** One AI per scene that draws, films and checks it: it draws a starting picture (a still image the video model starts from), and the video model films from it. 5 old round 7 scenes, 3 attempts each. This led to the round 8 build.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Junk photos sent to the picture AI (a table, a certificate) | Every photo sent, cut at 15 by number | The scene's AI picks each photo and says why | Fixed (not tested yet) |

## Round 9 end-picture test

*Nov 4.* **14 of 20 attempts work.** 3 scenes that failed on the used-product look, re-filmed with an end picture (a still image the video model must end on). Start and end picture 6 of 6, reference mode 2 of 6, positive-only wording 4 of 6, the service's suggested prompt 2 of 2.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Makeup and gel turned to patches or lather | The video model invents the finished look | After a product fail, make an end picture | Partly fixed |
| Beautyblender scene 2: foundation just vanishes | Nothing put the sponge on it | Prompt puts the cause under the action | Partly fixed |
| Reference mode follows the end picture only sometimes | It treats pictures as loose guides | Use start and end picture mode | Fixed (proven later) |

## Calm hook face test

*Nov 9.* **5 of 5 clips work.** Round 13 hooks looked acted (2 or 3 face moves per prompt). 5 hooks re-filmed with a calm face written once. Julian: "every clip pass." Built.

## User-note test, Nov 17 ads

*Nov 18.* **6 of 11 notes fully fixed** (same 15 products 4 of 7, new 15 products 2 of 4). Each failed Nov 17 scene got one user's note; the latest app fixed a frozen copy. Cost $28.68. The "change made?" check agreed 9 of 10; the clip checker caught 0 of 2 new breaks.

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Owala scene 2: extra brown loop still there | Not found yet | Nothing yet | Not fixed |
| Furbo scene 3: animations still there | Not found yet | Nothing yet | Not fixed |
| Brooklinen scene 2: bed shapes went "really weird" | Not found yet | Nothing yet | Not fixed |
| Allbirds scene 3: background changes mid-clip | Not found yet | Nothing yet | Not fixed |
| Stanley scene 2: declined the logo note, no new ad | Offered on-screen text instead | Nothing yet | Not fixed |

---

[All rounds](README.md)
