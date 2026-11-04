# Design mentor: the page AIs (junk check, labelling call, face note, photo picker)

**Design read:** ADRs 0002, 0006 and 0012; `docs/scraping-journey.md`; `docs/scraping-test/` (README,
ref-photo-test, screen-test); CONTEXT.md (Page text, Product photo, Hidden picture, Page panel,
Owner-pasted page, Labelling call, Library); spec #1, spec #210, and #273 point 2 (the colour list).
I read `docs/mentor/page-library.md` first and don't repeat it. **Code read** at `fd346df` (paths
under `backend/`): `jobs/work.py` (check_page, read_and_check_page, copy_and_pick, pick_photos and
its safety-filter helpers, save_photos, keep_photo, note_face, save_video_pictures), `jobs/photos.py`,
`jobs/picker_grids.py`, `jobs/library_items.py`, `jobs/colours.py`, `jobs/face_swap.py` (where
`has_face` is read), `agents/producer.py:ReadPage.run`, `agents/page_panel.py:_take`,
`gateway/gateway.py:call_model`, `gateway/openai_adapter.py`, `gateway/catalog.py`,
`jobs/notices.py`, `broll-test/face_notes.py`, and the tests listed below.

Teaching only: nothing in the app changed. Applying any concept is a design change and needs
Julian's yes. Costs are estimates from catalog prices times calls in the code, not a bill.

---

## Systems in this group

