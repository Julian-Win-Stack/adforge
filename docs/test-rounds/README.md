# AdForge test rounds: every grade, every failure

AdForge is a chat agent that turns a product page link into a short video ad: a presenter talking to camera, B-roll shots of the product in use, and an end card. The B-roll clips are made with Creatify's Boreal-H3 video model; from round 9, every clip is.

Julian built AdForge alone and graded every test ad himself, from the first 5 ads on October 8, 2026 to round 12 on November 8.

This folder shows each round's grades, every failure he raised (even small ones), what was changed, and whether the change worked. Low early numbers are kept as they were. They show the climb.

## How it was graded

- **The rule.** Anything bad Julian said about an ad or a clip counts as a fail, even if he tapped pass. Something he was shown and said nothing about counts as a pass. If he changed his mind later, the later grade wins.
- **Two bars.**
  - **Works:** nothing broke. Breaks include a wrong product look, impossible physics, a garbled label, the wrong action, a made-up claim, an ad that gave up, or a crash.
  - **Good ad:** quality. Slow, boring, weak hook, too much talking, unconvincing.
  - Before round 11 he graded only "works". There, quality shows as *not graded*, not as 0%.
- **Headline number: whole ads that work.** An ad fails if he said anything bad about the ad, or about a clip that is in the final ad.
- **Second number: clips in the final ad that work.** B-roll and talking are shown apart. B-roll can be compared across every round; talking clips only where he graded them.
- **"By his taps alone"** shows what his pass/fail taps said before his words were applied, so you can see how much the strict rule changes things.

## Results by round

| Round | Date | What changed that round | Ads that work | B-roll clips in the final ads that work | Good ad (quality) | Ads that work by his taps alone |
|---|---|---|---|---|---|---|
| [1](round-01.md) | Oct 8 | First full ads from just a product link | 0 of 5 (0%) | 8 of 13 (62%) | 0 of 5 † | 1 of 5 ‡ |
| [2](round-02.md) | Oct 9 | Round 1 fixes: voice, B-roll in the middle, no phone look | 3 of 3 (100%) | 11 of 11 (100%) | 0 of 3 † | 0 of 3 ‡ |
| [3](round-03.md) | Oct 10 | Every B-roll must prove its line | 1 of 4 (25%) | 7 of 13 (54%) | not graded | 1 of 4 |
| [4](round-04.md) | Oct 12–14 | Same-side rule, one shared "before" picture, 10 products | 5 of 10 (50%) | 18 of 29 (62%) | not graded | 7 of 10 |
| [5](round-05.md) | Oct 17–19 | Clip judge and automatic remakes, 15 new products | 7 of 15 (47%) | 23 of 33 (70%) | not graded | 13 of 15 |
| [6](round-06.md) | Oct 20 | Fixing agent, product videos read, 7 reruns | 12 of 19 (63%) | about 24 of 30 (80%) § | not graded | 15 of 19 |
| [7](round-07.md) | Oct 29–31 | Page photos sent as references, no drawn starting picture | 4 of 15 (27%) | 14 of 27 (52%) | not graded | 12 of 15 |
| [8](round-08.md) | Nov 3 | One AI helper per B-roll scene, checked starting picture | 9 of 15 (60%) | 26 of 27 (96%) | not graded | 12 of 15 |
| [9](round-09.md) | Nov 4–5 | Every scene, talking too, filmed on the video model | 12 of 15 (80%) | 3 of 3 (100%) ¶ | not graded | 14 of 15 |
| [11](round-11.md) | Nov 5–6 | 10 new products, end pictures, quality graded | 6 of 10 (60%) | 2 of 4 (50%) | 0 of 8 made | 8 of 8 made |
| [12](round-12.md) | Nov 6–8 | Ad-quality rules: 45% B-roll, natural speech speed, end card | 3 of 12 (25%) | 10 of 20 (50%) | 0 of 12 | 9 of 12 |

Round 10 made no whole ads. It is in the scene tests below.

- † Rounds 1 and 2 came before the quality bar existed, but Julian named quality problems on every ad, so they count as graded.
- ‡ Rounds 1 and 2 were graded in chat, not by taps: this column is his spoken verdict on each ad.
- § Approximate. The data does not always say which take ended up in the ad, so scenes whose failed takes were dropped are left out. See [round 6](round-06.md).
- ¶ From round 9, demo scenes where the presenter speaks while using the product count as talking, so very few pure B-roll scenes ran.

**Talking clips in the final ads that work** (where graded): round 1: 12 of 17 · round 2: 6 of 6 · round 8: 47 of 48 · round 9: 54 of 56 · round 11: 28 of 28 · round 12: 18 of 25.

## Ads that work, round by round

