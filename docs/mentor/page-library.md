# Design mentor: reading the page & the library

**Design read:** ADRs 0002, 0006, 0007 (replaced), 0009; `docs/scraping-journey.md`;
`docs/scraping-test/`; CONTEXT.md (page and library words); specs #1 ("Where facts come
from"), #210, #273.

**Code read:** `backend/jobs/firecrawl.py`, `page.py`, `page_text.py`, `photos.py`,
`colours.py`, `library.py`, `library_items.py`, `library_sheets.py`, `watcher.py`,
`product_videos.py`, `product_only.py`, `cut_out.py`, `recolour.py`, the page-read parts of
`work.py`, `agents/producer.py` (read_page), `agents/page_panel.py`, and the tests below.

## How it is tested today

- Chat-seam tests with every model faked at the gateway, a real local shop server and saved
  real Firecrawl answers: `test_producer_page.py`, `test_producer_page_media.py`,
  `test_video_watcher.py`, `test_watcher_safety_filter.py`, `test_library_build.py`,
  `test_cut_outs_and_paints.py`, `test_page_panel.py`. ffmpeg real on tiny files.
- Pure-logic tests: `test_page_pieces.py` (match_back, prices, copies), `test_library_contracts.py`.
- The marking script runs in a real Chromium: `test_firecrawl_browser.py`.
- Gap: model quality isn't tested. The fakes decide what the picker, copier and watcher
  answer. The only real-model evidence is the one-off scripts in `docs/scraping-test/`.

## Concepts, ranked by impact on this build

### 1. Grounded extraction: check the price against the page · **must apply now**

- **What it is:** every value a model or a third party pulls out of a document is checked
  against the document before it's used. You already do this for text: `match_back`
  keeps a copied sentence only if it's on the page.
- **Why it matters here:**
  - The price is the one fact that started the project (ADR 0002), and it's the one fact
    that isn't grounded.
  - `page_text.prices` / `record_prices` take the price only from Firecrawl's product record.
    `page_text.without_prices` removes the page's own declared prices.
  - Nothing checks that the record's price is on the page (`work.copy_page_text`).
  - The record can describe another item. In your own test, Fire TV's record was another
    ASIN, and Maybelline's record named another shade
    (`docs/scraping-test/photo-merge-test/results.md`, "Things that are odd").
  - Firecrawl's docs say only "Product information extracted from the page". They don't
    say how it's extracted.
  - The fact check can't catch it. It gets the same record price inside `page_text`, so it
    checks the price against itself.
- **How to apply it:**
  - In `copy_page_text`, look for each record price in the page's visible text
    (`on_the_page`). The record already holds the string to look for:
    `price.formatted` ("$34.00"). Also try the plain amount ("34", "34.00").
  - When the page declares exactly one price (`declared_price`), check that the two agree.
  - A price found nowhere: leave it out of the page text and record a developer notice.
    The planner then asks the owner, as ADR 0002 says to.
  - Test: FakeFirecrawl's record says $39 and the page says $34. Assert that the plan
    handoff has no $39 and that a notice was recorded.
  - What the app checks is your call: this needs your yes.