| | check_page (junk check) | pick_photos (photo picker) | note_face (face note) | label_photo (labelling call) |
|---|---|---|---|---|
| **What it is asked** | `work.CHECK_INSTRUCTIONS`: "readable" if the text names one product and says what it is. "unreadable" if it is empty, a loading screen, cookie wall, bot check, error page, or a page of many products. One sentence for the owner. Handoff `work.PageCheckHandoff`: product_url, page_url, page text cut to 200,000 chars (`page.for_model`), photo_count. | `photos.PICK_INSTRUCTIONS`, about 8,000 chars and about 10 jobs: pick the product, its gallery and other photos, text sections, faces, page_blocked, record photos, gallery videos, a colour list, and a label for each picture. Handoff `photos.handoff`: record text, page title, numbered pictures, every text piece, plus lines for hidden pictures, official photos and video players. Images `work._picker_images`: up to 14 screenshot parts, then grids (`picker_grids.beside_the_screenshot`), 50 at most. | `photos.NOTE_FACE_INSTRUCTIONS` (233 chars): a face "you could recognise". A body, hand, lips, or a face turned away, cut off or too small doesn't count. Handoff `photos.FaceNoteHandoff`: the photo's link (or file key). One image (`work.note_face`). | `photos.LABEL_INSTRUCTIONS`, which uses the picker's own `LABEL_FIELDS`. Handoff `photos.LabelPhotoHandoff`: source, shows, product text up to 4,000 chars. One image (`library_items.label_photo`). |
| **Which model, and why** | gpt-5-mini (`catalog.MODEL_FOR_PURPOSE`). No comment or ADR gives a reason. | gpt-6.1-sol. `catalog.py` gives the reason as "92% of its photos were the right product's (ref-photo-test)". That 92% was measured on gpt-5.6-sol. The model was moved "with no comparison test" (`tests/test_frozen_rules.py:MOVED_TO_6_1_SOL`). | gpt-6.1-sol: "from the same model, as the picker gives it" (`catalog.py`). Also moved with no comparison test. | gpt-6.1-sol: "from the same model, as the picker gives them (spec #210 point 1)" (`catalog.py`). Also moved with no comparison test. |
| **Input → output** | Text in → `work.PageCheck`, a Judgement: a decision (readable or unreadable) and a reason. The reason is shown word for word in the owner's notice (`producer.ReadPage.run`). | Pictures and text in → `photos.PickedPhotos`: 12 fields. `pictures` holds one `PickedPicture` per pick (kind, side, alone, colour, target, label, copy_group). | One photo in → `photos.FaceNote`, `{has_face: bool}`, with no reason. | One photo plus text in → `photos.photo_label_for(job)`, a `PhotoLabel`: kind, side and alone as enums, colour held to the job's list or none (`colours.answer`), target, label. |
| **What checks the output** | Only the schema. On "unreadable", the page is read once more, fresh (`work.read_and_check_page`), then the owner gets the page panel. The same call judges an owner's paste (`page_panel._take`). | The schema. `photos.picked_links` drops numbers it doesn't know. `PickedPicture.label_of` checks each picture: its colour must be on the picker's own list, and only a line-up has a target. `page_blocked` switches to the page's declared photos. `producer._doubtful` shows the photos to the owner when they are declared ones or fewer than 2. | Only the schema. Its answer decides whether `face_swap.for_clip` swaps the face, through `face_swap._photo_with_a_face`. | The schema and its validators (`PhotoLabel._said`, `_a_target_for_a_line_up_only`). Inside a producer tool, an answer that breaks a rule is asked once more, told why (`gateway.call_model`, `asking_again`). Its fields then decide a paid cut-out (`library_items.needs_product_only`) and whether to paint. |
| **Eval** | None. The decision map says "Nothing" (`docs/adr/0012-legacy/decision-map.md`). Two answers were checked by hand in the first run (`docs/runs/first-run-findings/21-…`, `22-…`). | One-off only: `docs/scraping-test/ref-photo-test` (34 pages, gpt-5.6-sol, an older prompt, official photos at low detail) and screen-test (15 pages). The 126-page test is in `runs/`, outside the repo. Nothing on gpt-6.1-sol or today's prompt. | None of its own. `broll-test/face_notes.py` ran the picker's `face_images` instead: 66 photos, 15 faces, 13 of them on one page (`docs/runs/broll-tests/face-notes.json`). | None. The decision map says "not measured". |
| **How it fails** | An outage, an unusable answer or a filter refusal is raised out of `ReadPage.run` as a plain tool failure (`agents/loop.py:why_it_failed`). No notice Kind is posted. On a paste it posts PASTE_NOT_CHECKED (developer) and PASTE_REFUSED (owner: "Send it again"). | `work.pick_photos` falls back to the page's declared photos, and the notice is posted as PHOTO_PICKER (developer). A safety-filter refusal goes to `work._picked_past_the_safety_filter`, which leaves grids out, then `_past_refused_tiles`, which halves them. A blocked page posts PAGE_BLOCKED as PHOTO_PICKER, plus the doubtful stop. | Returns True, so the photo counts as having a face, and posts FACE_NOT_NOTED (developer) (`work.note_face`). | Returns None. Posts PHOTO_NOT_LABELLED (developer), and OWNER_FILE_UNUSABLE (owner) for the owner's own photos. The photo stays out of the library and is tried again the next time photos are added (`library_items.label_photo`). |
| **Rough cost per ad (estimate)** | About $0.001–0.003 a call; the first run recorded $0.0009 and $0.0015. One call per ad, 2 when the first read is junk, plus 1 per paste. **Under 1 cent per ad.** | About $0.08–0.20 a call: 25–55K tokens in at $2/M, 3–10K out at $10/M. One call per ad (`pay_once` covers a re-read). On the safety-filter path it can take up to 36 full calls, **about $4–7** (concept 8). | About $0.006–0.01 a photo: up to 2,500 image tokens. Called for every photo the picker didn't note: picked R photos, look frames (`work.save_video_pictures`), owner photos (`producer.UsePhotos.run`), pasted photos, and every declared photo on a fallback (up to 50). **About $0.02–0.10 per ad, up to about $0.50.** | About $0.01–0.016 a photo. Called by `add_kept_photos` for photos without the picker's fields and for owner and pasted photos. Also called from `product_only._cut`, `made_pictures` and `owner_parts`. **About $0.02–0.15 per ad.** |

---

## How it is tested today

