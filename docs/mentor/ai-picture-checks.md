# Design mentor: picture checks, cut-outs and recolours

Design read: ADR 0008, ADR 0012, spec #273 (whole), `CONTEXT.md` (Quality check, Picture check,
Owner part, Library), `docs/limitations.md` (start-picture judge, colour mark),
`evals/calibrate_picture_check.py` and `evals/picture_check_cases.json` (read, not run). Code
read at `fd346df` (main merged in). Paths are under `backend/` unless they start with `docs/`.

Teaching only. Applying any concept is a design change and needs Julian's yes.
"(Julian's call)" marks a change to what a model checks or decides, what the owner is told, a
threshold, or money.

**The main finding:** `check_starting_picture` has no caller on the live path.
- `jobs/work.py:judge_starting_picture` is the only code that asks it.
- Its only caller is `evals/calibrate_picture_check.py:judge_as_the_app_does`. The
  `broll-test/usage_*.py` scripts ask the same purpose with their own prompts.
- `tests/test_broll_sent_list_end_to_end.py:test_broll_starting_picture_is_drawn_and_checked_before_filming`
  asserts it is never paid for.
- The live start-picture check is the scene agent's: `agents/scene_agent/picture_check.py:check`
  (purpose `check_scene_picture`), called from `agents/scene_agent/draw_starting_picture.py:make_picture`.
- So the one picture judge with an eval is the one nothing calls.

---

## Systems in this group

Cost assumptions (estimates, not a bill):
- A picture shown to sol costs about 1.5–2.5K input tokens at "high" detail, 2048 px max
  (`gateway/gateway.py:shrunk_image`, `gateway/openai_adapter.py:_image_part`).
- sol costs $2/M in and $10/M out (`gateway/catalog.py:PRICE_PER_MILLION_TOKENS`). A judge's
  "look first" fields come to about 0.5–2K output tokens.
- One high-quality edit at about 1152×2048 cost $0.06 (`docs/adr/0012-legacy/video-model-tests.md`).
  Each extra input picture adds $8/M picture tokens (`catalog.PRICE_PER_MILLION_PICTURE_TOKENS`).