- **One real post:** Google, [Introducing LangExtract](https://developers.googleblog.com/en/introducing-langextract-a-gemini-powered-information-extraction-library/).
  Every extraction is mapped back to its exact place in the source, so it can be checked.

### 2. Cache freshness (TTL) for facts · **must apply now**

- **What it is:** every cache has a maximum age. A fact like a price needs a short one.
- **Why it matters here:**
  - `firecrawl.read_page` and `firecrawl.product_record` send no `maxAge`, so Firecrawl's
    default applies. That default is **2 days**: it can hand back a copy of the page and the
    record up to 2 days old.
  - So a sale that ended yesterday can still be the ad's price.
  - The marked screenshot is always fresh, because requests with actions skip the cache.
    So the photos and the text/price can come from different days.
  - You already knew about the cache for block pages (`FRESH`, `maxAge: 0`). It just isn't
    used on the first read.
- **How to apply it:**
  - Send `maxAge: 0` (or a few minutes) on the page read and the product record.
  - Firecrawl's docs say a cached result costs the same 1 credit, so this costs only speed.
  - Re-reads don't need Firecrawl's cache: the job already saves Firecrawl's answers
    (`work._firecrawl_answers`), and `pay_once` is set on the model calls.
  - Test: `FakeFirecrawl.requests` records each body. Assert that `maxAge` is in the page and
    product calls.
- **One real post:** Firecrawl docs, [Faster scraping](https://docs.firecrawl.dev/features/fast-scraping).
  It gives the 2-day default, says actions skip the cache, and says "Cached results still
  cost 1 credit".

### 3. Data provenance: a transcript is model output, not the shop's words · **must apply now**

- **What it is:** each fact keeps a tag saying where it came from and how far to trust it.
  Something a model heard isn't the same kind of source as something written on the page.
- **Why it matters here:**
  - `watcher._keep` stores Gemini's transcript as `video.transcript`. `work._video_transcripts`
    hands it to the planner and the fact check.
  - `checks.FACT_CHECK_INSTRUCTIONS` says the transcript "counts as the shop's own words,
    like the page's text".
  - The page text is matched back to the page. The transcript has no check at all.
  - That makes it a third source of facts that ADR 0002 never lists.
  - A misheard number ("50% off" for "15% off") or sung lyrics would pass the fact check.
  - Gallery videos are mostly music, with little speech. That is the condition under which
    a published study of Whisper found invented sentences.
- **How to apply it:**
  - Keep the source on each fact the planner gets: page, record, owner, video (heard).
  - Proposal for your yes: a claim backed only by a transcript, and carrying a number, price
    or promise, goes to the owner as a question, the way an unclear page does.
  - Prompt wording (mine to change): tell the watcher that sung lyrics and background music
    are not "said".
- **One real post:** Koenecke et al., [Careless Whisper: Speech-to-Text Hallucination Harms](https://arxiv.org/abs/2402.08021)
  (FAccT 2024). About 1% of Whisper transcripts held whole phrases not in the audio, more
  often when there was a lot of non-speech. The study is of Whisper, not Gemini.

### 4. Pixel-preserving segmentation instead of generative redraws · **good to know**

- **What it is:**
  - A segmentation model outputs a mask: which pixels are the product.
  - A cut-out made from a mask keeps the photo's own pixels. The shape, letters and print
    can't change, because nothing is redrawn.
  - A generative edit redraws the whole picture, so "unchanged" can only be hoped for and
    then checked.
- **Why it matters here:**
  - Today `product_only._cut` → `cut_out.redraw_with_check` makes a cut-out by asking the
    picture model to redraw the product. `recolour.recolour` does the same for paints.
  - Round 7 (#273): 83 of 118 cut-outs failed ("adds a lower body", "elongated"), and 0 of
    17 paints passed.
  - The checks also failed good edits (#273 cause 6).
  - In the scene agents, the cut-out then feeds a second generative draw: the starting
    picture (`agents/scene_agent/draw_starting_picture.swapped`). Two redraws in a row
    compound their errors.
  - Known: #273's Out of scope says real-pixel cut-outs get built only if letters or angles
    still break after #273.
- **How to apply it (when the #273 real-flow test says so):**
  - SAM 3 runs on fal (already a paid service here) at $0.005 a request. It takes a text
    prompt ("the bottle") and points or boxes, and gives back masks.
  - Cut-out = the photo's pixels inside the mask, on white. For a line-up, pick the mask
    whose position matches the picker's `target`.
  - Paint = shift the colour inside the mask only. Or keep the generative paint and paste
    the original pixels back outside the mask, so "change nothing else" holds by construction.
  - The cut-out check shrinks to "did the mask take the right object".
  - A mask can't draw a hidden part. Keep the helper flow only where a whole product must
    be drawn.
- **One real post:** Meta, [Introducing SAM 3](https://ai.meta.com/blog/segment-anything-model-3/).
  Text-prompted segmentation; it powers Facebook Marketplace's "View in Room" for products.

### 5. Short transactions: no network calls inside a DB transaction · **must apply now**

- **What it is:** a transaction holds its locks until it commits. So inside one you only
  write rows. No model calls, no downloads, no sleeps.
- **Why it matters here:**
  - `watcher._keep` opens `transaction.atomic()`.
  - Inside it, `library.add_to_library` takes `SELECT … FOR UPDATE` on the Job row. That
    lock is held until `_keep` commits.
  - Still inside, `work.save_video_pictures` → `keep_photo(has_face=None)` → `note_face`
    makes one vision call per look frame.
  - Those calls retry with `with_retries` (5 + 20 + 60 + 120 s) when the provider is down,
    so the row can stay locked for minutes.
  - Meanwhile the other videos' watcher threads write `ModelCall` rows. Each one references
    the job (a foreign key), which needs a key-share lock on the Job row, and FOR UPDATE
    blocks that.
  - Any `post_notice` for the job also waits.
  - So "every video watched at once" (ADR 0009) waits in line to record its results, and a
    provider outage freezes the job row.
- **How to apply it:**
  - Do the network work first: pull the frames, then run the face notes.
  - Then use one short atomic block that only adds the library rows, the photos and the watch.
  - Test: make the fake gateway raise if a model call starts inside an atomic block that a
    test didn't open. Run it in the committing (`transaction=True`) chat tests. Any future
    network call inside a transaction then fails CI.
  - This is code structure, so it's mine to fix. No design change.
- **One real post:** GitLab, [Transaction guidelines](https://docs.gitlab.com/development/database/transaction_guidelines/).
  "Avoid doing in a transaction block: external network requests such as HTTP API calls."
  Lock conflicts: [Postgres explicit locking](https://www.postgresql.org/docs/16/explicit-locking.html)
  (FOR UPDATE blocks FOR KEY SHARE).

### 6. Perceptual hashing, and its blind spot for colour · **must apply now**

- **What it is:** a perceptual hash gives similar pictures similar bits. You compare two
  hashes by counting the bits that differ. dHash, the one used here, works on a
  black-and-white copy of the picture, so it ignores colour.
- **Why it matters here:**
  - `photos.dhash` / `photos.Copies` treat the same studio shot in two colours as copies.
  - `test_page_pieces.test_copies_of_one_photo_are_kept_once` pins this: "two shades … kept
    once: accepted".
  - That was accepted before #273. Since #273, colour picks tier 4 ("the photo in the
    clip's colour first") and decides when to paint.
  - `agents/page_panel._take`: an owner who pastes the page with 3 colour shots keeps 1. Their
    chat says "these N photos" and never says why 2 were dropped. That breaks "No silent
    failures".
  - `work.save_video_pictures` drops a look frame in a new colour as a "copy" of a page photo.
  - The threshold is 12 of 256 bits, which is strict, so it also misses crops
    (photo-merge test, "What this does not settle").
- **How to apply it:**
  - Count two pictures as copies only when dHash is within 12 bits **and** their average
    colour is close. Measure colour in Lab, over the non-background pixels, and pick the
    "close" threshold on your test photos.
  - Flip that test's expectation.
  - Say which photos were merged, and as copies of what.
- **One real post:** Neal Krawetz, [Kind of Like That](https://www.hackerfactor.com/blog/index.php?/archives/529-Kind-of-Like-That.html).
  The original dHash write-up: "Convert the image to a grayscale picture", then compare by
  Hamming distance.

### 7. Re-asking with the error (feedback-driven retries) · **good to know**

- **What it is:** when an answer fails a validator or a check, the retry tells the model
  what was wrong. Better still, put the rule into the schema so the model can't break it.
- **Why it matters here:**
  - `watcher._asked`: an answer the schema refuses (say, a 22 s moment) is sent again
    exactly as before. Same input, so the same mistake is likely.
  - `cut_out.redraw_with_check`: when the check says "adds a lower body", the redraw uses
    the same prompt. That's a blind re-roll.
  - `SeenMoment`'s 15 s rule is a Pydantic validator, and the end − start rule can't go into
    Gemini's schema. So Gemini can't hold to it while it writes the answer.
- **How to apply it:**
  - Ask for a moment as `start` + `length`, with `maximum: 15`. Gemini enforces
    `minimum` and `maximum`. Compute `end` in code.
  - On a schema failure, add the validation error to the retry ("moment 3 lasts 22 s; split it").
  - On a failed cut-out, add the check's `problem` to the redraw prompt.
  - Watch the replay key: `redraw_with_check` finds paid tries by their prompt, so a prompt
    that changes per try needs the try number in that key.
- **One real post:** Instructor docs, [Validation and re-asking](https://python.useinstructor.com/concepts/reask_validation/).
  The Pydantic error goes back to the model on the retry. Gemini's enforced keywords:
  [Structured output](https://aistudio.google.com/docs/structured-output).

### 8. Temporal grounding in video · **good to know**

- **What it is:** finding exactly when something happens in a video. Its accuracy is
  limited by how many frames a second the model looks at.
- **Why it matters here:**
  - `settings.WATCH_VIDEO_FRAMES_PER_SECOND = 1.0`. The watcher's seconds are the cut
    (#210 point 4: "no second look at a moment"). `product_videos.cut_pieces` cuts exactly
    those seconds.
  - A squeeze, a pump or a click lasts under a second. At 1 fps, the model may not see it,
    or may place its edges a second off.
  - A piece that starts half a second early opens with a flash of the previous shot, and
    the video model may copy that jump.
- **How to apply it:**
  - Snap each moment's edges to the video's shot changes. PySceneDetect or ffmpeg's scene
    filter finds them for free.
  - Try 2–3 fps for short videos. Cost grows with frames, and a 30 s video is about a cent
    today (ADR 0009).
  - Grade a few pieces before and after. Grading is yours.
- **One real post:** Gemini docs, [Video understanding](https://aistudio.google.com/docs/video-understanding):
  "fast action sequences might lose detail due to the 1 FPS sampling rate". Tool:
  [PySceneDetect](https://www.scenedetect.com/).

### 9. SSRF defence: an egress proxy, or pin the checked address · **good to know**

- **What it is:** SSRF (server-side request forgery) is when a pasted link makes the server
  fetch somewhere only the server can reach. The robust fix checks the address at the
  moment of connecting, not before.
- **Why it matters here:**
  - `page.check_where_it_points` looks the host up, then httpx looks it up again. Its own
    docstring names the gap (DNS rebinding).
  - `product_videos._player_file` checks only the player's `src`. yt-dlp then follows
    redirects and manifests that nothing checks.
  - Inside the Docker network, `web:8000` serves the API without Caddy's lock
    (`deploy/compose.yml`).
  - Today only you get past Caddy's password, so the risk is low. It becomes real when shop
    owners can paste links.
- **How to apply it:**
  - Run Stripe's Smokescreen as a container. Point httpx (`proxy=`) and yt-dlp (its
    `proxy` option) at it.
  - It resolves and connects to the checked address itself, which covers both rebinding
    and redirects.
- **One real post:** Stripe, [Smokescreen](https://github.com/stripe/smokescreen). Stripe's
  egress proxy makes sure every destination "is a publicly routable IP address and not an
  internal IP address".

### 10. Indirect prompt injection from scraped pages · **good to know**

- **What it is:** text on a page you read (a review, alt text, hidden copy) is written to
  read like instructions to your model.
- **Why it matters here:**
  - `photos.handoff` gives the picker every numbered text piece, reviews included.
  - `work.copy_page_text` gives the copier the whole markdown.
  - The copied text then reaches the planner, the fact check and the watcher.
  - `match_back` blocks made-up sentences. It doesn't block a sentence that really is on
    the page and is an instruction.
  - The damage is small: there's no web search and no message outside (ADR 0002), and every
    answer is structured. So the worst case is a wrong ad or a wrong photo pick.
- **How to apply it:**
  - Wrap page text in clear delimiters, and tell each model that it is data, not
    instructions ("spotlighting").
  - Leave review sections out of the picker's text pieces.
  - Add one planted-instruction page to the real-model eval set (see Also seen here, 2).
- **One real post:** Microsoft MSRC, [How Microsoft defends against indirect prompt injection attacks](https://www.microsoft.com/en-us/msrc/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks)
  (spotlighting, and limiting what an injection can reach).

## Also seen here

1. **Stopping conditions:** each step has its own cap (`watcher._past_the_filter` stops at
   16 probes, `watcher._asked` re-asks once). One page read has no cap as a whole: up to 50
   photos with labelling and face calls, plus every video × (3 watches + 16 probes + parts).
2. **Agent evals on a real model:** `photos.PICK_INSTRUCTIONS` gained labels, the colour
   list, charts, hidden pictures and videos after the screen test. Since then only fakes
   and frozen-text tests cover it. Two runs of the same call differed by 32 photos (the
   text test, as the ref-photo test notes). The saved `jobs/<id>/firecrawl/*.json` answers
   are a ready replay set.
3. **Calibrating an LLM judge:** the scraping journey found the judge wrong on 27 of 40
   "wrongly kept" photos. `cut_out.check_cut_out` failed good edits in round 7 (#273
   cause 6), and nothing has measured it against your eye since.
4. **A stable prompt prefix:** `product_only._helpers_for` sends the photo first and the
   library sheets after it. The sheets are the same for every helper ask in an ad, so
   sheets first and photo last would let the cache hit.
5. **Independent evidence for checks:** `product_only._cut` gives the helper ask's
   helpers to both the picture model and `cut_out.check_cut_out` (#273 point 6). A wrong
   helper is drawn in, then passes as "drawn as the other photos show it".
6. **Precision vs. recall:** `photos.PICK_INSTRUCTIONS` ("a missing photo costs little, a
   wrong one puts another product in the advert") and the journey's "would this hurt the
   ad?" triage choose precision on purpose.
7. **An explicit state machine:** a video's state is spread over `file`, `watched`,
   `thrown_out` and `transcript`, and `watcher.watch_videos` reads them as a state. A
   cut-out's state is keys in a JSON dict (`covered`, `unusable`) in `product_only._cut`.
8. **Leases:** `library_items.add_kept_photos` holds the advisory LIBRARY_LOCK through each
   labelling call. `recolour.recolour` holds RECOLOUR_LOCK through a paid edit.
9. **An app-side rate limiter:** `firecrawl.scrape` sleeps inside the worker on a 429, up
   to 6 waits of 120 s. `watcher.watch_videos` and `product_videos.save_videos` start one
   thread per video, with no cap. `work.copy_page_text` sends the whole markdown, uncapped:
   5 big Amazon pages hit the 200K tokens-a-minute limit in the copy test.
10. **Atomic counters:** `work.save_video_pictures`, `product_videos.save_videos` and
    `keep_owner_video` take max(position) + 1 with no lock, so a race ends in an
    IntegrityError from the unique constraint. `library.add_to_library` does it right with
    `select_for_update`.