- **Chat tests.** Each test drives the chat with `say(...)`. Every model is faked at the gateway
  (`gateway/fake.py:FakeModel`), Firecrawl is faked (`conftest.FakeFirecrawl`), and photos come
  from a real local server.
  - `test_producer_page.py` and `test_unreadable_page.py`: a junk page is read again fresh, then
    the page panel opens.
  - `test_page_panel.py`: a pasted page judged by check_page.
  - `test_producer_page_media.py`: the grids, the 50-image cap, and the safety-filter halving
    cases.
  - `test_picker_labels.py`: the fixed fields, copy groups, and broken fields going to the
    labelling call.
  - `test_producer_face_notes.py`: the picker's notes, a call of its own for photos the picker
    never saw, a failure counting as a face, and paying once.
  - `test_library_build.py`: the labelling call is given the rules, and a broken answer is
    refused and asked again later.
  - `test_producer_doubtful_photos.py`: a blocked page or declared photos stop before planning.
- **Gateway tests.** `test_gateway.py` tests how `call_model` works, using `check_page` as its
  sample purpose: a bad handoff, a re-ask once, timeouts.
- **Pinned wording.** `test_frozen_rules.py` pins:
  - the picker's "shown beside" block, word for word;
  - no shop names in the picker's rules;
  - the chart and line-up kinds;
  - the move to gpt-6.1-sol.
- **What the tests cannot see:**
  - Any real answer. The fake returns what the test scripted. No test can fail because a model
    picks a wrong photo, misses a face, labels the wrong kind, or calls a real page junk.
  - Whether the prompt is coherent. A pin stops the wording from drifting; it doesn't grade it.
  - Real cost and latency. The fake bills fixed token counts.
  - No eval in `backend/evals/` covers any of the four.

---

## Concepts, ranked by impact on this build

### 1. Eval–production skew (training–serving skew) · **must apply now**

- **What it is:** an eval has to measure the thing that ships. If the eval builds the call its own
  way, or the shipped call changed after it, the number describes some other system. In ML this
  is called training–serving skew.
