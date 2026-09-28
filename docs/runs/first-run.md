# First run: 24 test ads

- Date: 2026-09-27
- Commit: `433f535` (branch `27-captions-music-overlays`), with no uncommitted code, for every
  run from "23 · … (redo)" on. Worker restarted at 2026-09-27 23:32 UTC to load it.
- Runs 21, 22, 23 and 24 and the first 01 were made on `433f535` **plus** an uncommitted change to
  `backend/jobs/work.py`: a silent clip (a scene that shows the product) was asked for exactly
  as long as its audio instead of rounded up to a whole second. The diff is in
  [first-run-uncommitted.diff](first-run-uncommitted.diff). It made #23 and #01 fail (the video
  model returned clips 0.1 s shorter than their audio), so the user had the change stashed
  (`git stash list`: "exact-length silent clips (from first run)") and #23 and #01 redone.
  #21, #22 and #24 were not redone: #21 and #22 stopped before any clip, and #24 finished.
- First message: runs #21–24, #1–6 (and the redos) used `Make a 30-second ad for this product:
  <link>`. From #7 on, the user changed the plan: `Make an ad for this product: <link>`, with no
  length, except #8 (`15-second`) and #9 (`6-second`, to force the shortening step).
- From #8 on: commit `71dec51` ("Ask for a silent clip as the shortest Boreal makes that lasts
  its line"; worker restarted 2026-09-28 00:14 UTC). Until then a silent clip was asked for as
  its line rounded up to a whole second, and up to a second of its motion was cut away; the
  user saw those cuts as awkward in #2–7 and chose not to redo finished ads. Measured on this
  run's clips, Boreal makes floor(3s)/3 + 1/24 seconds when asked for `s`, so the fix asks for
  the shortest third of a second that still lasts the line (at most about 1/3 s cut away).
- Products: [docs/test-products.md](../test-products.md). Prices of #12 and #19 re-checked
  the same day: unchanged ($14.99 was $19.99; $80.97 was $89.99).
- Langfuse project: `cmuk6bugf0bvvad0cplzq08su`.
- Cost: sum of `gateway_modelcall.cost_usd` for the session. "Without produce" leaves out
  `purpose='produce'` (the producer's own turns). Recorded costs price every input token at
  full rate, ignoring OpenAI's prompt-caching discount, so they may be above the real bill.

- Backup (2026-09-28, after the last run): `~/adforge-backups/first-run-2026-09-28/`:
  `adforge-db.dump` (`pg_dump -Fc` of the `adforge` database) and `adforge-media.tar.gz` (the
  `adforge_media` volume the app mounts at `/app/media`).

## Summary

| # | Name | Result | Cost | Cost without produce |
|---|------|--------|------|----------------------|
| 21 | 21 · Needs JavaScript · SNOO Smart Sleeper | Stopped early: page unreadable | $0.0242 | $0.0009 |
| 22 | 22 · Category page · Nécessaire body collection | Stopped early: page lists several products | $0.0237 | $0.0015 |
| 23 | 23 · Amazon · Stanley Quencher H2.0 | Failed: scene 5's clip (6 of 7 scenes made; no finished ad) | $2.4775 | $1.3527 |
| 24 | 24 · No price · Life Fitness Integrity Treadmill | Finished ad (24.52 s) | $1.2606 | $0.8530 |
| 1 | 01 · Skincare · Naturium Multi-Active Exosome Serum | Failed: scene 2's clip (3 of 4 scenes made; no finished ad) | $1.5274 | $0.9038 |
| 23 | 23 · Amazon · Stanley Quencher H2.0 (redo) | Finished ad (28.32 s) | $1.6848 | $1.1045 |
| 1 | 01 · Skincare · Naturium Multi-Active Exosome Serum (redo) | Failed: designing the voice (after the plan) | $0.1538 | $0.0782 |
| 2 | 02 · Makeup · Merit Flush Balm | Finished ad (27 s) | $1.4773 | $1.0120 |
| 3 | 03 · Supplement · Vital Proteins Salted Caramel Collagen Peptides | Finished ad (29 s) | $1.5516 | $1.0346 |
| 4 | 04 · Clothing · Mack Weldon Vintage French Terry Crew Neck Sweatshirt | Finished ad (24.46 s) | $1.2953 | $0.8778 |
| 5 | 05 · Well-known brand · Steve Madden Kenzo Bag Gold | Finished ad (25.68 s) | $1.3402 | $0.9026 |
| 6 | 06 · Gadget · Anker 313 Power Bank | Finished ad (30.16 s) | $1.5811 | $1.0653 |
| 7 | 07 · Kitchen · Great Jones Dutch Baby | Finished ad (25.7 s) | $1.4816 | $0.9670 |
| 8 | 08 · Cleaning · Molly's Suds Toilet Bowl Cleaner | Finished ad (14.4 s) | $1.0961 | $0.6808 |
| 9 | 09 · Food · Momofuku Chili Crunch Sauce | Finished ad (5.34 s) | $0.6051 | $0.3431 |
| 10 | 10 · Pet · maxbone Enrichment Tether Toy | Finished ad (21.9 s), after many refused pictures | $2.2021 | $1.1063 |
| 11 | 11 · A smell · Beardbrand Fox Hunt Men's Cologne | Finished ad (32 s) | $1.8403 | $1.1928 |
| 12 | 12 · See-through · Smartish Gripmunk Slim Case for iPhone 17e | Finished ad (23 s), closing line mispronounced | $1.7404 | $1.0332 |
| 13 | 13 · Tiny · Gorjana Melrose Diagonal Studs | Finished ad (11.2 s) | $0.9737 | $0.5958 |
| 14 | 14 · Too big to hold · Branch Swivel Chair | Finished ad (32 s) | $1.5943 | $1.0342 |
| 15 | 15 · A kit · Nécessaire The Body Ritual Kit | Finished ad (28 s), after the producer stalled | $1.8527 | $1.1251 |
| 16 | 16 · Baby · Mushie Space Teething Ring | Finished ad (28 s) | $1.3089 | $0.9155 |
| 17 | 17 · Person suits buyer · Brickell Beard Oil | Finished ad (18 s) | $1.1125 | $0.6941 |
| 18 | 18 · Reviews claim more · Starface Hydro-Stars + Big Yellow | Failed: planning (product name check) | $0.4071 | $0.2848 |
| 19 | 19 · On sale · Titan Foam Fitness Mat | Finished ad (25.8 s) | $1.2357 | $0.8611 |
| 20 | 20 · Several prices · Supergoop Unseen Sunscreen SPF 50 | Finished ad (29 s) | $1.6640 | $1.0853 |
| 1 | 01 · Skincare · Naturium Multi-Active Exosome Serum (3rd try) | Finished ad (28.7 s) | $1.4696 | $0.9598 |


Total for all 27 sessions: $34.98; without produce $22.07. Finished ads: 21 of the 24
products. No ad for #21 and #22 (they stop early by design) or #18 (planning failed).

## Seen across runs (facts, for the error analysis; not fixed here)

- **A line can't be changed once it passes the fact check.** In #23 and #01 (first tries) and
  #12, the producer asked to change a line, was told yes, and its tools refused ("the original
  checks had already passed"). In #12 a mispronounced name ("Gripmonk") went into the ad.
- **Silent clips came back 0.1 s short** until `71dec51` (see the top). No clip failed that way
  after it.
- **The product-name check wants the name letter for letter.** #18 never got a plan (name with
  "®" and "+"); #01 (3rd try) failed it once.
- **Inworld refuses a voice description with a non-ASCII character** (a curly apostrophe), with
  400; the app records only the status line and the chat calls it "a server error" (#01 redo).
- **OpenAI's image rate limit is 5 input images a minute**, and a 6- or 7-scene ad asks for
  more at once: 429s in #23 redo, #2, #3, #11, #14, #15, #20, and a scene gave up once in #11
  and #15 before the producer retried it.
- **OpenAI's safety system refused 11 presenter pictures in #10**; the chat said only "failed
  again unexpectedly".
- **The producer stopped by itself once** (#15), after the presenter and voice, with nothing
  failed and no question; "please try again" was then misread as "make a new presenter".
- **Agent messages whose text is "[Attached 1 picture]"** with no attachment on them: #23 redo,
  #3, #9, #11, #14, #20, #01 (3rd try).
- **Availability conflicts became questions** (#10, #11, #15), as did a model-name mismatch
  (#12) and a subscription price (#17). All pages' own product data said "available".
- **Photo counts differ from `docs/test-products.md`** in most runs (the chat's count is
  usually higher). The list counted photos differently; not checked further.
- **Asked-for length:** runs without a length came out 11.2 s to 32 s.
- **Noted by the user while watching #2–7:** the presenter sometimes looks like slow motion,
  out of step with the voice. Our assembly only trims clips (it never changes their speed), so
  this is probably Boreal's lip-sync, but it has not been checked.

## Runs

### 21 · Needs JavaScript · SNOO Smart Sleeper

- Session: `9007e285-8d95-4a8e-a79d-686e6e6f1f20`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/9007e285-8d95-4a8e-a79d-686e6e6f1f20
- First message: `Make a 30-second ad for this product: https://www.happiestbaby.com/products/snoo-smart-sleeper-bassinet`
- Result: stopped early, after reading the page (as predicted: judged unreadable).
- The producer said: "I couldn't access usable product details from that page—only navigation
  and footer content came through. Please send another public product-page link with the SNOO
  description and photos."