```
Round  1  ░░░░░░░░░░░░░░░░░░░░   0%  (0 of 5)
Round  2  ████████████████████ 100%  (3 of 3)
Round  3  █████░░░░░░░░░░░░░░░  25%  (1 of 4)
Round  4  ██████████░░░░░░░░░░  50%  (5 of 10)
Round  5  █████████░░░░░░░░░░░  47%  (7 of 15)
Round  6  █████████████░░░░░░░  63%  (12 of 19)
Round  7  █████░░░░░░░░░░░░░░░  27%  (4 of 15)
Round  8  ████████████░░░░░░░░  60%  (9 of 15)
Round  9  ████████████████░░░░  80%  (12 of 15)
Round 11  ████████████░░░░░░░░  60%  (6 of 10)
Round 12  █████░░░░░░░░░░░░░░░  25%  (3 of 12)
```

## Scene tests

Tests of one hard scene or one feature, not whole ads. Their clip counts are kept apart from the ad line. Details: [scene-tests.md](scene-tests.md) and [round 10](round-10.md).

| Test | Date | What it tested | Clips that work |
|---|---|---|---|
| [Fix-from-a-note](scene-tests.md#fix-from-a-note-test) | Oct 12–14 | Fixing one scene from an owner's note | 2 of 3 |
| [Round 4 clip-judge loop](scene-tests.md#round-4-clip-judge-loop) | Oct 13–14 | Automatic remakes of clips the judge failed | 4 of 11 remakes |
| [Live ad: owner notes](scene-tests.md#live-ad-owner-notes-test) | Oct 17 | Three owner notes on one real ad | 2 of 3 (whole ad 0 of 1) |
| [Hand round 1](scene-tests.md#hand-round-1-show-dont-tell) | Oct 24 | Hand-sent clips, page photos as references, short prompts | 3 of 22 |
| [Hand round 2](scene-tests.md#hand-round-2-reference-video) | Oct 25 | The page's own video as a movement reference | 0 of 11 |
| [Scene-helper prototype](scene-tests.md#scene-helper-prototype) | Nov 3 | One AI helper draws, checks and films each scene | 15 of 15 |
| [Round 9 end-picture test](scene-tests.md#round-9-end-picture-test) | Nov 4 | An end picture of the finished look | 14 of 20 |
| [Round 10](round-10.md) | Nov 4–5 | End pictures chosen by the scene's AI helper after a fail | 8 of 11 |

## What the numbers show

- **The climb is real but not straight.** Ads that work went from 0 of 5 in round 1 to 12 of 15 in round 9, with two large dips (rounds 7 and 12).
- **Round 1's 0% was mostly one bug.** The voice was cut off at the end of all 5 ads. It was fixed the next day and never came back.
- **Round 2's 100% is only 3 ads, and all 3 failed on quality.** The new middle B-roll scenes were empty filler, which led to the rule that every B-roll scene must show something that sells.
- **Round 7 tried a new approach and it broke a lot.** Page photos went to the video model directly with no drawn starting picture: backgrounds leaked in and parts were made up. Round 8 went back to one checked starting picture per scene, with its own AI helper. Final-ad B-roll went from 52% to 96%.
- **Round 9's 80% came partly from playing it safe.** Julian's own note: "almost all the ads pass... because we didn't film the risky and hard actions."
- **From round 11 he graded quality, and no ad has passed that bar yet** (0 of 8, then 0 of 12). The top complaints: too slow, too much talking, a presenter who doesn't feel real, no emotional moment.
- **Round 12's dip to 25% came with three times more B-roll** (45% of ad time, up from 13%). Hard demos break more often: grips, physics, props that change between scenes. Julian also said his own pass taps that round "are not accurate", because he let small mistakes go.
- **The strict rule matters.** By his taps alone, 12 to 14 of 15 ads passed in rounds 7 to 9, and 9 of 12 in round 12. The strict rule is lower because he often tapped pass and still named a small fault.

## Biggest failures and where they went

| Failure | Rounds seen | Status now |
|---|---|---|
| Voice cut off at the end of the ad | 1 | Fixed (proven in a later round): round 2 onward |
| B-roll looked phone-filmed for no reason | 1 | Fixed (proven in a later round): rounds 2–4 |
| Too much talking, too little B-roll | 1, 5, 6, 11, 12 | Partly fixed after it came back (round 12: B-roll up from 13% to 45% of ad time) |
| B-roll that shows nothing (filler) | 2, 6, 12 | Partly fixed after it came back |
| Product turns and shows a made-up side (a phone with two backs) | 1, 3, 4 | Partly fixed; drops and tumbles are a known limit |
| Hair tools: can't twist while gliding, brushes break physics | 1, 3, 4, 6, 7, hand round 1, 12 | Known limit |
| Made-up way of using the product (floss, refills, twisting a paw washer) | 5, 6, 7, 9, 12 | Came back (some cases a known limit) |
| Product's contents or effect made up (foam, lather, makeup patches) | 6, 8, 9, 10 | Partly fixed (end pictures) |
| Labels, logos and print redrawn or garbled | 8, 10, 11, 12 | Known limit (newer picture fixes not tested yet) |
| Product or props change between scenes | 3, 12 | Not fixed (fixed in round 4, came back in round 12) |
| The clip judge disagreed with Julian (too strict, or missed faults) | 4, 5, 6, 7, 8, 9 | Partly fixed |
| A failed clip's retry only reworded the prompt | 7, 8, 9, 10, 11 | Fixed (not tested yet) |
| Backgrounds or strangers from page photos leaked into clips | 7, 8, 9 | Partly fixed |
| Ads too long, speech too slow, weak hook | 9, 11, 12 | Partly fixed: length fixed (proven in a later round); pace partly fixed; hooks fixed (not tested yet) |
| Presenter looks pasted on or not like a real person | 11, 12 | Fixed (not tested yet in a full round; passed 8 of 8 scenes in a small re-film test) |
| An ad made no video at all | 6, 11 | Fixed (proven in a later round): every round 12 ad got past planning |
| Unnatural grip, legs passing through straps | 12 | Not fixed |
| No emotional "after" moment | 12 | Not fixed |

Workarounds for known limits are written down in [video-model limits](../h3-limits.md) and [known limits](../limitations.md).

## Status words

Every failure in the round pages has one of these statuses:

| Status | Meaning |
|---|---|
| Fixed (proven in a later round) | A change was made, and a later graded round showed the failure stopped. |
| Fixed (not tested yet) | A change was made, but no later graded round has checked it. |
| Partly fixed | Better, but not gone, or fixed for some cases only. |
| Came back | A change was made, and the failure showed up again later. |
| Not fixed | No change has fixed it. |
| Known limit | The video model cannot do it reliably yet, so the app avoids it. |
| Dropped | Julian chose not to fix it (too small, a test shortcut, or a product set aside). |

## Side tests

Small one-question tests between rounds (picture check, rule checks, reference pictures, note fixes and more). Every result, yes or no: [side-tests.md](side-tests.md).

## Words used in these pages

| Word | Meaning |
|---|---|
| Boreal-H3, the video model | Creatify's AI model that turns a picture and a text prompt into a video clip. |
| Scene | One part of the ad's script, filmed as one clip. Early ads had up to 8 scenes; later ones 3 to 5. |
| Take, try, remake | One filmed attempt at a scene. A remake is a new take after a failed one. |
| Clip number | The app's id for one take (for example clip 3839), kept so each grade can be traced. |
| Talking scene | The presenter speaks to camera. |
| B-roll | A shot of the product in use, with no one speaking to camera. |
| End card | The closing frame of the ad, showing the product. |
| The planner | The step that writes the script and the scene list. |
| Starting picture / end picture | A still image drawn for a scene's first (or last) frame, which the video model films from. |
| The picture check | An AI check that looks at each starting picture before filming. |
| The clip judge | An AI that watches each finished clip and fails clips with faults. |
| The fixing agent | An AI that remade failed clips (rounds 6–7). |
| The scene's AI helper | From round 8, one AI per scene that picks photos, draws the starting picture, films, judges and retries. |
| Owner note | A shop owner's request to change a scene in a finished ad. |
| Presenter-video service | A separate service used for talking scenes until round 8. From round 9 every scene is made with Boreal-H3. |
| Reference mode | A way of filming where pictures are sent to the video model as loose guides, not as the exact first frame. |
| Product check | The clip judge's question: does the product look like the shop's photos all the way through? |
| Borderline | A fail that rests only on a small or indirect remark, such as a fault named in a clip inside an ad he tapped pass. |
| Claude | The AI coding assistant that builds AdForge with Julian. |
| #123 | A pull request or issue number in the project's GitHub repository. |

## How to read the numbers

- **Small samples.** 3 to 19 ads a round. One ad moves a round by 5 to 33 points.
- **Products change.** Rounds 1–4 shared most products; rounds 5–6 used new ones; rounds 7–9 used the same 15; rounds 11–12 used new ones again.
- **Each round changed several things at once.** A rise or dip cannot always be pinned on one change.
- **One grader, rising bar.** Julian graded more strictly as the ads got better; round 12 added quality.
- **"Shown, no feedback" is a pass.** He may not have watched every take, so some passes may be generous.
- **What counts as an ad.** In round 11, two products that gave up before any video count as failed ads. In round 6, three products that made no ad are left out.
- **Where workers counted differently**, the per-ad data was recounted with the rule above; the exceptions are marked § and ¶ in the table.