- sol calls run on Azure when its keys are set (spec #273 calls the helper ask free there).
- Under ADR 0012 every scene draws from picked photos, talking scenes too. Each pick passes
  through cut-out → face swap → paint (`agents/scene_agent/draw_starting_picture.py:swapped`).
  A cut-out, swap or paint is made once per ad and reused.

| System | What it is asked | Model and why | Input → output | What checks its output | Eval | How it fails (notice Kind) | Rough cost per ad (estimate) |
|---|---|---|---|---|---|---|---|
| **check_starting_picture** | `jobs/picture_check.py:STARTING_PICTURE_CHECK`: find only impossible objects, "Never compare the two pictures". Owner-part rule added by `jobs/owner_parts.py:CHECK_RULE`. Handoff: ask number, plus the part when there is one (`owner_parts.check_handoff`) | gpt-6.1-sol. "With the photo, sol caught 7 of Julian's 10 wrong pictures and failed none of 88 good ones; luna missed…" (`gateway/catalog.py:MODEL_FOR_PURPOSE` comment) | [picture, shop photo] → `StartingPictureVerdict` {objects[], verdict, problem}. Fails when 2 of 3 asks fail (`jobs/work.py:judge_starting_picture`) | Vote of 3 asks (`picture_check.ASKS`, `FAILS_TO_FAIL`); a fail must say why (`StartingPictureVerdict._a_failure_says_why`) | `evals/calibrate_picture_check.py`: 62 cases (10 must-fail; 32 of the 52 passes were never graded). 6 of 10 caught, 0 of 52 failed (docstring, 2026-10-11). Pictures sit in `/mnt/project-files`, not in the checkout | An ask that is blocked, unusable or hit by an outage counts as unchecked. All unchecked → passes. `REFUSED_BY_THE_FILTER` (dev), `CHECK_IT_YOURSELF` (owner), `LEFT_OUT_BY_THE_FILTER` (dev) (`judge_starting_picture`) | **$0 live: no caller.** One eval run: $3.02 (`calibrate_picture_check.py` docstring) |
| **check_made_picture** | `jobs/cut_out.py:MADE_PICTURE_CHECK`: shows what it is meant to and the product is unchanged from its references. Handoff: the producer's prompt, label and reference lines (`MadePictureCheckHandoff`) | gpt-6.1-sol. "its check, asked once as the cut-out check is" (`catalog.py` comment). No reason given for the model | [made, refs…] → `MadePictureVerdict` → `Said` (`cut_out.check_made_picture`) | One ask. A fail or no answer → not stored (`jobs/made_pictures.py:make_picture`) | None | Unchecked or fail → `PictureNotMade` → the producer's tool answers "Failed" (`agents/producer.py:MakePicture.run`). **No notice Kind**, so nothing reaches Sentry | 0 unless the producer calls `make_picture`. About $0.01–0.04 per call |
| **check_part_picture** | `jobs/owner_parts.py:PART_PICTURE_CHECK`: the part alone, as its sentence says. Handoff: part and sentence (`PartPictureCheckHandoff`) | gpt-6.1-sol, "on the picture judges' model" (`catalog.py` comment) | [drawn, drawn from] → `PartPictureVerdict` → `Said` (`owner_parts.check_part_picture`) | Shared loop, at most 2 redraws. Unchecked passes; the owner's yes is the last check (`owner_parts._redrawn`) | None | Refused → `REFUSED_BY_THE_FILTER` (dev). No answer → `PART_CHECK_UNCHECKED` (dev) (`check_part_picture`). All tries fail → `PART_NOT_DRAWN` (owner). Outage → `PART_TRIED_AGAIN` (dev) (`owner_parts._say_it_failed`) | 0 unless the ad has an owner part. About $0.01–0.02 per ask, up to 3 per drawing, again on each correction |
| **check_face_swap** | `jobs/face_swap.py:FACE_CHECK`: do the face features differ. "Nothing else counts: changes to the body, clothes, pose, product, background or framing never fail it." Handoff: only the picture order (`FaceCheckHandoff`) | gpt-6.1-sol, "its face-only check, on the picture judges' model" (`catalog.py` comment) | [photo, new] → `FaceSwapVerdict` {features, verdict, problem} (`face_swap._check`) | Shared loop, at most 2 redraws (`face_swap.for_clip`). A check that can't be made fails the try (`_check`, `_unchecked_before`) | None. Prompt "Tested Oct 30 on round 7's six photos" (`SWAP_PROMPT` comment) | No answer → `FACE_SWAP_UNCHECKED` (dev). Refused → `REFUSED_BY_THE_FILTER` (dev). Outage raised → `FACE_SWAP_INTERRUPTED` (dev). All tries fail → original face sent, `FACE_SWAP_FAILED` (owner) (`for_clip`, `_say_it_failed`) | Per picked photo with a stranger's face, per portrait: 1–3 × about $0.01–0.02 |
| **check_cut_out** | `jobs/cut_out.py:CUT_OUT_CHECK`: the thing is alone and unchanged. Framing and position are not a fail (#273 point 8). Handoff: keep, photo label, hidden part (`CutOutCheckHandoff`) | gpt-6.1-sol, "The picture judges' model" (`catalog.py` comment). Never tested on its own | [cut-out, photo, up to 8 helpers] → `CutOutVerdict` → `Said` (`cut_out.check_cut_out`) | One ask per draw inside `cut_out.redraw_with_check`. A fail must say why | None | Refused → `Unchecked(refused)` → the product-only cut-out is Unusable for good → `CUT_OUT_FAILED` (dev), sent via `product_only.to_send`. No answer → tried next send → `CUT_OUT_TRIED_AGAIN` (dev) (`product_only._cut`, `_tried_again`). An owner photo's clean copy: `OWNER_PHOTO_UNCHECKED` (dev) (`jobs/work.py:owner_photo_check`) | Per cut-out photo: 1–3 × about $0.01–0.06 (2 to 10 pictures) |
| **make_product_only** (what the cut-out check checks) | `jobs/product_only.py:_prompt` + `jobs/reference_photos.py:clean_photo_of`: only the product, exactly as it looks, plain light grey background; with helpers, "take only the shape of that part" | gpt-image-2.5-sunburst, "drawn as the clean picture of a shop owner's thing is" (`catalog.py` comment) | [photo, *helpers] at the photo's shape (`gateway/catalog.py:edit_size`, `gateway/gateway.py:edit_picture` `keep_shape`) → PNG → `ProductOnly` item, relabelled by `label_photo` (`_cut`) | `check_cut_out`, at most 2 redraws (`jobs/checked_pictures.py:made_and_checked`) | None | Refused twice, or all checks fail → Unusable, kept on the row → photo goes whole with `CUT_OUT_FAILED` (dev) (`product_only.to_send`). Outage → `CUT_OUT_TRIED_AGAIN` (dev) (`for_clip`). Label fails → `PHOTO_NOT_LABELLED` (dev) (`jobs/library_items.py:label_photo`) | Per picked look photo that isn't alone, or a line-up: 1–3 × about $0.06–0.12, plus a label (about $0.01). With 1–4 such photos per ad, about $0.07–1.50 |
| **pick_cut_out_helpers** | `jobs/product_only.py:HELPER_INSTRUCTIONS`: is a part hidden, which part, and which library pictures show it. Handoff: photo label and what to keep (`HelperHandoff`) | gpt-6.1-sol. #273 point 6 named it; no test of the model (`catalog.py` comment) | [photo, every library sheet] → `HelperAnswer` {covered, covered_part, helpers: list[int]} → kept on `LibraryItem.cut_out` (`_helpers_for`, `_cut`) | Code keeps only numbers shown on the sheets, never the photo or its versions, at most 8 (`_helpers_for`). Nothing checks `covered` | None | Any `WHY_IT_FAILED` error → photo goes whole for now, `CUT_OUT_TRIED_AGAIN` (dev) (`for_clip`). Covered with no helper → Unusable for good, `CUT_OUT_FAILED` (dev). **Sheets the filter leaves out are dropped silently** (`_helpers_for` discards `left_out`) | Once per cut-out photo: the photo plus ⌈library/4⌉ sheets, about $0.02–0.06 each |
| **recolour_picture** (paint) | `jobs/recolour.py:RECOLOUR_PROMPT`: "Make the product in picture 1 the exact colour of the product in picture 2. Change nothing else." | gpt-image-2.5-sunburst, "on the picture model that cleans the shop owner's photos" (`catalog.py` comment, which still mentions a check) | [picture, colour reference] at the picture's shape → `Recoloured` item; label copied, `colour` set to the target (`recolour.recolour`) | **Nothing**, by decision (#273 point 4): `made_and_checked(make, lambda _: None)` | None | Refused twice → original sent, `RECOLOUR_FAILED` (dev) (`recolour._say_it_fell_back`). No reference → `NO_COLOUR_REFERENCE` (dev) (`draw_starting_picture.py:swapped`). Outage raised | 0 for a one-colour product. Otherwise once per picture and colour: about $0.06–0.12 |
| **Shared loop** `made_and_checked` | Not a model. Makes, checks, redraws while the check fails; gives up at the 2nd refusal (`jobs/checked_pictures.py:made_and_checked`) | — | `make(try)`, `check(file)` → `Checked` {file or None, problems} | Its callers' checks. Replays paid tries via `tried_before` | — | Returns None; each caller picks the fallback | Sets the cap: at most 3 draws per picture (`MOST_REDRAWS = 2`) |

---

## How it is tested today

**What there is**
- **Through the chat (#273 flows):** `tests/test_cut_outs_and_paints.py`. A paint is made once
  and never checked. The sizes asked follow the photo's shape. A one-of-three-colours clip
  paints nothing. Colour answers are enums. Cut-outs are made when sent. Helpers reach the model
  and the check.
- **At the module seam:**
  - `tests/test_product_only.py` (18 tests): helper filtering, Unusable kept, refusal retry,
    outages, label failure. They call `for_clip` directly.
  - `tests/test_recolour.py`, `tests/test_recolour_end_to_end.py`
  - `tests/test_scene_face_swap.py`
  - `tests/test_owner_part_picture.py`, `tests/test_make_picture.py`
- **Pure logic:** `tests/test_edit_size.py` (`catalog.edit_size`).
- **Pins:**
  - `tests/test_frozen_rules.py` pins the dead judge's wording, its 3/2 vote and
    `picture_check.MOST_REDRAWS`.
  - `tests/test_traces.py` checks that every scored verdict exists in its answer form.
- **The eval's report logic:** `tests/test_calibrate_picture_check.py`, with a fake judge.
- All but the pure ones commit to the database, so they are `slow` and skipped by the fast suite
  (`tests/conftest.py`).

**What they cannot see**
- **Every check is faked to a fixed verdict.** `tests/conftest.py:fake_model` answers
  `CUT_OUT_OK`, `NOTHING_COVERED` and `PART_PICTURE_OK`; the face tests answer `FACE_PASSES`.
  The tests prove plumbing (who is asked, what is sent, what happens on a fail, a refusal or an
  outage), never that a verdict is right.
- **The fake picture model ignores what it is asked.** `gateway/fake.py:FakeModel.edit` returns
  a flat 72×128 picture for any size. No test can see shape, colour, label drift or clutter left
  in, and no code checks the real output's size either.
- **No eval for 7 of the 8 model systems.** The one eval measures a function with no live
  caller, and its pictures aren't in the checkout.
- **The tested path isn't the live one.** `test_product_only.py` tests `for_clip`'s "left out of
  the clip" rule (#273 point 7). Live code reaches it only through `to_send`, which sends the
  photo whole.
- **The live prompts aren't pinned.** Nothing pins `CUT_OUT_CHECK`, `FACE_CHECK`, `SWAP_PROMPT`
  (the "tested" prompt), `HELPER_INSTRUCTIONS` or `RECOLOUR_PROMPT`. The dead judge's wording is
  pinned.
- **No production rates.** Nothing reads how often each check fails in real runs.

---

## Concepts, ranked by impact on this build

### 1. Training–serving skew (evaluate the judge you ship) · **must apply now**

**What it is**
- An eval number only means something for the exact code, prompt, model and inputs it ran on.
- When production calls something else, the number describes nothing that runs.
- ML calls this training–serving skew. The standard defence: the eval calls the same code as
  production, and you notice when production stops calling it.

**Why it matters here**
- `evals/calibrate_picture_check.py:judge_as_the_app_does` imports `jobs/work.py:judge_starting_picture`.
  Nothing on the live path calls it.
- The live check differs in every way the eval measured:
  - It is `agents/scene_agent/picture_check.py:check`: a different prompt (product match plus
    physics), one ask instead of 3, and more pictures.
  - The old judge was told "Never compare the two pictures". The live one is told to compare.
- These still describe the dead judge as if it ran:
  - the catalog's reason for the model ("sol caught 7 of Julian's 10", `gateway/catalog.py:MODEL_FOR_PURPOSE`);
  - the toilet limitation (`docs/limitations.md`);
  - ADR 0008's "The start-picture check … unchanged".
- The five live checks in this group have no eval: `check_cut_out`, `pick_cut_out_helpers`,
  `check_face_swap`, `check_part_picture`, `check_made_picture`.
- So the round 7 numbers (83 of 118 cut-outs failed, 0 of 17 paints passed, spec #273) were
  never turned into a measure of the checks themselves.

**How to apply it in this build**
- Point evals at live entry points only. A cut-out check eval calls
  `jobs/cut_out.py:check_cut_out`. A start-picture eval calls `agents/scene_agent/picture_check.py:check`.
- Add a free test: every purpose in `MODEL_FOR_PURPOSE` and in `evals/` must appear as
  `purpose="…"` somewhere in live code (`jobs/`, `agents/`, `gateway/`). It would catch the
  unused `"scene_picture_check"` and `"make_starting_picture"` entries. It would not catch
  `check_starting_picture`: its dead caller `judge_starting_picture` is itself in
  `jobs/work.py`. That needs a reach check too: a purpose's function must be called from a live
  entry point, not only from `evals/`, `broll-test/` or `tests/`.
- Keep a one-line coverage map per check: purpose → eval → cases → last run → result.
- First labelled set: the cut-out check. Round 7 already has 118 cut-outs with verdicts.
  Julian grades a sample, and the eval re-asks the check on them (Julian's call: his time, and
  the run's cost stated first).
- Whether to delete the dead judge, its eval and its pins is Julian's call (deletes are held).

**One real post:** Google, "Rules of Machine Learning", Rules #29–#37 on training–serving skew
("Re-use code between your training pipeline and your serving pipeline").
https://developers.google.com/machine-learning/guides/rules-of-ml

### 2. Two-axis edit evaluation: instruction fidelity and content preservation · **must apply now**

**What it is**
- A picture edit has two jobs: make the change asked for, and change nothing else.
- Image-edit benchmarks score these separately. The models best at following the instruction
  often over-edit the rest.
- A check that scores only one axis passes edits that broke the other.

**Why it matters here**
- **Face swap checks only the faces.**
  - `jobs/face_swap.py:FACE_CHECK`: "changes to the body, clothes, pose, product, background or
    framing never fail it."
  - Yet `SWAP_PROMPT` asks the model to "Keep … the product with its labels", and the whole
    photo is redrawn with no mask (`gateway/openai_adapter.py:OpenAIProvider.edit`).
- **Paints have no check at all.** `jobs/recolour.py:recolour` runs `made_and_checked(make, lambda _: None)`
  on a prompt that says "Change nothing else".
- **The live check then compares against the edited copy.**
  - `agents/scene_agent/picture_check.py:check` shows the scene check `photo.file`, the
    cut-out, swapped or painted version.
  - `SelectedPhoto.original_file` is kept (`agents/scene_agent/photos.py`) but never shown.
  - So a label a swap or paint changed becomes the "real product" every later check matches.
- OpenAI's image guide warns the model "can still struggle with precise text placement and
  clarity". Print is what drifts on a full redraw.
- The cut-out check already scores both axes ("alone" and "unchanged", `jobs/cut_out.py:CUT_OUT_CHECK`).
  It is the pattern to copy.

**How to apply it in this build** (Julian's call: changes what a model checks)
- **Measure first, free:** the round export puts every swap and paint beside its original
  (`evals/round_export.py`), so Julian can grade preservation.
- **Face swap:** add one preservation question: "Outside the faces, is the product, with its
  print, the same as in picture 1?" `SWAP_PROMPT` was tested with this check, so re-test the pair.
- **Paint:** a preservation-only question that never asks about framing: "Apart from the
  product's colour, is anything different: shape, print, letters, parts?" The framing fails
  that sank `RECOLOUR_CHECK` came from the 1152×2048 canvas, which #273 point 1 fixed. Concept 6
  is the free version.
- **Scene check:** show it `original_file` beside each edited photo, so the chain is checked
  against the shop's photo.

**One real post:** Apple Machine Learning Research, "GIE-Bench: Towards Grounded Evaluation for
Text-Guided Image Editing". It scores functional correctness and content preservation separately,
and finds GPT-Image-1 "often over-modifies irrelevant image regions".
https://machinelearning.apple.com/research/gie-bench

### 3. Online evaluation from production traces · good to know (retagged by the independent reader)

**What it is**
- Watch each check's verdicts and each fallback in real traffic, over time.
- Measures: first-try fail share, redraws per kept picture, unchecked share, "can't be had"
  count, paints that fell back.
- A jump means a prompt, a model or the inputs changed.
- It doesn't prove a verdict right (concept 1 does that), but it costs nothing and shows where
  to look.

**Why it matters here**
- Spec #273's problem statement: round 7's failures "fell back quietly … Julian could only find
  the bad ones by looking."
- The data is already written:
  - every verdict is a `ModelCall` row;
  - `gateway/gateway.py:VERDICT_SCORES` sends `check_cut_out`, `check_made_picture` and
    `check_part_picture` verdicts to Langfuse as scores (`_scores`);
  - fallback Kinds (`CUT_OUT_FAILED`, `RECOLOUR_FAILED`, `FACE_SWAP_FAILED`) reach Sentry
    grouped by kind (`jobs/notices.py:post_notice`).
- Gaps:
  - `VERDICT_SCORES` scores the dead `check_starting_picture`, but not `check_face_swap` or the
    live `check_scene_picture`.
  - The helper ask's `covered` and its number of helpers are never scored.
  - Nobody reads the rates.

**How to apply it in this build**
- Add `check_face_swap` to `VERDICT_SCORES` and to `tests/test_traces.py:JUDGES_ANSWERS`. Its
  `verdict` field already fits.
- `check_scene_picture` answers in `passes` booleans, so `_scores` needs a path for it.
- One Langfuse dashboard per purpose, per round: fail share of first tries, unchecked share,
  redraws per kept cut-out.
- A read-only script over a round's `ModelCall` rows (no paid calls), added to `evals/round_export.py`.
- An alert when a check fails over half its first tries in a day (Julian's call: threshold).
- Sample a few passes and fails each round for Julian to grade. That builds concept 1's set.

**One real post:** Langfuse docs, "Evaluation core concepts": every judgment is a score; "Use
custom dashboards to track scores and application performance over time"; alerts when a score
crosses a threshold. https://langfuse.com/docs/evaluation/core-concepts

### 4. Fail-open vs fail-closed, decided per check · good to know

**What it is**
- When a check can't answer, either let the work through (fail open: the ad keeps moving) or
  stop it (fail closed: a bad picture stays out).
- Choose by what a wrong picture costs against what a stall costs. Write the choice down per
  check.

**Why it matters here.** One event (no answer, or a refusal) has seven different outcomes:
- **Start judge:** all asks unanswered → passes (`jobs/work.py:judge_starting_picture`).
- **Product-only cut-out:**
  - Check gave no answer → photo whole for now, tried again next send.
  - Check refused → unusable for the whole ad.
  - (`jobs/product_only.py:_cut`)
  - The same outage-vs-refusal split makes one temporary and the other permanent.
- **Owner's clean picture:** unchecked is kept. "NOT APPROVED YET (audit item 24: never asked)"
  (`jobs/work.py:owner_photo_check`).
- **Part picture:** unchecked passes; the owner's yes is the last check (`jobs/owner_parts.py:_redrawn`).
- **Made picture:** unchecked → not stored (`jobs/made_pictures.py:make_picture`).
- **Face swap:**
  - An unchecked try fails (`jobs/face_swap.py:_check`).
  - When every try fails, the stranger's real face is sent (`for_clip`).
- **Paint:** refused twice → original colour sent, developer told only (`jobs/recolour.py:recolour`).

**How to apply it in this build** (Julian's call)
- One table in the docs, one row per check: on no answer → on every try failing → who is told.
  Julian picks each row.
- Start with the rows marked "NOT APPROVED YET" and the face-swap row.

**One real post:** Google, "Building Secure and Reliable Systems", ch. 8: fail safe ("serve as
much as possible in the face of uncertainty") vs fail secure ("lock down fully").
https://google.github.io/building-secure-and-reliable-systems/raw/ch08.html

### 5. Negative caching (a stored failure needs an expiry or a key) · good to know

**What it is**
- Storing "this failed" or "this doesn't exist" so you don't pay to ask again.
- Done well, a negative answer expires, or is tied to the inputs it was true for. The world
  changes, and a stale "no" blocks the right answer forever.
- DNS caps negative answers at hours.

**Why it matters here**
- `jobs/product_only.py:_unusable` writes "unusable" on the row, and `_cut` returns it first,
  forever. Nothing else writes `LibraryItem.cut_out` (only `_cut` and `_unusable`).
- **The helper answer is stored once and never re-asked** (`_cut`).
  - If the owner later sends a photo that shows the hidden part, it enters the library.
  - The cut-out still says "no other photo shows it", and the photo keeps going whole.
- Three failed checks, a refused helper (see Bugs) or one refused check make that photo's
  cut-out unusable for the whole ad.
- `call_model(pay_once=True)` hands back any recorded answer for the same handoff and pictures
  (`gateway/gateway.py:call_model`), so a wrong verdict replays too.
- `jobs/face_swap.py:_unchecked_before` keeps an unanswered check failed for that try. That is
  deliberate (#345).

**How to apply it in this build**
- Key the stored helper answer to what it saw: keep `sheets.entries` beside it, and re-ask when
  the library has gained look pictures since. One more helper ask is about $0.03 (Julian's
  call: money).
- Keep the reason kind with "unusable". A "covered, no helper" holds until the library changes.
  A refusal could be retried once per new run (Julian's call).
- Test through the chat: owner sends the hidden part's photo after an Unusable → the next send
  asks again.

**One real post:** IETF RFC 2308, "Negative Caching of DNS Queries": negative answers get a TTL,
and "Values of one to three hours have been found to work well".
https://datatracker.ietf.org/doc/html/rfc2308

### 6. Deterministic (code-based) graders before LLM judges · good to know · known

**What it is**
- Checks written as plain code: size, shape, background plainness, colour distance.
- They are free, instant and give the same answer every time.
- Run them first, and ask a model only what code can't see.
- Colour distance has a standard measure: ΔE in CIELAB (CIEDE2000).

**Why it matters here**
- **Paints have no check** because the model check failed good edits on framing (spec #273
  cause 6; `jobs/recolour.py` docstring "Nothing checks the paint").
- **Colour is measurable here.**
  - A cut-out sits "on a plain light grey background" (`jobs/reference_photos.py:clean_photo_of`),
    so code can separate the product's pixels from the background.
  - Code then compares their mean colour with the colour reference's.
- **Known limitation:** "A colour mark doesn't stop a wrong-colour picture … nothing checks the
  picture's colour. Add a check only after a graded failure." (`docs/limitations.md`).
- **The cut-out check pays to see what code can.** Each ask costs about $0.01–0.06, and half of
  what it asks ("on a plain background") is a check of border pixels (`jobs/cut_out.py:CUT_OUT_CHECK`).
- **Nothing checks the returned size.** The picture is never compared with the size asked
  (`gateway/gateway.py:edit_picture`). Tests check only the size asked
  (`tests/test_cut_outs_and_paints.py:test_a_cut_out_and_a_paint_are_asked_for_in_the_photos_shape`).

**How to apply it in this build**
- Pure functions with unit tests: `plain_background(file)`, `product_lab(file)`, `delta_e(a, b)`,
  `size_matches(file, asked)`.
- Pillow's `ImageCms` converts to Lab, so no new dependency is needed. scikit-image has
  `deltaE_ciede2000` if one is wanted.
- **Record before you fail:** store each paint's ΔE on its `Recolour` record
  (`jobs/library.py`) and show it on the grading page.
- After a graded wrong-colour failure, Julian sets the threshold (Julian's call: a new fail rule
  and a threshold).

**One real post:** Hamel Husain, "Your AI Product Needs Evals": Level 1 assertions "should run
fast and cheaply"; "conquer a good portion of your Level 1 tests before you move into
model-based tests". https://hamel.dev/blog/posts/evals/

### 7. Parallel fan-out (sectioning) of independent picture steps · good to know

**What it is**
- Independent slow calls run at once and are gathered at the end.
- The wait is the slowest call, not the sum of all of them.

**Why it matters here**
- `agents/scene_agent/draw_starting_picture.py:make_picture` runs `swapped` for each pick, one
  after another, before the draw.
- Per pick, that can be:
  - a helper ask, up to 3 cut-out edits and 3 checks, and a label;
  - then a face swap (up to 3 edits and checks);
  - then a paint.
- OpenAI says complex edits "may take up to 2 minutes".
- Spec #273 cause 4: one ad spent 27 minutes on 8 cut-outs.
- Under ADR 0012 every scene draws from picks, talking scenes too.

**How to apply it in this build**
- Run `swapped` for a draw's picks in a small thread pool.
- Each pick already takes its own per-photo lock (`jobs/job_locks.py:one_at_a_time`, keyed by
  `job:number`), so two scenes sharing a photo still make it once.
- Close each thread's database connection, as `evals/calibrate_picture_check.py:judge_as_the_app_does`
  does.
- Azure pictures are serialised anyway (`gateway/gateway.py:_one_azure_picture_at_a_time`).
  Check `docs/rate-limits.md` before raising the pool size.

**One real post:** Anthropic, "Building effective agents", the Parallelization workflow:
"Sectioning: Breaking a task into independent subtasks run in parallel."
https://www.anthropic.com/engineering/building-effective-agents

---

## Also seen here

- #3 Timeouts, deadlines and retry budgets: `jobs/product_only.py:for_clip` holds a blocking `pg_advisory_lock` (`jobs/job_locks.py:one_at_a_time`) through the helper ask and up to 3 paid edits and checks, with no timeout; the same in `jobs/recolour.py:recolour` and `jobs/face_swap.py:for_clip`.
- #9 Golden dataset: `evals/calibrate_picture_check.py:load_cases` reads 62 pictures from `/mnt/project-files`; no case file exists for any other check in this group.
- #11 Regression evals: `jobs/cut_out.py:check_cut_out`: `CUT_OUT_CHECK` was reworded by #273 point 8 with no before/after; `tests/test_frozen_rules.py:test_the_start_picture_judge_is_unchanged_from_the_one_tested` pins the dead judge instead.
- #23 Data provenance: `jobs/recolour.py:recolour` stores the paint's `colour` as the target colour, though nothing looked at it.
- #25 Context budget (context rot): `jobs/product_only.py:_helpers_for` shows the helper ask every library sheet (up to 50, holding 200 pictures) to find one part.
- #27 Confidence intervals on eval scores: `jobs/face_swap.py:SWAP_PROMPT` "every one passed" on 6 photos fits a true pass rate as low as about 54% (95% Clopper–Pearson); the start judge's "6 of 10 caught" (`evals/calibrate_picture_check.py`) is as wide.
- #33 Contract and integration tests against the real thing: `gateway/fake.py:FakeModel.edit` returns 72×128 for any size asked, so no test sees what the real model returns.
- #34 Calibrating an LLM judge: `jobs/recolour.py:recolour` (check removed, #273 point 4) and `jobs/cut_out.py:check_cut_out` (loosened, point 8) were changed after Julian's eye found 3 good paints failed (#273 cause 6); no labelled set measured either change.
- #37 Constrained decoding: `jobs/product_only.py:HelperAnswer` takes `helpers: list[int]`, filtered after the answer (`_helpers_for`); #273 put the colour list in the schema, but not the library numbers shown.
- #43 Independent evidence for checks: `jobs/product_only.py:_cut` gives the cut-out check the helper ask's picks and part as truth; `agents/scene_agent/draw_starting_picture.py:swapped` makes the scene check compare against the edited copy, not `original_file`.
- #44 Stable prompt prefix: `jobs/product_only.py:_helpers_for` puts the photo before the sheets every helper ask of the ad shares.
- #47 Precision vs. recall: `jobs/product_only.py:_helpers_for` (`covered`: a wrong yes loses the cut-out, a wrong no invents the part) and `jobs/library_items.py:needs_product_only` (a paid cut-out decided by the picker's `alone`).
- #58 Feedback-driven retries: `jobs/cut_out.py:redraw_with_check` and `jobs/face_swap.py:for_clip` redraw with the same prompt and pictures; the check's problem never reaches the picture model (`jobs/checked_pictures.py:made_and_checked`).
- #59 Pixel-preserving segmentation: `jobs/face_swap.py:for_clip` redraws the whole photo to change a face; OpenAI's guide says the mask is guidance and "may not follow its exact shape", so only compositing back keeps the label exact.
- #61 Identity consistency check (face embeddings): `jobs/face_swap.py:_check` still asks only whether the features "differ", never whether the stranger is gone by an identity measure.
- #63 Content-addressed keys: `jobs/product_only.py:_cut` keys the helper answer and "unusable" to the row alone, not to the library they were judged against.

---

## Bugs and loose ends

- **A dead chain behind the start judge.** These have no live caller:
  - `jobs/work.py:judge_starting_picture`;
  - `jobs/owner_parts.py:CHECK_RULE`, `check_handoff`, `OwnerPartCheckHandoff`;
  - `jobs/picture_check.py:STARTING_PICTURE_CHECK`, `StartingPictureVerdict`, `MOST_REDRAWS`;
  - `VERDICT_SCORES["check_starting_picture"]`.
  - `ASKS` and `FAILS_TO_FAIL` are still read by `jobs/fix_check.py`.
  - Removing any of them is Julian's call.
- **Docs describe the dead judge as live:**
  - the `jobs/picture_check.py` docstring ("checks the clean pictures of an owner part and a
    fix's new starting picture");
  - ADR 0008's last consequence;
  - `docs/limitations.md` (start-picture judge);
  - `jobs/work.py:owner_photo_check` ("which `judge_starting_picture` still holds").
- **The frozen rule pins the wrong constant.**
  `tests/test_frozen_rules.py:test_the_start_picture_judge_runs_as_tested` pins
  `picture_check.MOST_REDRAWS` ("Julian 2026-10-10 21:48: two redraws"). The live loop uses
  `jobs/checked_pictures.py:MOST_REDRAWS`, which nothing pins.
- **Spec #273 point 7 isn't live behaviour.**
  - `draw_starting_picture.py:swapped` calls `product_only.to_send`, so an Unusable cut-out's
    photo goes whole to the drawing, with clutter, and is never left out.
  - The `jobs/product_only.py` docstring still says "cut-and-send leaves the photo out of the clip".
  - The scene agent is never told: its tool result carries only the ruling
    (`draw_starting_picture.py:result`).
- **A refused helper can't be left out.** In `jobs/cut_out.py:check_cut_out`, `may_leave_out`
  allows only the photo. If the safety filter refuses a helper, the check is refused, and
  `product_only._cut` stores "unusable" for good.
- **The helper ask drops refused sheets silently.** `jobs/product_only.py:_helpers_for` discards
  `left_out` from `library_sheets.past_the_filter`. `jobs/work.py:plan` posts
  `LEFT_OUT_BY_THE_FILTER` for the same event. `_helpers_for` also accepts numbers from sheets
  the model never saw (`sheets.entries`).
- **Stored helper numbers can go stale.**
  - `jobs/owner_parts.py:_kept` redraws a part's item in place and deletes its recolours.
  - `product_only._cut` then reads `library[number]` unguarded. A deleted helper raises a
    `KeyError`, which isn't in `WHY_IT_FAILED`, so the draw tool fails.
- **A made picture's failure has no notice Kind.** `jobs/made_pictures.py:make_picture` raises
  `PictureNotMade`, and only the producer's tool answer records it. It never reaches Sentry.
- **Stale names:**
  - `gateway/catalog.py:MODEL_FOR_PURPOSE` keeps an unused `"scene_picture_check"` beside the
    used `"check_scene_picture"`.
  - Its `recolour_picture` comment still names "its check".
  - `tests/test_cut_outs_and_paints.py:black_video_ad` scripts `check_recolour`, a purpose that no
    longer exists, so `assert "check_recolour" not in paid` can never fail.

---

## Checked by an independent reader

Checked against the code at `fd346df` (paths under `backend/`), reading only.

- Training–serving skew · CORRECTED · These hold: `judge_starting_picture`'s only caller is
  `evals/calibrate_picture_check.py:judge_as_the_app_does`, the end-to-end test asserts
  `check_starting_picture` is never paid for, the live check is one ask with a different prompt,
  and no eval covers the five live checks (`evals/owner_notes` only replays or passes them).
  Fixed one point: the proposed `purpose="…"` test would not catch `check_starting_picture`,
  because `jobs/work.py` still names it. The text now says so and adds a reach check. Tag stays
  "must apply now".
- Two-axis edit evaluation · CONFIRMED · These hold: `FACE_CHECK` says product changes "never
  fail it", `SWAP_PROMPT` asks to keep the labels, and `OpenAIProvider.edit` sends no mask.
  `recolour` runs `made_and_checked(make, lambda _: None)`. The scene check is shown
  `photo.file`, never `SelectedPhoto.original_file`, which is kept only in the record.
  Worth knowing: dropping the paint check was Julian's own call (#273 point 4, "I think we can
  just trust it"), so the paint part reopens a recent decision. Nothing changed.
- Online evaluation from production traces · CORRECTED, retagged good to know · The facts
  hold: `VERDICT_SCORES` has no `check_face_swap` or `check_scene_picture`, `check_scene_picture`
  answers in `passes` booleans, and round 7's failures were found by eye (#273). Retagged
  because nothing in the build gives a wrong result for want of it: every fallback Kind already
  reaches Sentry under its own name (`jobs/notices.py:post_notice`), and `ai-pictures.md` tags
  the same concept good to know.

Cite check: every `file:symbol` cite in the concepts and the table names a file that exists and
a symbol in it. Nothing to fix.