- **Why it matters here:**
  - `catalog.MODEL_FOR_PURPOSE` justifies the picker with "92% … ref-photo-test". That test ran
    gpt-5.6-sol, with up to 6 official photos at low detail, and no hidden pictures, no videos, no
    labels and no colour list (`ref-photo-test/results.md`).
  - What ships today (`work.pick_photos`, `work._picker_images`):
    - gpt-6.1-sol;
    - up to 24 official photos, in grids at high detail;
    - up to 40 hidden pictures and 20 video frames;
    - a label for every picture, and a colour list.

    The model moved "with no comparison test" (`test_frozen_rules.MOVED_TO_6_1_SOL`).
  - The comment on `photos.NOTE_FACE_INSTRUCTIONS` says the call is "worded as the picker's note
    (tested on the 8 test products' 74 photos: backend/broll-test/face_notes.py)". The words do
    match the picker's face line. But that script calls purpose `pick_photos` with
    `PICK_INSTRUCTIONS`, so what was tested is the note made inside the pick call, with the whole
    page in view. The one-photo call (`work.note_face`, answering `photos.FaceNote`) has never
    run on a real model in any test or eval. Only 66 of the 74 photos opened.
  - `face_notes.picker_images` builds the picker's pictures again, its own way: official photos
    first, then the parts, at most 6 official photos. Production sends the parts first, then the
    grids.
  - check_page and label_photo have no eval at all.
- **How to apply it in this build:**
  - Write one eval script per system, and have it call the production function, never a copy of
    the call:
    - `work.pick_photos(job, PageRead(...))`, with the PageRead built from a job's saved Firecrawl
      answers (`jobs/<id>/firecrawl/*.json` and `screenshot.png`);
    - `work.note_face`, `library_items.label_photo` and `work.check_page`.
  - Save each run's answers under the prompt's hash and the model's name. #11 (regression evals)
    then has a baseline to compare against.
  - The eval saves the answers and a contact sheet. Julian marks the pictures.
  - Cost (estimate, Julian's call):
    - the picker is about 10–20 cents a page, so the 35 ref-photo pages are about $4–7;
    - a face note or a label is about 1 cent a photo;
    - check_page on the 126 pages is about $0.20–0.40.
  - Once a real number exists, fix the wrong comment in `photos.py`.
- **One real post:** Google, [Rules of Machine Learning](https://developers.google.com/machine-learning/guides/rules-of-ml).
  Rule #32: "Re-use code between your training pipeline and your serving pipeline whenever
  possible." Rule #37: "Measure Training/Serving Skew."

### 2. Defense in depth for a safety label: an independent face detector · **must apply now**

- **What it is:** when a miss is the costly error, don't trust one detector. Add a second,
  independent one, and count a hit from either. A small face detector runs on our own machine for
  free, gives the same answer every time, and gives each face's box and size.
- **Why it matters here:**
  - `has_face` now decides whether a stranger's face is swapped for the presenter's
    (`face_swap._photo_with_a_face` → `face_swap.for_clip`). A wrong "no" sends the photo whole,
    stranger's real face and all, with no notice. #345's red warning only fires when a swap
    *fails*.
  - Spec #1 decided the note for a smaller job: "B-roll uses the note to prefer photos without a
    stranger's face". The cost of an error went up; the evidence stayed the same.
  - The only evidence is the picker's own note, made inside the pick call: 15 faces in 66
    photos, 13 of them on one makeup page (`docs/runs/broll-tests/face-notes.json`). That can't
    tell how often faces are missed on other pages.
  - `work.note_face` itself has never been measured.
  - A *failed* call is already on the safe side (`note_face` returns True). A wrong "no" isn't
    covered.
- **How to apply it in this build:**
  - Run a face detector (MediaPipe Face Detector, full-range model) on every kept photo, in
    `work.keep_photo`, and on the photos the picker noted, in `work.save_photos`.
  - has_face = the model says yes, OR the detector finds a face over N px. Julian's call: N, and
    whether a face turned away or cut off should count now that the note decides a swap.
  - Record each disagreement as a developer notice of a new Kind, so Julian can grade them.
  - Measure each detector's misses on a set Julian marks (start from
    `docs/runs/broll-tests/face-notes-sheet.jpg`), with a confidence interval (#27).
  - It is a new dependency: vet it first. Each extra swap costs a picture edit and its check
    (Julian's call: money).
- **One real post:** Google, [MediaPipe Face Detector](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector).
  "Detect faces in an image or video". It gives a box and six landmarks per face, filtered by
  score.

### 3. Open-set classification: a closed label set needs an "other" value · **good to know**

- **What it is:** a model made to pick from a fixed list still labels an input that isn't on the
  list. It picks the least-wrong label, and says it with confidence. An "other" value lets the
  model say so, and lets code handle it.
- **Why it matters here:**
  - `photos.photo_label_for` → `colours.answer` holds the labelling call's colour to the picker's
    list or `none`. That was decided in #273 point 2.
  - An owner photo in a colour the list lacks, or any photo when the picker wrote the list short,
    can only get a wrong listed colour or `none`.
  - `none` means "the product's body isn't in the picture". `agents/scene_agent/draw_starting_picture.py:swapped`
    returns an item whose colour is `none` without painting it. So a product photo in the wrong
    colour can be sent unpainted.
  - `PickedPicture.label_of` refuses a colour outside the picker's own list. That photo then goes
    to label_photo, which is forced back onto the list.
  - The kind enum has no "not this product". Declared photos "may include other products' photos"
    (`work.DECLARED_INSTEAD`), yet label_photo labels each one as the product
    (`library_items.THE_PRODUCT`). The only guard is the owner's go-ahead (`producer._doubtful`).
  - Known: `docs/limitations.md`, "A colour mark doesn't stop a wrong-colour picture."
- **How to apply it in this build** (Julian's call: it changes what the model decides):
  - Add `other` to the colour enum. Code treats it as "not in the ad's colour": paint it from a
    reference, or leave it out, never "already right". Count how often `other` comes up; if it
    comes up often, the list is too short.
  - For photos the picker never saw, add a field `shows_the_product: yes | no | unsure`. A "no"
    stays out of the library, with a developer notice.
- **One real post:** Hendrycks & Gimpel, [A Baseline for Detecting Misclassified and Out-of-Distribution Examples](https://arxiv.org/abs/1610.02136).
  How to tell when a classifier is answering confidently about an input that is outside its
  classes.

### 4. Inter-annotator agreement between the two labellers (Cohen's kappa) · **good to know**

- **What it is:** when two labellers fill in the same fields, measure how often they agree, field
  by field, corrected for agreement by chance. That corrected score is Cohen's kappa. Two models
  count as two labellers.
- **Why it matters here:**
  - The same `PictureLabel` fields come from two places:
    - the picker, which sees the whole page (`work._labels_and_copies`);
    - the labelling call, which sees one photo and the page text (`library_items.label_photo`).
  - Code treats the two as one. `library_items.needs_product_only` reads `kind` and `alone` to
    decide a paid cut-out, and `colour` decides painting.
  - If the labelling call says `alone: no` where the picker would say `yes`, the same photo gets
    a cut-out on one path and not on the other.
  - The labelling call also labels a cut-out from scratch (`product_only._cut`). An item and its
    cut-out can then disagree on colour or side.
  - Nothing measures either.
- **How to apply it in this build:**
  - The free part: the picker's fields are already stored on `ProductPhoto` (kind, side, alone,
    colour, target).
  - The paid part (Julian's call, about 1 cent a photo): run `label_photo` on 100 photos from past
    jobs that the picker labelled. Report the raw agreement and kappa for each field. Report raw
    agreement too, because kappa looks low on rare values like `line-up`.
  - A field that agrees poorly gets a prompt fix in `LABEL_FIELDS`, then a re-run.
- **One real post:** Arize, [How to measure human-LLM judge alignment](https://arize.com/blog/measuring-human-llm-judge-alignment/).
  It recommends Cohen's kappa for two labellers with categorical labels, and warns that "severe
  class imbalance can produce surprisingly low kappa despite high raw agreement".

### 5. Contradictory instructions in a prompt built up over time · **good to know**

- **What it is:** a prompt that grew by adding new sections can end up saying two opposite
  things. A reasoning model then tries to make both true, which costs tokens and can change its
  answers.
- **Why it matters here** (`photos.PICK_INSTRUCTIONS`):
  - One section says "The official photos are a guide only: answer with I numbers from the page,
    never R numbers." A later one says "The official photos R1, R2, ... are no longer a guide only:
    … put its R number in record_images."
  - "Last, pictures: one entry for every picture you picked, in gallery_images, more_images or
    record_images" comes before `record_images` is explained.
  - "face_images are the I numbers", so a picked R photo gets no face note from the picker. Each
    one costs a `note_face` call (`photos.picked_links` sets its has_face to None).
  - The schema asks for `product`, `product_sections`, `notes` and `video_notes`, and no code
    reads them. They are kept because the picker was tested with them (`photos.PickedPhotos`
    comments), and they cost output tokens.
  - In `NOTE_FACE_INSTRUCTIONS`, "you could recognise" is vague for its new job (concept 2).
- **How to apply it in this build:**
  - Rewrite the picker prompt as one text: each rule said once, in the order the answer is
    written. The wording is mine to change, but this changes a tested prompt, so run concept 1's
    eval before and after.
  - Then `test_the_photo_picker_is_told_of_the_pictures_beside_the_screenshot_as_tested` pins the
    new text.
- **One real post:** OpenAI, [GPT-5 prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide).
  Contradictory prompts "can be more damaging to GPT-5 than to other models": it "expends
  reasoning tokens searching for a way to reconcile the contradictions".

### 6. Reasoning effort per task, and a cap on output · **good to know**

- **What it is:** a reasoning model takes an effort setting. Low effort is faster and uses fewer
  tokens, and reasoning tokens are billed as output. The default differs from model to model.
- **Why it matters here:**
  - `openai_adapter.OpenAIProvider.complete` sends no `reasoning` and no `max_output_tokens`. Every
    call in this group runs at its model's default.
  - A model swap quietly changes that default too. It did for three of these four on Oct 29.
  - note_face answers one yes or no, and check_page one of two words. Both still pay for default
    reasoning, billed as output: $10/M on sol, $2/M on mini.
  - Gemini already has this setting: `catalog.THINKING_LEVEL_FOR_PURPOSE` ("thinking is billed as
    output").
- **How to apply it in this build** (Julian's call: money, and what the models decide):
  - Add `REASONING_EFFORT_FOR_PURPOSE` next to it, and pass `reasoning={"effort": …}` in
    `complete`. Check which values gpt-6.1-sol supports on its model page.
  - Try low effort on note_face and check_page, and the default on the picker. Keep a setting only
    if concept 1's eval still holds.
  - Record `output_tokens_details.reasoning_tokens` on `ModelCall`, so the cost of thinking shows.
- **One real post:** OpenAI, [Reasoning models guide](https://developers.openai.com/api/docs/guides/reasoning).
  "Lower effort favors speed and lower token usage." Reasoning tokens are "billed as" output
  tokens. Defaults are "model-dependent rather than universal".

### 7. Image tokenization: know what the vision model sees · **good to know**

- **What it is:** a vision API shrinks each image to its own limits before the model sees it, and
  charges for what's left. "High detail at 2,048 px" means different things on different models.
- **Why it matters here:**
  - The `gateway.MAX_IMAGE_SIDE` comment says "the model looks at no more than this anyway".
    OpenAI's guide says that for the gpt-5.6 family, high detail "fits within 2048 × 2048 pixels
    and 2,500 patches" (32 px patches). A 2,048 × 2,048 photo is 4,096 patches, so the API shrinks
    it to about 1,600 px.
  - gpt-6.1-sol isn't in that table. Nothing in the repo says what its limits are.
  - The guide offers `original` detail for "large, dense, spatially sensitive" images. The marked
    screenshot is one of those: 28 px labels on a busy page (`firecrawl_scripts/mark.js`).
    `photos.parts` cuts it into parts 1,200 px tall, sized for older limits.
  - The picker that was tested sent official photos at low detail. Now every grid goes at high
    detail (`openai_adapter._image_part`). That was decided in #1 step 1, but its cost was never
    measured.
- **How to apply it in this build:**
  - Look up gpt-6.1-sol's image size limits. Record how many patches each image becomes on the
    call's record (free), so the cost per image shows.
  - Try `original` detail on the screenshot parts in the eval (fewer, taller parts) before changing
    anything (Julian's call: money).
  - Fix the comment.
- **One real post:** OpenAI, [Images and vision guide](https://developers.openai.com/api/docs/guides/images-vision).
  It lists each detail level and each model's patch budget.

### 8. Delta debugging with cheap probes · **good to know**

- **What it is:** finding which part of an input causes a failure by testing halves of it is
  called delta debugging. When one part is at fault it needs about log n tests, and more when
  several are. Each test should be as cheap as you can make it.
- **Why it matters here:**
  - `work._past_refused_tiles` calls `gateway.refused_by_halving(lambda some: refused_by_filter(asked_with, some), cut)`.
    Each probe, `asked_with`, is a full picker call: the instructions, every screenshot part,
    every text piece, and the reasoning.
  - Before that, `gateway.past_the_safety_filter` tries leaving out each grid alone, then all of
    them (`work._picked_past_the_safety_filter`).
  - Worst case, on a page with 16 grids: 1 call, 16 one-grid tries, 1 all-grids try, then 1 more
    call, 16 probes and the final call. That is 36 full picker calls, about $4–7 (estimate). A
    probe that passes pays for a full answer that is thrown away.
  - The watcher already probes cheaply: `probe_video` asks for one word with MINIMAL thinking
    (`catalog.THINKING_LEVEL_FOR_PURPOSE`).
- **How to apply it in this build** (Julian's call: money):
  - Add a `probe_pictures` purpose, on the picker's model and deployment: only the tiles being
    tested, a one-word answer, the lowest effort, a small `max_output_tokens`.
  - First check it reproduces a recorded refusal. Azure's content filter scores the whole prompt,
    so a probe without the page text might pass where the full call was refused. Keep the
    full-call probe as the fallback.
- **One real post:** Andreas Zeller, The Debugging Book, [Reducing Failure-Inducing Inputs](https://www.debuggingbook.org/html/DeltaDebugger.html).
  It explains delta debugging: the number of tests is logarithmic in the best case and n² in the
  worst.

### 9. Running per-photo calls in parallel, a few at a time · **good to know**

- **What it is:** independent model calls run a few at a time, up to a cap, instead of one after
  another. Total time is then about the slowest few, not the sum of all.
- **Why it matters here:**
  - `work.save_photos` calls `keep_photo` → `note_face` for one photo after another.
    `library_items.add_kept_photos` calls `label_photo` one after another, and holds LIBRARY_LOCK
    through each.
  - On a declared-photo fallback that is up to 50 face notes, then 50 labels, one at a time, while
    the owner waits for read_page.
  - Photo downloads already run 8 at a time (`picker_grids._fetched`, FETCHING_TOGETHER).
- **How to apply it in this build:**
  - Collect the photos, run the calls in a pool of about 4 to 8, then take the lock only to add the
    rows (#42, #10).
  - This is code structure, so it's mine to change. Keep the cap under Azure's per-second limits
    (`docs/rate-limits.md`).
- **One real post:** OpenAI Cookbook, [api_request_parallel_processor.py](https://github.com/openai/openai-cookbook/blob/main/examples/api_request_parallel_processor.py).
  It "parallelizes requests to the OpenAI API while throttling to stay under rate limits".

---

## Also seen here

- #9 Golden dataset: `jobs/work.py:check_page`. ADR 0006's 126-page set, with its 12 junk pages,
  lives in `runs/`, outside the repo. The only labelled set in the repo is
  `docs/runs/broll-tests/face-notes.json`.
- #10 Short transactions: `jobs/watcher.py:_keep` still runs `work.save_video_pictures` →
  `note_face` inside `transaction.atomic()`.
- #11 Regression evals: `gateway/catalog.py:MODEL_FOR_PURPOSE`. pick_photos, note_face and
  label_photo moved to gpt-6.1-sol "with no comparison test"
  (`tests/test_frozen_rules.py:test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol`).
- #19 Stopping conditions: `jobs/work.py:_past_refused_tiles`. Up to 36 full picker calls on one
  page, with no dollar cap.
- #27 Confidence intervals on eval scores: `jobs/photos.py` (the comment on
  `NOTE_FACE_INSTRUCTIONS`, "tested on the 8 test products' 74 photos"; spec #1 says it
  "matched the user's judgement on all 74 kept photos"). That rests on 15 faces, 13 of them from
  one page.
- #34 Calibrating an LLM judge: `jobs/work.py:check_page`. It is a Judgement whose "unreadable"
  sends the owner to the page panel, and it has never been compared with Julian's calls.
- #35 Agent evals on a real model: `jobs/library_items.py:label_photo`. It has never run on a
  real model in a test, and the decision map says "not measured".
- #37 Constrained decoding: `jobs/photos.py:PickedPicture.label_of`. The picker's colour can't be
  held to its own list in one schema. The labelling call does it right in
  `photos.photo_label_for`.
- #39 Prompt decomposition: `jobs/photos.py:PICK_INSTRUCTIONS`. One call does about ten jobs.
- #42 Leases: `jobs/library_items.py:add_kept_photos` holds LIBRARY_LOCK through every labelling
  call.
- #47 Precision vs. recall: `jobs/work.py:note_face`. A failed note counts as a face, which is
  right. A wrong "no" is not covered (concept 2).
- #63 Content-addressed keys: `jobs/work.py:note_face` and `jobs/library_items.py:label_photo`.
  `pay_once` with `images_may_move` matches by the photo's link only, so a photo changed at the
  same link gets the old answer. A hash of the photo's bytes would survive a re-read and still be
  right.
- #76 Indirect prompt injection: `agents/producer.py:ReadPage.run`. `check.reason` is a sentence
  the model wrote from the page's text, and it goes word for word into the owner's
  PAGE_UNREADABLE notice.

---

## Bugs and loose ends

- **Stale claim.** FACE_NOT_NOTED (`jobs/work.py`) and the `ProductPhoto.has_face` help text say
  a face photo "is used only when no other photo shows what the ad needs". No code ranks photos by
  `has_face`: it only decides `face_swap.for_clip`. Spec #1 says the same old thing.
- **Wrong test cited.** `jobs/photos.py` says `NOTE_FACE_INSTRUCTIONS` was tested by
  `broll-test/face_notes.py`. That script tests the picker's `face_images` in `PICK_INSTRUCTIONS`.
- **Wrong reason.** `gateway/catalog.py` justifies pick_photos with 92%, which is a gpt-5.6-sol
  number.
- **A check_page failure in `ReadPage.run` posts no notice of any Kind.** It isn't logged either,
  since `agents/loop.py:EXPECTED_FAILURES` covers it. Only the tool answer and the ModelCall row
  show it. copy_page_text, right next to it, posts PAGE_TEXT_NOT_COPIED.
- **The fresh re-read's check can fail the read.** In `work.read_and_check_page`, the second
  `check_page` (on the fresh read) is outside the try. An outage there fails the whole read
  instead of opening the page panel.
- **Paid for and never read.** `PickedPhotos.product`, `product_sections`, `notes` and
  `video_notes`. And every picked official (R) photo gets its own `note_face` call, because the
  picker is told to note I numbers only.
- **Face notes can't be found in traces.** `FaceNote` isn't a Judgement and has no reason, so
  `ModelCall.decision` and `reason` stay empty and there is no Langfuse score
  (`gateway.VERDICT_SCORES`). Face notes can't be filtered or graded from the traces.
- **Spec #1 contradicts itself.** Step 1 says "never 512 px"; step 4 says "shrunk to fit 512 ×
  512". The code follows step 1 (`gateway.MAX_IMAGE_SIDE`).
- **66 photos, not 74.** `face-notes.json` has 74 entries, but 8 of them failed to download, so
  "all 74 photos" is really 66.
- **A cut-out's label isn't checked against its original.** `label_photo` on a cut-out
  (`product_only._cut`) works out every field again from scratch. Nothing checks that it agrees
  with the original's colour or side (concept 4).

---

## Checked by an independent reader

Checked against the code at `fd346df` (paths under `backend/`), reading only.

- Eval–production skew · CORRECTED · The 92% (gpt-5.6-sol, at most 6 official photos at low
  detail), the 6.1-sol move with no comparison test, the harness building pictures its own way,
  and no eval for check_page or label_photo all hold. Fixed one point: the `photos.py` comment
  says the call is "worded as the picker's note", which was tested. It doesn't say the one-photo
  prompt was tested. The text now says the one-photo call never ran on a real model (fake-model
  tests do run it), and that 66 of the 74 photos opened. Tag stays "must apply now".
- Defense in depth: an independent face detector · CONFIRMED · `face_swap._photo_with_a_face`
  reads only `has_face`. A False returns the item unswapped from `face_swap.for_clip`, with no
  notice. `note_face` returns True on failure. `face-notes.json` has 15 faces, 13 on product 02.
  Spec #1 step 1 has the "prefer photos without a stranger's face" wording. A photo that gets a
  cut-out first (`product_only.to_send`) is no longer a photo, so the swap applies to the rest
  (e.g. in-use photos). That narrows the case but doesn't change it. Nothing changed.

Cite check: every `file:symbol` cite in the concepts and the table names a file that exists and
a symbol in it. Nothing to fix there. One cite outside those sections, flagged and not changed:
"Also seen here" #27 puts "Matched on all 74" in `jobs/photos.py`. Those words are spec #1's
(step 1, "matched the user's judgement on all 74 kept photos"). The `photos.py` comment says
only "tested on the 8 test products' 74 photos".
