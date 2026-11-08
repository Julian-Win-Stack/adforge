# Round 1: First 5 full ads (October 8, 2026)

## What this round tested

- The first full ads made from nothing but a product link: 5 products, made side by side.
- Products: a Steve Madden handbag, a Smartish phone case, Lemi Shine shower cleaner, L'ange Le Duo (a hair styler that curls and straightens) and Molly's Suds toilet cleaner.
- Talking scenes came from a presenter-video service; B-roll came from Boreal-H3.
- Julian watched the whole ads in chat, then re-graded every clip on a page the next day.

## Scorecard

| Measure | Strict rule | By his taps alone |
|---|---|---|
| Whole ads that work | 0 of 5 (0%) | 1 of 5 (his chat verdict: "OK" on the bag ad) |
| Good ad (quality) | 0 of 5 | — |
| B-roll clips in the final ads that work | 8 of 13 (62%) | — |
| Talking clips in the final ads that work | 12 of 17 (71%) | — |
| All clips, both bars together | 9 of 30 | — |

How these are counted (any bad word is a fail; "works" vs "good ad"): see [the summary](README.md#how-it-was-graded).

## Every failure, and what happened to it

Status words are explained in [the summary](README.md#status-words).

| # | Failure | How often | Bar | Status |
|---|---|---|---|---|
| 1 | The voice disappears in the last few seconds of every ad | 5 of 5 ads | Works | Fixed (proven in a later round) |
| 2 | Too many talking scenes: middle scenes are the presenter talking instead of B-roll proving the product; he wants more B-roll, maybe even the last scene | 5 of 5 ads (7 middle talking scenes) | Good ad | Came back |
| 3 | Toilet cleaner scene 2 zooms into the dirty toilet for no reason | 1 clip | Good ad | Dropped |
| 4 | Toilet cleaner scene 3: a talking scene claims the nozzle reaches around 360 degrees, but nothing proves it | 1 scene | Good ad | Fixed (not tested yet) |
| 5 | Le Duo scene 4: the woman does a straightening move but the hair ends up curly | 1 clip (also scene 3 below) | Works | Known limit |
| 6 | Le Duo scene 3 looks silly: the styler just spins by itself, 'the lowest form of B-roll' | 1 clip | Works | Fixed (not tested yet) |
| 7 | Phone case: both sides of the phone show the back camera, never the screen | 1 ad, 3 clips (scene 3, scene 6, scene 7; scene 4 re-graded pass) | Works | Came back |
| 8 | Shower cleaner: the line about soap scum building up was a talking scene; it should be a B-roll showing the shower getting dirty | 1 scene | Good ad | Partly fixed |
| 9 | Shower cleaner: no real before/after photo, so the ad couldn't show the result | 1 ad | Good ad | Known limit |
| 10 | Shower cleaner scene 4: before and after look exactly the same | 1 clip (scene 5 also listed in the ad's fail) | Good ad | Dropped |
| 11 | Every B-roll clip looks phone-filmed / hand-held, for no benefit (named on the phone-case close-ups) | 5 of 5 ads; named on phone case scene 3 and scene 4 | Good ad | Fixed (proven in a later round) |

Details, in the same order:

### 1. The voice disappears in the last few seconds of every ad

**Fixed (proven in a later round)** · 5 of 5 ads

- **Why it happened:** When the voice and the background music were mixed, the mixer stopped as soon as the voice track ended and threw away the voice's last part, so the last ~1.2 s of the final line was lost.
- **What was changed:** The mix now pads the voice and trims the sound to the picture length, and a test checks the last word is as loud as the first. (#116)
- **What happened next:** Round 2 (Oct 9): sound and picture the same length in all 5 ads, no cut-off complaint; never raised again in rounds 3-4.

### 2. Too many talking scenes: middle scenes are the presenter talking instead of B-roll proving the product; he wants more B-roll, maybe even the last scene

**Came back** · 5 of 5 ads (7 middle talking scenes)

- **Why it happened:** The planner's instructions said 'Most scenes are the person talking to camera', plus a home-made table giving each product type a B-roll share; a line that couldn't be filmed became a talking scene.
- **What was changed:** Planner rule: first scene talking, every middle scene B-roll, last scene talking or a product hero shot; a line that can't be filmed (e.g. 'no bleach') is said by the voice over another B-roll. (#116, #121)
- **What happened next:** Round 2: every middle scene was B-roll in all 5 ads. But many of those B-roll scenes were empty filler (round 2's main complaint). Round 12 again had an ad with 3 talking scenes and one 'useless' B-roll (Bondi Sands).

### 3. Toilet cleaner scene 2 zooms into the dirty toilet for no reason

**Dropped** · 1 clip

- **Why it happened:** The planner wrote 'the camera pushes toward the dirty ring': when nothing happens in a scene, it made the camera move instead, and the prompt asked to end close on the ring.
- **What was changed:** Rule: the one movement in a B-roll is a hand or the product, never the camera; video prompts open 'The camera stays still.' and never say where the camera ends. (#116)
- **What happened next:** Round 3 toilet cleaner scene 2: the camera still moved closer, and Julian passed it and decided a camera move alone is not a fail (Oct 10 17:10). The 'camera never moves' rule was deleted on Nov 1; the separate rule against forced zooms stays (the video-model limits list). No pointless-zoom complaint in rounds 2-4.

### 4. Toilet cleaner scene 3: a talking scene claims the nozzle reaches around 360 degrees, but nothing proves it

**Fixed (not tested yet)** · 1 scene

- **Why it happened:** The planner was told a talking person could name steps; a claim a camera could see was never required to be shown.
- **What was changed:** Planner rule: a claim about something the product does that a camera could see is a B-roll scene that shows it. (#116)
- **What happened next:** Round 2's toilet cleaner script didn't make that claim, so it was not re-tested; never raised again.

### 5. Le Duo scene 4: the woman does a straightening move but the hair ends up curly

**Known limit** · 1 clip (also scene 3 below)

- **Why it happened:** The planner split the page's 'twist while sliding' how-to: it moved the twist to before the shot ('glide the already clamped and rotated tool down'), so the film showed the page's straightening move while the line promised curls.
- **What was changed:** Julian's planner sentence: 'Still pick only ONE action. Write that one action the way the page words it. If the page says something happens while doing it, keep that part.' Plus the prompt writer films a 'while' part during the move. (#116)
- **What happened next:** It did not work. Round 3 Le Duo scene 2 and scene 4 failed (curl pops in suddenly, fake-looking rotation); round 4 scene 2 and scene 4 failed (the model can't turn the tool while gliding); the round 4 loop test's ever-stronger prompts got a partial turn at best. Written down as a video-model limit (the 'hair tools' entry in the video-model limits list).

### 6. Le Duo scene 3 looks silly: the styler just spins by itself, 'the lowest form of B-roll'

**Fixed (not tested yet)** · 1 clip

- **Why it happened:** The plan said 'the camera circles the vents' but the model rotated the product instead; nothing banned the product spinning on its own, and a show-the-vents shot proves nothing.
- **What was changed:** Rule: a hand never turns, flips or spins the product and it never spins by itself; the B-roll's one movement is a hand or the product doing something (plus the camera rule above). (#116)
- **What happened next:** Round 2's Le Duo vents scene showed hair gliding through the plates instead, but round 2 Le Duo was never graded; no spinning-product complaint in rounds 3-4.

### 7. Phone case: both sides of the phone show the back camera, never the screen

**Came back** · 1 ad, 3 clips (scene 3, scene 6, scene 7; scene 4 re-graded pass)

- **Why it happened:** Each start picture showed one face of the phone; scenes that turned, dropped or rotated it revealed the hidden face, and the video model, which only sees the start picture, drew another back.
- **What was changed:** Rule: never turn, flip or spin the product; the side facing the camera at the start faces it at the end, even after a drop; another side only gets its own scene that starts on it. (#116)
- **What happened next:** Round 2: no flip complaint. Round 3: phone case scene 2 and scene 3 flipped again; round 4: phone case scene 3 showed the back on both sides again. Later fixes: the same-side sentence (PR #123) and a clip judge that looks at 10 frames a second (PR #163). The video-model limits list: drops and tumbles still flip, so they aren't filmed.

### 8. Shower cleaner: the line about soap scum building up was a talking scene; it should be a B-roll showing the shower getting dirty

**Partly fixed** · 1 scene

- **Why it happened:** No B-roll kind existed for showing the problem; the planner put problem lines in talking scenes.
- **What was changed:** New B-roll kind 'shows the problem' (the problem visible at a glance, product in view, nothing changes). (#116)
- **What happened next:** Round 2 shower ad didn't use it (it made filler instead). The kind did work elsewhere: round 3 toilet cleaner scene 2 problem scene PASS. The shower cleaner was then dropped from tests.

### 9. Shower cleaner: no real before/after photo, so the ad couldn't show the result

**Known limit** · 1 ad

- **Why it happened:** The app asked the owner for a before/after photo; the test's stand-in owner had none (the shop's site has none), and the app made the scene anyway.
- **What was changed:** Rule: without the owner's photo of a thin film, no scene shows that film before or after; a code check refuses such a scene; the test harness can send real photos. (#116)
- **What happened next:** Round 2: still no photo, still filler; Julian stopped testing the shower cleaner until real before/after photos exist. The known-limits list: a before/after demo that lives only in a YouTube video on the page is never seen (Lemi Shine is the example).

### 10. Shower cleaner scene 4: before and after look exactly the same

**Dropped** · 1 clip (scene 5 also listed in the ad's fail)

- **Why it happened:** The result is an almost invisible film (soap scum), so a wipe shows no visible change; the start picture had no visible dirt.
- **What was changed:** Start-picture check asks that what the action will change is plainly visible ('shows the before'); a result that is only an invisible film goes back to the planner (thin-film rule). (#116)
- **What happened next:** Not re-tested: round 2's shower ad had no result scene and the product was then dropped from tests.

### 11. Every B-roll clip looks phone-filmed / hand-held, for no benefit (named on the phone-case close-ups)

**Fixed (proven in a later round)** · 5 of 5 ads; named on phone case scene 3 and scene 4

- **Why it happened:** Our own code forced it: every start-picture prompt said 'taken on a phone... Casual' and every video prompt opened 'A handheld phone video, casual, not cinematic'.
- **What was changed:** Removed the phone wording from both prompts; video prompts open 'A N-second video at real-time speed. The camera stays still.' (talking scenes keep their phone look). (#116)
- **What happened next:** No phone-look complaint in rounds 2-4.


## Left open

- Too many talking scenes: middle scenes are the presenter talking instead of B-roll proving the product; he wants more B-roll, maybe even the last scene
- Le Duo scene 4: the woman does a straightening move but the hair ends up curly
- Phone case: both sides of the phone show the back camera, never the screen
- Shower cleaner: the line about soap scum building up was a talking scene; it should be a B-roll showing the shower getting dirty
- Shower cleaner: no real before/after photo, so the ad couldn't show the result

## Caveats

- Graded in chat, not by taps. The "taps alone" column uses his spoken verdict per ad.
- Every ad failed "works" because the voice was cut off at the end of all 5 ads.
- He talked about quality on every ad (too much talking, phone-filmed look), so quality counts as graded here.
- Earlier tests graded single hand-made clips, not ads; they are not counted.

---

[All rounds](README.md) · [Round 2](round-02.md) →