- Questions asked: none answered (the reply above asks for a different link; not answered).
- Cost: $0.0242; without produce $0.0009. Model calls: 1 `check_page`, 2 `produce`.
- Broke: nothing.

### 22 · Category page · Nécessaire body collection

- Session: `7c048689-16bd-4072-a5e1-ea3591ee5cf2`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/7c048689-16bd-4072-a5e1-ea3591ee5cf2
- First message: `Make a 30-second ad for this product: https://necessaire.com/collections/body`
- Result: stopped early, after reading the page (as predicted).
- The producer said: "That link shows a collection of several body products. Please send the
  link to the specific product you want featured."
- Questions asked: none answered (asks for a specific product link; not answered).
- Cost: $0.0237; without produce $0.0015. Model calls: 1 `check_page`, 2 `produce`.
- Broke: nothing.

### 23 · Amazon · Stanley Quencher H2.0

- Session: `f810c0be-f452-48c2-834a-a798814c7fe0`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/f810c0be-f452-48c2-834a-a798814c7fe0
- First message: `Make a 30-second ad for this product: https://www.amazon.com/dp/B0CJZMP7L1`
- Result: **failed at scene 5's clip.** Scenes 1–4, 6 and 7 finished; no finished ad.
- Questions asked and answers:
  1. "The page details are ready, but Amazon didn't provide any usable product photos. Please
     attach at least one clear photo of the lilac Stanley Quencher H2.0 30 oz." (as predicted)
     → Answered "Here's a photo of it." with one photo from Stanley's own site:
     `B2B_Web_PNG-TheQuencherH2.OFlowStateTumbler30OZ-Lilac-Front-1.png` (1515×1515 PNG, from
     the `adventure-quencher-travel-tumbler-30-oz` product; Lilac is no longer a listed variant
     there, but its photos are still on the product).
  2. (Unexpected) "For scene 5, may I shorten the line to: 'The narrow base fits most cup
     holders for easy travel'?" → Answered "Yes."
- What broke:
  - Scene 3's starting picture failed first on an image-service rate limit (429, "still down
    after 3 tries"); chat: "Scene 3's visual couldn't be created because the image service hit
    a temporary rate limit, even after several retries." Retried later by the producer and
    finished.
  - Scene 5 (a scene that shows the product: "A hand places the tumbler upright into a car cup
    holder.") has a 4.5-second line. The video model returned a 4.4-second clip four times (3
    by the producer, 1 after "please try again"). Step result: "the video model couldn't make
    the clip (it came back 4.4 seconds long, shorter than the line's 4.5 seconds of audio)".
    This is the kind of scene the uncommitted `work.py` change affects (asked for exactly the
    audio's length instead of rounded up to 5 s). The `make_clip` model calls confirm it: all
    four for scene 5 asked for `"audio": null, "seconds": 4.5`. The other silent clips asked for
    5.28 s and 5.14 s and were usable.
  - The producer tried to regenerate the audio: "The voice line can't be regenerated because it
    already matches the approved script".
  - After "Yes.", the producer could not change the line: "The checker wouldn't accept the edit
    because it hadn't flagged that line itself." then "I couldn't apply the shorter line because
    the production system has locked the previously approved script. Since the existing clip
    remains 0.1 seconds too short, I can't complete or assemble the ad with the available
    tools."
  - After "please try again": "The retry failed for the same reason: the generated clip is 4.4
    seconds, but the locked voice line is 4.5 seconds. I can't complete or assemble the ad
    unless the production system allows the line to be shortened."
- Cost: $2.4775; without produce $1.3527. 101 model calls (42 `produce`, 10 `make_clip`,
  7 + 3 failed `make_starting_picture`, …).

### 24 · No price · Life Fitness Integrity Treadmill

- Session: `33ca8bc1-1fb6-4732-a727-e9de35a402b8`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/33ca8bc1-1fb6-4732-a727-e9de35a402b8
- First message: `Make a 30-second ad for this product: https://www.lifefitness.com/en-us/catalog/cardio/treadmills/integrity-series-treadmill`
- Result: **finished ad**, five scenes. Chat: "Your finished ad is ready. It runs 24.52 seconds
  and includes all five scenes, captions, product overlays, presenter voice, and background
  music." (30 seconds were asked for.)
- Questions asked and answers:
  1. "What price should the ad state for the Integrity Series Treadmill? The page only offers a
     quote and doesn't list a current price." (as predicted) → Answered "The price is $4,999."
- The page read gave two usable photos; all five starting pictures used photo 1.
- Cost: $1.2606; without produce $0.8530. 59 model calls, none failed.
- Broke: nothing.

### 01 · Skincare · Naturium Multi-Active Exosome Serum

- Session: `b1daed9c-3f87-4148-921a-2dd3e181d2d3`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/b1daed9c-3f87-4148-921a-2dd3e181d2d3
- First message: `Make a 30-second ad for this product: https://naturium.com/products/multi-active-exosome-serum`
- Result: **failed at scene 2's clip.** Scenes 1, 3 and 4 finished; no finished ad.
- Not a one-photo ad, as predicted: chat said "I've got the page details and two product
  photos."
- Questions asked and answers:
  1. (Unexpected) "Scene 2 failed again because the clip is 0.1 seconds shorter than its audio.
     May I shorten the line to: 'Use one to two pea-sized drops morning and night, avoiding the
     eye area'?" → Answered "Yes."
- What broke: the same failure as #23.
  - Scene 2 is a silent clip (it shows the product). All four `make_clip` calls for it asked for
    `"audio": null, "seconds": 5.54`; the video came back 5.4 s against 5.5 s of audio. Chat:
    "Scene 2's clip came back slightly shorter than its voice line, so it couldn't be used."
  - "The voice tool kept the existing approved take rather than making a new one."
  - After "Yes.": "The checker couldn't directly replace that line because it had already
    passed." then "I'm sorry—the production tools have locked the original line and won't
    accept the approved shorter wording, so I can't complete scene 2 or assemble the ad. The
    other three scenes are finished."
  - After "please try again": "The retry failed for the same reason: the clip is 5.4 seconds,
    but the locked audio is 5.5 seconds. I'm sorry, but I can't complete or assemble the ad with
    the available tools."
- Cost: $1.5274; without produce $0.9038. 66 model calls, none failed.

### 23 · Amazon · Stanley Quencher H2.0 (redo)

- Session: `01a05283-9a9d-4ecd-9a21-81fd4178c320`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/01a05283-9a9d-4ecd-9a21-81fd4178c320
- First message: `Make a 30-second ad for this product: https://www.amazon.com/dp/B0CJZMP7L1`
- Result: **finished ad**, six scenes. Chat: "Your finished vertical ad is ready—28.32 seconds,
  with captions, product overlays, voiceover, and background music."
- Questions asked and answers:
  1. "The page has enough product details, but no usable photos were available. Please attach
     at least one clear photo of the Stanley Quencher tumbler so I can create the ad." (as
     predicted; unlike the first try, it didn't say "lilac") → Answered "Here's a photo of it."
     with the same Lilac photo as the first try.
- What broke:
  - 2 `make_starting_picture` calls failed on OpenAI's image rate limit (429, "Rate limit
    reached for gpt-image-2.5-sunburst … on input-images per min"). Retried and finished; not
    mentioned in the chat.
  - Message #24 is an agent message whose whole text is "[Attached 1 picture]" with no
    attachment.
  - The producer tried to start scene 5's clip before its picture was ready: "Scene 5's visual
    is still rendering, so its clip couldn't start yet. I'll create it as soon as the visual is
    ready." It did, and the scene finished.
- Cost: $1.6848; without produce $1.1045. 72 model calls, 2 failed.

### 01 · Skincare · Naturium Multi-Active Exosome Serum (redo)

- Session: `a0754894-d422-406a-828d-3b626be660f7`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/a0754894-d422-406a-828d-3b626be660f7
- First message: `Make a 30-second ad for this product: https://naturium.com/products/multi-active-exosome-serum`
- Result: **failed at designing the presenter's voice**, right after the five-scene plan and
  the presenter's picture. No scenes made.
- Two photos again: "The page is clear and both product photos are usable".
- Questions asked: none.
- What broke:
  - Both `design_voice` calls (the first, and one after "please try again") failed with
    "HTTPStatusError: Client error '400 Bad Request' for url
    'https://api.inworld.ai/voices/v1/voices:design'".
  - Chat: "The presenter creation hit an unexpected error, so I couldn't continue generating
    the ad. Please try again, and I'll pick up from the finished five-scene plan." After
    "please try again": "The presenter tool failed again due to a server error. I can't
    continue until it's working; please try again later."
  - Why (found by sending the same request to Inworld once more by hand, outside the app, to
    read the reply the app doesn't record): Inworld answered `400 {"code":3, "message":
    "design_prompt (Voice Description) must contain visible printable English (ASCII)
    characters and LF/CRLF line breaks."}`. The voice description written for this ad was "A
    clear, warm woman’s voice …", with a curly apostrophe (’), which is not ASCII. The app
    records only the status line, not this reply, and the chat calls it "a server error".
- Cost: $0.1538; without produce $0.0782. 11 model calls, 2 failed.

### 02 · Makeup · Merit Flush Balm

- Session: `bb1500f2-858d-4a14-8420-3b90924f9db0`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/bb1500f2-858d-4a14-8420-3b90924f9db0
- First message: `Make a 30-second ad for this product: https://www.meritbeauty.com/products/flush-balm`
- Result: **finished ad**, six scenes. Chat: "Your finished vertical ad is ready. It runs 27
  seconds and includes captions, product overlays, and background music."
- Photos: "I've got the product details and four usable photos." (the list says 3)
- Questions asked: none.
- What broke: 2 `make_starting_picture` calls failed on OpenAI's image rate limit (429);
  retried and finished, not mentioned in the chat.
- Cost: $1.4773; without produce $1.0120. 67 model calls, 2 failed.

### 03 · Supplement · Vital Proteins Salted Caramel Collagen Peptides

- Session: `67352137-fadb-450a-b036-ffb1372db603`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/67352137-fadb-450a-b036-ffb1372db603
- First message: `Make a 30-second ad for this product: https://www.vitalproteins.com/products/salted-caramel-collagen-peptides`
- Result: **finished ad**, six scenes. Chat: "Your finished 29-second ad is ready, with
  captions, overlays, voiceover, and background music."
- Photos: "I've got the page details and 10 usable product photos." (the list says 8)
- Questions asked: none.
- What broke:
  - 2 `make_starting_picture` calls failed on OpenAI's image rate limit (429); retried and
    finished, not mentioned in the chat.
  - Message #22 is an agent message whose whole text is "[Attached 1 picture]" (as in #23 redo).
- Cost: $1.5516; without produce $1.0346. 69 model calls, 2 failed.

### 04 · Clothing · Mack Weldon Vintage French Terry Crew Neck Sweatshirt

- Session: `06b1f8ad-45d4-4fd9-9be3-6301c6211286`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/06b1f8ad-45d4-4fd9-9be3-6301c6211286
- First message: `Make a 30-second ad for this product: https://mackweldon.com/products/vintage-french-terry-crew-neck-sweatshirt`
- Result: **finished ad**, five scenes. Chat: "Your finished ad is ready. It runs 24.46 seconds
  and includes all five scenes, captions, overlays, and background music."
- Photos: "I've got the page details and all nine product photos" (the list says 5). Scene 1:
  "a clear front view of the charcoal sweatshirt" (4 colours at one price).
- Questions asked: none.
- What broke: nothing.
- Cost: $1.2953; without produce $0.8778. 58 model calls, 0 failed.

### 05 · Well-known brand · Steve Madden Kenzo Bag Gold

- Session: `6c9c5065-de0f-4fd4-a0ca-0f8a26204f9e`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/6c9c5065-de0f-4fd4-a0ca-0f8a26204f9e
- First message: `Make a 30-second ad for this product: https://www.stevemadden.com/products/bkenzo-gold`
- Result: **finished ad**, five scenes. Chat: "Your finished ad is ready—25.68 seconds, with
  captions, overlays, voiceover, and instrumental music."
- Photos: "The page has six usable photos." (the list says 4)
- Chat said of scene 5: "The included duster bag isn't shown because it isn't pictured on the
  page".
- Questions asked: none.
- What broke: nothing.
- Cost: $1.3402; without produce $0.9026. 59 model calls, 0 failed.

### 06 · Gadget · Anker 313 Power Bank

- Session: `30cc012f-fe29-4929-abc5-464296c4f141`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/30cc012f-fe29-4929-abc5-464296c4f141
- First message: `Make a 30-second ad for this product: https://www.anker.com/products/a1229`
- Result: **finished ad**, six scenes. Chat: "Your finished ad is ready—30.16 seconds, with
  captions, product overlays, voiceover, and background music."
- Photos: "I've got the product details and 10 usable photos" (the list says 9).
- Questions asked: none.
- What broke: nothing.
- Cost: $1.5811; without produce $1.0653. 67 model calls, 0 failed.

### 07 · Kitchen · Great Jones Dutch Baby

- Session: `c9232a32-2edf-4a15-a779-460a0d305841`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/c9232a32-2edf-4a15-a779-460a0d305841
- First message: `Make an ad for this product: https://greatjonesgoods.com/products/dutch-baby`
- Result: **finished ad**, six scenes. Chat: "Your finished 25.7-second vertical ad is ready,
  complete with captions, overlays, voice, and music."
- Photos: "I've got the product details and eight usable photos" (the list says 3). Scene 1:
  "its cobalt-blue finish" (6 colours).
- Questions asked: none.
- What broke: nothing.
- Cost: $1.4816; without produce $0.9670. 67 model calls, 0 failed.

### 08 · Cleaning · Molly's Suds Toilet Bowl Cleaner

- Session: `c7feaf30-398c-4367-b3ad-fa8663a117f6`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/c7feaf30-398c-4367-b3ad-fa8663a117f6
- First message: `Make a 15-second ad for this product: https://mollyssuds.com/products/toilet-bowl-cleaner`
- Result: **finished ad**, four scenes. Chat: "Your finished ad is ready—14.4 seconds, with
  captions, overlays, and background music."
- Photos: "I've got three usable product photos" (the list says 2).
- Questions asked and answers:
  1. (Unexpected) "The script is about 16.1 seconds. Would you like me to shorten it to fit 15
     seconds, or keep it slightly longer?" → Answered "Shorten it to fit 15 seconds."
- Silent clips asked for 3.5 s and 4.5 s (the new rule); both usable.
- What broke: nothing.
- Cost: $1.0961; without produce $0.6808. 55 model calls, 0 failed.

### 09 · Food · Momofuku Chili Crunch Sauce

- Session: `d14a8fe7-070d-44b4-ba33-205d12adc5e0`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/d14a8fe7-070d-44b4-ba33-205d12adc5e0
- First message: `Make a 6-second ad for this product: https://shop.momofuku.com/products/chili-crunch-sauce`
- Result: **finished ad**, two scenes. Chat: "Your finished ad is ready—5.34 seconds, with
  captions, product overlays, and background music."
- Photos: "four usable photos" (the list says 3). Both starting pictures used photo 4.
- The shortening step was **not** reached: the plan already fit ("Everything checks out and
  fits the target"). One `plan_ad` call, no shortening. Lines as spoken: "Chili Crunch Sauce.
  Smooth, savory, squeezable." and "Drizzle over a bowl, $10."
- Questions asked: none.
- What broke: message #15's text ends with "[Attached 1 picture]" after its sentence, with no
  attachment on that message.
- Cost: $0.6051; without produce $0.3431. 36 model calls, 0 failed.

### 10 · Pet · maxbone Enrichment Tether Toy

- Session: `cff47d8c-ddcf-4191-b7e0-5fd82f3c7395`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/cff47d8c-ddcf-4191-b7e0-5fd82f3c7395
- First message: `Make an ad for this product: https://www.maxbone.com/products/maxbone-tether-toy`
- Result: **finished ad**, six scenes. Chat: "Your finished 21.9-second ad is ready, complete
  with captions, product overlays, voiceover, and background music."
- Photos: "four usable photos" (the list says 3).
- Questions asked and answers:
  1. "Is the Enrichment Tether Toy currently in stock or out of stock? The page shows
     conflicting availability information." (the hidden "out of stock" text, as predicted: it
     asked rather than misread it) → Answered "It's in stock." (the shop's own product data
     says available).
- What broke:
  - 15 of 20 `make_starting_picture` calls failed: 4 on OpenAI's image rate limit (429), and
    **11 refused by OpenAI's safety system** ("Your request was rejected by the safety
    system"). Every refused one was a picture of the presenter ("using the woman from the first
    image…") for scenes 1, 4 and 6; the pictures of the toy and the dog went through. They
    finished after retries: "Scene 1's picture failed again unexpectedly. I'll make one more
    attempt with a simpler product-holding setup." / "Scene 6's picture failed again, so I
    can't finish that scene yet." / "Scene 6's picture is finally ready using photo 2."
  - The chat never said why ("failed again unexpectedly"): the safety refusal isn't passed on.
- Cost: $2.2021; without produce $1.1063. 113 model calls, 15 failed.

### 11 · A smell · Beardbrand Fox Hunt Men's Cologne

- Session: `6c5ab725-43bf-4869-b5d2-03be3997e425`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/6c5ab725-43bf-4869-b5d2-03be3997e425
- First message: `Make an ad for this product: https://www.beardbrand.com/products/fox-hunt-mens-cologne`
- Result: **finished ad**, seven scenes. Chat: "Your finished **32-second vertical ad** is
  ready, with captions, overlays, voiceover, and background music." (no length was asked for)
- Photos: "four usable product photos" (the list says 3).
- Questions asked and answers:
  1. (Unexpected) "The page has conflicting availability details. Is Fox Hunt Men's Cologne
     currently **in stock or sold out**?" → Answered "It's in stock." (the shop's own product
     data says available).
- What broke:
  - 5 `make_starting_picture` calls failed on OpenAI's image rate limit (429). Scene 6's
    picture gave up: "Scene 6's image couldn't be created because the image service hit a
    temporary rate limit after several retries." The producer retried it later; it finished.
  - Scene 1's line was remade: "Scene 1 merged the brand and product names, so I'll remake that
    line for clearer pronunciation."
  - Message #34's text is "[Attached 1 picture] Scene 6's clip is now underway."
- Cost: $1.8403; without produce $1.1928. 84 model calls, 5 failed.

### 12 · See-through · Smartish Gripmunk Slim Case for iPhone 17e

- Session: `039f4b2f-4bc0-4610-bbb1-1279d33f3be5`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/039f4b2f-4bc0-4610-bbb1-1279d33f3be5
- First message: `Make an ad for this product: https://smartish.com/products/gripmunk-clear-slim-case-for-iphone-17e`
- Result: **finished ad**, six scenes. Chat: "Your 23-second vertical ad is finished with
  captions, overlays, and background music."
- Photos: "I've kept all 10 product photos" (the list says 9).
- Questions asked and answers:
  1. "The page conflicts between iPhone 17e and iPhone 16e. Which model should the ad name?"
     (the title/name mismatch the list noted) → Answered "iPhone 17e."
  2. (Unexpected) "The system wouldn't remake the unchanged line. May I change scene 6 to 'This
     iPhone 17e case is fourteen ninety-nine' to avoid the mispronunciation?" (asked again in
     #19, #23, #26 and #27 while waiting) → Answered "Yes."
  3. (Unexpected) "To complete the ad, should I use the closing line with 'Gripmunk' pronounced
     like 'Gripmonk,' or stop here?" → Answered "Use the closing line as it is."
- What broke:
  - The transcript caught a mispronunciation: "Scene 6 mispronounced 'Gripmunk' as 'Gripmonk,'
    so I'll remake that spoken line." The remake was refused: "The system wouldn't remake the
    unchanged line."
  - The approved new wording was refused, as in #23 and #01 (first tries): "That edit wasn't
    accepted because the original checks had already passed." then "The system won't apply the
    approved rewrite because the original line already passed its checks. I can't safely remake
    scene 6 with different wording using the available tools."
  - So **the finished ad's closing line says "Gripmonk"** (as the transcript heard it).
- Cost: $1.7404; without produce $1.0332. 74 model calls, 0 failed.

### 13 · Tiny · Gorjana Melrose Diagonal Studs

- Session: `6b9826af-0040-4e7a-835c-90c168411541`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/6b9826af-0040-4e7a-835c-90c168411541
- First message: `Make an ad for this product: https://www.gorjana.com/products/melrose-diagonal-studs`
- Result: **finished ad**, four scenes. Chat: "Your finished 11.2-second ad is ready, with
  captions, overlays, voiceover, and background music." (no length was asked for)
- Photos: "four usable product photos" (the list says 3).
- Questions asked: none.
- What broke: nothing.
- Cost: $0.9737; without produce $0.5958. 52 model calls, 0 failed.

### 14 · Too big to hold · Branch Swivel Chair

- Session: `a63ac7d8-0f4a-4b25-923c-5f628f220ee1`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/a63ac7d8-0f4a-4b25-923c-5f628f220ee1
- First message: `Make an ad for this product: https://www.branchfurniture.com/products/swivel-chair`
- Result: **finished ad**, six scenes. Chat: "Your finished 32-second vertical ad is ready, with
  captions, overlays, and background music." (no length was asked for)
- Photos: "two usable photos" (the list says 2, one only 240 px).
- Questions asked: none.
- What broke:
  - 2 `make_starting_picture` calls failed on OpenAI's image rate limit (429); retried and
    finished, not mentioned in the chat.
  - Message #23's whole text is "[Attached 1 picture]".
- Cost: $1.5943; without produce $1.0342. 71 model calls, 2 failed.

### 15 · A kit · Nécessaire The Body Ritual Kit

- Session: `cff029cb-92af-42ce-89ae-45f914da57df`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/cff029cb-92af-42ce-89ae-45f914da57df
- First message: `Make an ad for this product: https://necessaire.com/products/the-body-ritual-kit`
- Result: **finished ad**, seven scenes. Chat: "Your finished 28-second vertical ad is ready,
  with captions, product overlays, voiceover, and background music."
- Photos: "four usable photos" (the list says 2). Scene 5: "the clearest fragrance-free Body
  Lotion photo"; scene 6: "all three containers clearly shown"; scene 2: "the boxed kit and
  all three products".
- Questions asked and answers:
  1. (Unexpected) "Should the ad present The Body Ritual Kit as **available now**, or invite
     viewers to **join the waitlist**? The page gives conflicting availability information."
     → Answered "It's available now." (the shop's own product data says available).
- What broke:
  - **The producer stopped by itself** after "The presenter and voice are ready." (message #9),
    with nothing failed and no question asked: `producer_running` false, no step running.
  - "please try again" was misread as a request for a new presenter: "Of course—I'll create a
    new presenter and matching voice." then "I couldn't generate another—the presenter tool
    only allows one creation for this ad. The existing presenter and voice remain available."
    It stopped again.
  - Beyond the "please try again" rule, I sent one more message: "Please continue making the ad
    with the existing presenter and voice." It then carried on and finished.
  - 5 `make_starting_picture` calls failed on OpenAI's image rate limit (429). Scene 2's gave
    up once ("even after three attempts"); the producer retried it later and it finished.
- Cost: $1.8527; without produce $1.1251. 88 model calls, 5 failed.

### 16 · Baby · Mushie Space Teething Ring

- Session: `b7535102-4f11-47fc-8b00-fe0b436f9331`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/b7535102-4f11-47fc-8b00-fe0b436f9331
- First message: `Make an ad for this product: https://mushie.com/products/space-teething-ring`
- Result: **finished ad**, five scenes. Chat: "Your finished 28-second ad is ready, with
  captions, overlays, voiceover, and background music."
- Photos: "I kept all four product photos" (the list says 3).
- Chat on the voice: "The wording matches, with 'Mushie' pronounced 'Mooshee.'" (accepted, not
  remade).
- Questions asked: none.
- What broke: nothing.
- Cost: $1.3089; without produce $0.9155. 57 model calls, 0 failed.

### 17 · Person suits buyer · Brickell Beard Oil

- Session: `43c0d0dd-b1c1-4dd5-b0f6-48eb87dc9f71`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/43c0d0dd-b1c1-4dd5-b0f6-48eb87dc9f71
- First message: `Make an ad for this product: https://brickellmensproducts.com/products/beard-oil`
- Result: **finished ad**, four scenes. Chat: "Your finished **18-second vertical ad** is ready,
  with captions, product overlays, voiceover, and background music."
- Photos: "four usable product photos" (the list says 3).
- Questions asked and answers:
  1. (Unexpected) "Which offer should the ad feature: the **$25 one-time price** or the
     **$22.50 subscription price**?" → Answered "The $25 one-time price."
- What broke: nothing.
- Cost: $1.1125; without produce $0.6941. 55 model calls, 0 failed.

### 18 · Reviews claim more · Starface Hydro-Stars + Big Yellow

- Session: `59716b2e-3dec-4e49-abb0-972e0a5a8086`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/59716b2e-3dec-4e49-abb0-972e0a5a8086
- First message: `Make an ad for this product: https://starface.world/products/hydro-stars-big-yellow`
- Result: **failed at planning.** No plan, no scenes.
- Photos: "three usable product photos" (the list says 1).
- Questions asked: none.
- What broke:
  - All 5 `plan_ad` calls (3 at first, 2 after "please try again") were refused by the app's
    own check: `UnusableReply: … Value error, No scene where the person talks to camera says
    "hydro-stars® + big yellow": at least one must.` The check
    (`backend/jobs/planning.py`, `_name_said_on_camera`) needs the product's name exactly,
    letter for letter apart from case and spaces, in a talking scene's line; this name has
    "®" and "+" in it.
  - Chat: "The first plan failed because it didn't say the full product name on camera, so I'll
    rebuild it correctly." … "I'm sorry, but the ad planner failed repeatedly because it
    couldn't include the full product name correctly. I can't continue to production without a
    valid plan."
  - After "please try again": "The same planning error returned, so I'll refresh the product
    page into a new job and try from scratch." … "The planner is still failing on the required
    product-name line, even after restarting from the saved page and photos. I can't move into
    production until that tool produces a valid plan."
  - Whether reviews were treated as facts (the reason #18 was picked) can't be seen: no plan was
    accepted.
- Cost: $0.4071; without produce $0.2848. 15 model calls, 5 failed.

### 19 · On sale · Titan Foam Fitness Mat

- Session: `7cfcd96d-ed45-49e5-82bb-10dff6c17ad8`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/7cfcd96d-ed45-49e5-82bb-10dff6c17ad8
- First message: `Make an ad for this product: https://titan.fitness/products/foam-fitness-mat`
- Result: **finished ad**, five scenes. Chat: "Your finished 25.8-second vertical ad is ready,
  complete with captions, overlays, voiceover, and background music."
- Photos: "two usable photos" (the list says 1). Every starting picture used photo 1.
- Price line (scene 5): "It's on sale now for eighty dollars and ninety-seven cents, with free
  shipping." (the sale price, as the list predicted).
- Questions asked: none.
- What broke: nothing.
- Cost: $1.2357; without produce $0.8611. 56 model calls, 0 failed.

### 20 · Several prices · Supergoop Unseen Sunscreen SPF 50

- Session: `6609e69d-f6e8-4143-bf1b-270884cace01`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/6609e69d-f6e8-4143-bf1b-270884cace01
- First message: `Make an ad for this product: https://supergoop.com/products/unseen-sunscreen-spf-50`
- Result: **finished ad**, six scenes. Chat: "Your finished 29-second vertical ad is ready, with
  captions, product overlays, and background music."
- Photos: "six usable product photos" (the list says 5).
- Questions asked and answers:
  1. "Which size or pack should the ad feature, and what is its current purchase price?" (as
     predicted) → Answered "The 1.7 oz at $38." (pre-decided).
- What broke:
  - 2 `make_starting_picture` calls failed on OpenAI's image rate limit (429); retried and
    finished, not mentioned in the chat.
  - Message #26's whole text is "[Attached 1 picture]".
- Cost: $1.6640; without produce $1.0853. 73 model calls, 2 failed.

### 01 · Skincare · Naturium Multi-Active Exosome Serum (3rd try)

- Session: `9ad87e95-8740-41bb-bee6-a62303f3813e`
- Langfuse: https://us.cloud.langfuse.com/project/cmuk6bugf0bvvad0cplzq08su/sessions/9ad87e95-8740-41bb-bee6-a62303f3813e
- First message: `Make an ad for this product: https://naturium.com/products/multi-active-exosome-serum`
- Result: **finished ad**, five scenes. Chat: "Your finished 28.7-second vertical ad is ready,
  with captions, overlays, voiceover, and background music."
- Photos: "two usable photos". Every starting picture used photo 1. Scene 4: "the available
  photos don't show the dispenser or a pea-sized drop, so it uses the clear labeled bottle
  instead."
- Questions asked: none.
- What broke:
  - 1 `plan_ad` call refused by the product-name check (as in #18): "The first draft missed the
    required spoken product name, so I'm revising the plan now." The second plan passed.
  - Messages #20 and #26: whole text "[Attached 1 picture]".
- Cost: $1.4696; without produce $0.9598. 63 model calls, 1 failed.
