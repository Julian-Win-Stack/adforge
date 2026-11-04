# Design mentor: picture models (the person, place pictures, owner photos and parts, face swaps)

Design read: ADR 0011, ADR 0012, `docs/lessons.md`, `docs/what-makes-a-good-ad.md`,
`docs/agents/proven-broll-rules.md`, `docs/adr/0012-legacy/broll-picture-logic.md`, `CONTEXT.md`
(Person, Starting picture, Before-picture, Pose, Product size, Reference photo, Owner part, Scene
picture, Second state), specs #210, #273, #303, #327. Code read at `fd346df` (main merged in).
Paths below are under `backend/` unless they start with `docs/`.

Teaching only. Applying any concept is a design change and needs Julian's yes.
"(Julian's call)" marks a change to what a model checks or decides, what the owner is told, a
threshold, or money.

---

## Systems in this group

All picture models are `gpt-image-2.5-sunburst` (`gateway/catalog.py:MODEL_FOR_PURPOSE`).
Its only recorded test: portrait "Good" at $0.03, edit "same face, sharp label" at $0.06
(`docs/adr/0012-legacy/video-model-tests.md`). Every call uses `quality="high"`
(`gateway/openai_adapter.py:OpenAIProvider.draw`, `OpenAIProvider.edit`).

Costs are estimates, not a bill: catalog prices (`gateway/catalog.py:picture_cost_usd`)
times calls per ad from the code. A text-only draw recorded 947 output tokens, about $0.03
(`docs/adr/0012-legacy/real-api-replies.md`). A 1152×2048 edit with 2 inputs was about $0.06.

| System (model) | Asked (instructions, handoff) | Why this model | In → out | What checks it | Eval | How it fails (notice Kind) | Cost per ad (est.) |
|---|---|---|---|---|---|---|---|
| **draw_person** (sunburst) | `PORTRAIT_PROMPT` + plan's gender and looks (`jobs/work.py:_who_the_person_is`). A remake puts the producer's instruction on top: "which wins where the two differ" (`jobs/work.py:_on_top`, `remake_person`) | No reason line in `catalog.py:MODEL_FOR_PURPOSE`; one legacy test | Text → 720×1280 PNG (`openai_adapter.py:OpenAIProvider.draw`) → PORTRAIT item (`jobs/work.py:create_person`) | Nothing in code. Shown in chat (`agents/producer.py:CreatePerson.run`); producer told to `look` (`agents/producer.py:INSTRUCTIONS`) | None | Error → producer tool answer "Failed: …", no notice (`agents/loop.py:why_it_failed`). Out of money → Azure, `PICTURES_ON_AZURE` (dev) (`gateway/gateway.py:_picture_made`) | ~$0.03 × 1, +1 per new look |
| **make_reference_photo_picture** (sunburst) | `jobs/reference_photos.py:clean_photo_of`: "A clean photo of only X … Never copy any person's face". X is the owner's thing (`jobs/work.py:_clean_picture`), the producer's `shows` (`agents/producer.py:AddToLibrary.run`) or the agent's question text (`jobs/owner_answers.py:take_the_answer`) | "drawn as starting pictures are" (catalog comment) | Owner photo → cut-out at its shape (`jobs/cut_out.py:redraw_with_check`) → library item (`jobs/owner_answers.py:into_the_library`) | Cut-out check once per draw, ≤2 redraws (`jobs/work.py:owner_photo_check`, `jobs/checked_pictures.py:made_and_checked`). Unchecked passes. Off with QUALITY_CHECKS | None. Check policy marked "NOT APPROVED YET" (`owner_photo_check` docstring) | All fail → `ReferencePhotoUnusable`, owner asked (`jobs/work.py:_clean_picture`) or `OWNER_FILE_UNUSABLE` (owner) (`jobs/owner_answers.py:_clean_picture`). Unchecked → `OWNER_PHOTO_UNCHECKED` / `REFUSED_BY_THE_FILTER` (dev) | 0 unless owner photos; ~$0.05–0.15 per photo |
| **make_picture** (sunburst) | The producer's whole prompt, references as "pictures 1, 2, 3" (`agents/producer.py:MakePicture`). No fixed lines | "drawn as cut-outs are" (catalog comment) | References' product-only versions → edit, or words alone → draw (`jobs/made_pictures.py:make_picture`, `_drawn`) → library item, source `Made` | `jobs/cut_out.py:check_made_picture` once; with no reference only "shows what it should" and physics. Then `label_photo` | None | Fail or unchecked → `PictureNotMade` → producer "Failed: … make it again, or go on" (`MakePicture.run`). No notice | 0 unless called; ~$0.03–0.10 a call |
| **make_part_picture** (sunburst) | `jobs/owner_parts.py:drawn_from_its_sentence` (part's sentence beside a product photo) or `changed_as_said` (owner's words on their cleaned photo) | "as cut-outs are drawn" (catalog comment) | `_product_photo` or clean picture → part picture at photo shape → `OwnerPart` item, redrawn in place (`_kept`) | `check_part_picture` against the sentence, ≤2 redraws (`_redrawn`); unchecked passes. Owner's yes gates every clip holding it (`draw_part_picture`) | None | `PART_NOT_DRAWN` (owner) or `PART_TRIED_AGAIN` (dev) (`_say_it_failed`); `PART_CHECK_UNCHECKED` (dev) (`check_part_picture`). Asks yes with `PART_NEEDS_YES` (owner) | 0 unless an owner part; ~$0.05–0.15 per drawing, again per correction |
| **plan_before_picture** (gpt-6.1-sol) | `jobs/scenes.py:BEFORE_PICTURE_INSTRUCTIONS`. Handoff: script lines, B-roll scenes (shows, kind, usage, result), the chat (`jobs/work.py:_before_picture`) | Moved with every 5.6-sol job, "no comparison test" (`tests/test_frozen_rules.py:test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol`) | Text → `BeforePicturePlan` {prompt, prompt_reason}, structured, `pay_once` (`jobs/scenes.py:BeforePicturePlan`) | Schema only (non-empty) | Frozen pins on its words only (A19, `tests/test_frozen_rules.py`) | Raises up; the scene step fails (`agents/tasks.py:_do_the_work`) | ~$0.01–0.03 once |
| **make_before_picture** (sunburst) | The plan's prompt, verbatim (`jobs/work.py:_before_picture`) | Same picture model | Text → 720×1280 → BEFORE_PICTURE item. Never shown in chat, never exported for grading | None | None | Not wrapped in `PlaceNotDrawn`: raises and fails the step, no `PLACE_NOT_DRAWN` (`_before_picture`) | ~$0.03 once |
| **make_room_alone** (sunburst) | `jobs/scenes.py:ROOM_ALONE_PROMPT`: "Take out every other thing, and every mark, stain or mess" | "as the before-picture is" (catalog comment) | Before-picture → 1152×2048 room → ROOM_ALONE (`jobs/work.py:_draw_once`); shown once (`show_once`) | None | None | Any error → `PlaceNotDrawn` (`jobs/work.py:_drawn`) → `PLACE_NOT_DRAWN` (dev) and every scene agent's step fails (`agents/scene_agent/__init__.py:run`) | ~$0.06 once |
| **make_person_in_room** (sunburst) | `jobs/scenes.py:PERSON_IN_ROOM_PROMPT`: same face, hair, clothes; "stands straight and still … hands at their sides". Comment: "Untested" | Same | [portrait, room alone] → 1152×2048 → PERSON_IN_ROOM, once per portrait (`jobs/work.py:person_and_room`) | None. No identity check | None | `PlaceNotDrawn` → `PLACE_NOT_DRAWN` (dev); no portrait for a face scene also raises it (`person_and_room`) | ~$0.06 once, +1 per new look |
| **draw_scene_room** (sunburst) | `jobs/scenes.py:SCENE_NEW_PLACE_PROMPT` around the producer's words (`agents/producer.py:SetPlace.run` → `jobs/work.py:new_scene_place`) | Same | Text → 720×1280 SCENE_ROOM; becomes `Scene.place` (`_set_place`) | None; producer told to look (`SetPlace.run`) | None | Error → "Failed: …" tool answer (`agents/loop.py:why_it_failed`) | 0 unless set_place; ~$0.03 |
| **edit_scene_place** (sunburst) | `SCENE_PLACE_EDIT_PROMPT` with the producer's one change (`jobs/work.py:edit_scene_place`) | Same | The scene's place picture → 1152×2048 own place (`_edited_once`) | None | None | `NoPlaceYet` → refusal (`SetPlace.run`); errors → "Failed" | 0 unless set_place; ~$0.06 |
| **make_scene_person_in_room** (sunburst) | `PERSON_IN_ROOM_PROMPT` with [portrait, the scene's room] (`jobs/work.py:presenter_in_place`) | Same | → SCENE_PERSON_IN_ROOM | None | None | Inside `person_and_room` → `PlaceNotDrawn` → `PLACE_NOT_DRAWN` (dev); via set_place → "Failed" | ~$0.06 per own place per portrait |
| **swap_face** (sunburst) | `jobs/face_swap.py:SWAP_PROMPT`: "Picture 1 is {photo}. Picture 2 is a portrait … Redraw EVERY face" | "on the picture model that paints"; prompt "Tested Oct 30 on round 7's six photos" (comment) | [photo, portrait] → photo-shaped picture (`keep_shape=True`) → `FaceSwapped` item keyed by photo and portrait (`for_clip`, `_made_before`) | `check_face_swap` (sol): only "is every old face gone", never "is it the presenter" (`FACE_CHECK`, `_check`). An unchecked try fails | Six photos, once | 3 failed tries, 2 refusals or no presenter → the original photo is sent, `FACE_SWAP_FAILED` / `NO_PRESENTER_TO_SWAP` (owner, red) (`_say_it_failed`, `_say_no_presenter`). Outage → `FACE_SWAP_INTERRUPTED` (dev), raised. `FACE_SWAP_UNCHECKED` (dev) (`_check`) | Per picked photo with a stranger's face, per portrait: ~$0.06–0.18 |

**Fixed cost per ad from this group: about $0.20** (portrait, before-plan, before-picture, room
alone, person-in-room). Everything else is per owner photo, part, place or face, and is 0 on a
plain ad.

**Where they feed the scene agent** (taught in PR #324; only the seam here):
- The draw sends up to 15 picks, then the shared person-in-room or room alone **last**, with
  the line "keep the same look and light" (`agents/scene_agent/draw_starting_picture.py:make_picture`).
- Each pick passes through cut-out, face swap, then recolour first (`draw_starting_picture.py:swapped`).
- The picture check gets the same files, labelled only by the agent's "take" line
  (`agents/scene_agent/picture_check.py:check`).

---

## How it is tested today

**Through the chat, every model faked at the gateway:**
- `tests/test_person_in_room.py` (10):
  - the shared room and person are drawn once, from the right inputs in the right order;
  - each is shown once;
  - a worker stop pays nothing twice;
  - a room that can't be drawn fails all four scene agents, with a developer notice only.
- `tests/test_producer_person.py` (2) and `tests/test_producer_person_and_room.py` (11):
  - create and remake the person, with the instruction on top;
  - set_place, new or edited;
  - a new person is drawn again in each place.
- `tests/test_scene_face_swap.py` (10):
  - the swap happens before the draw and is reused;
  - each failure mode sends the original with one red notice;
  - a new presenter gets a new swap;
  - replay after a stop.
- `tests/test_make_picture.py` (12). `tests/test_owner_parts.py` (25),
  `tests/test_producer_owner_part.py` (22), `tests/test_owner_part_picture.py` (3),
  `tests/test_owner_part_library.py` (10). `tests/test_owner_answer.py` (7).
  `tests/test_pictures_on_azure.py` (10).
- `tests/test_frozen_rules.py`: word pins on `BEFORE_PICTURE_INSTRUCTIONS` (A19) and model pins.

**What the fake is:** `gateway/fake.py:FakeModel.edit` returns a 72×128 plain-colour picture,
whatever size was asked; `FakeModel.draw` a tan 72×128.

**What these tests can't see:**
- Anything a picture shows. The tests prove wiring, order, replay and notices, never content.
- Identity drift from portrait → person-in-room → starting picture → H3.
- That the room alone wipes out the before-picture's things and problems. The test pins the
  wiping words as correct (`test_the_room_alone_is_drawn_from_the_drawn_room_with_only_the_room_in_it`).
- The person-in-room pose leaking into talking-scene pictures.
- Whether a swapped face looks like the presenter.
- Real cost and the 20 pictures/min OpenAI limit (`docs/rate-limits.md`) with four agents at once.
- The safety filter's two stages. The tests script `BlockedBySafetyFilter("refused")`, which is an
  input-stage refusal (`gateway/types.py:BlockedBySafetyFilter`).
- No test covers a refused before-picture draw, or a scene with its own place when the ad's
  room can't be drawn (see Bugs).
- No eval in `backend/evals/` measures any of these 12 systems.

---

## Concepts, ranked by impact on this build

### 1. Cross-shot continuity from a shared anchor · **must apply now**

**What it is**
- Film continuity: the same objects, stains and states across shots.
- Generative pipelines get it by giving every shot the same reference picture for each
  shared subject and location.
- AdForge example: the stained shirt in the "problem" scene and the clean one in the
  "result" scene must be one shirt.

**Why it matters here**
- `jobs/work.py:_before_picture` plans and draws "each thing the scenes act on and every
  problem". Its own docstring says why: "drawn alone, each got its own toilet and its own
  stains (round 3 #8 s3 FAIL)".
- `jobs/scenes.py:ROOM_ALONE_PROMPT` then takes out "every other thing, and every mark, stain
  or mess". Scene agents get only the room alone or the person-in-room
  (`jobs/work.py:scene_place` → `agents/scene_agent/draw_starting_picture.py:make_picture`).
- So the before-picture reaches no scene. It isn't shown in chat or exported for grading. Its
  A19 rule is pinned in `tests/test_frozen_rules.py`, but what it draws is thrown away.
- Spec #303: "Agents never see each other's pictures."
- CONTEXT "Second state" promises the second clip "can be made to match the first's", but
  `second_state` is never in the facts a scene agent gets (`agents/scene_agent/media.py:_scene_facts`).
- Under ADR 0012, talking scenes act on things too, but the before-picture's handoff takes
  only scenes with a B-roll kind (`job.scenes.exclude(broll_kind="")` in `_before_picture`),
  which a talking scene normally has blank (`jobs/planning.py:ScriptScene.broll_details`).
- Result: the round-3 failure is possible again in every problem → solve and two-state ad.

**How to apply it in this build** (Julian's call)
- List the ad's shared things: each object two or more scenes act on, with its before state.
- Pick: send the before-picture as a second shared picture to scenes whose `shows` name a
  thing in it ("shows the problem", "does a job"). This is the cheapest change: it is already
  drawn and paid for.
- For a second-state scene: give its agent the first scene's passed starting picture, so the
  pair is drawn in order, not at once.
- Pin the outcome, not words: a chat-flow test that a problem scene's draw is sent the
  shared-thing picture. Then Julian grades one problem/solve ad.

**One real post:** Runway, "Introducing Runway Gen-4": consistent characters, locations and
objects across scenes from reference images. https://runway.com/research/introducing-runway-gen-4

### 2. Instruction hierarchy (code's rules outrank free text) · **must apply now**

**What it is**
- Several writers fill one prompt: code's fixed rules, the planner, the producer, the owner.
- Decide which may override which. Put the rules nobody may override where nothing can beat them.
- OpenAI trains its models to rank system over user over tool text for the same reason.

**Why it matters here**
- `jobs/work.py:ON_TOP` adds the producer's instruction "which wins where the two differ"
  (marked "Not approved yet").
- `PORTRAIT_PROMPT`'s "Not a real, famous person. No text, logos or products" are the lines an
  owner's wish contradicts ("have her hold the bottle").
- The first runs hit this failure in 22 of the 25 files in `docs/runs/first-run-findings/`
  (e.g. `12-smartish-gripmunk-case.md`, `19-titan-foam-mat.md`): "prompt says no products, then
  asks for the product", and the picture model drew the product. The other 3 have no portrait
  finding. All of these came through the planner's `person_looks`, not `ON_TOP`, and before the
  fix below. No recorded run since shows a prop in a portrait.
- The fix was a field description, "Only the person: no product, props or animals"
  (`jobs/planning.py:Plan.person_looks`). That is a prompt, the weakest level in
  `docs/what-makes-a-good-ad.md`.
- A remake gets no such guard. The `looks` field of `remake_person` in `agents/producer.py`
  replaces the plan's text "in full" with no "only the person" line. Its `instruction` then goes
  on top through `ON_TOP`.
- Nothing checks the portrait that comes back. Every talking scene's person-in-room picture and
  every face swap is drawn from it.
- `agents/producer.py:MakePicture`: the producer's prompt is the whole prompt, with no fixed
  lines at all.

**How to apply it in this build** (Julian's call)
- Flip `ON_TOP`: the instruction first, then "These always hold, whatever is asked above: …".
- Keep each purpose's invariants as one constant, pinned in `tests/test_frozen_rules.py`.
- Check the portrait once: reuse `jobs/cut_out.py:check_made_picture` with no references,
  asking "one person only, no product, text or logo". About $0.01 on sol.

**One real post:** Wallace et al. (OpenAI), "The Instruction Hierarchy: Training LLMs to
Prioritize Privileged Instructions". https://arxiv.org/abs/2404.13208

### 3. Generation loss across chained edits · good to know (retagged by the independent reader)

**What it is**
- Every redraw is a lossy copy. Faces, print and small parts drift a little each pass, and the
  drift adds up.
- The fix has three parts: start every hop from the earliest picture that does the job,
  restate what must stay at every hop, and check the end result against the original.

**Why it matters here.** The chains before H3:
- **Person:**
  - text → portrait at 720×1280 (`gateway/openai_adapter.py:OpenAIProvider.draw`)
    → person-in-room (`jobs/work.py:person_and_room`)
    → starting picture → H3.
  - In the draw, the person-in-room is the last of up to 16 inputs, and its only line is "keep
    the same look and light" (`agents/scene_agent/draw_starting_picture.py:make_picture`).
- **Room:** text → before-picture → room alone → person-in-room → starting picture.
- **Product photo:** cut-out or face swap → recolour → starting picture (`draw_starting_picture.py:swapped`).
- No hop is checked against the picture the chain started from:
  - The cut-out is checked against its photo, "alone and unchanged" (`jobs/cut_out.py:check_cut_out`).
  - The face swap is checked only for whether the old face is gone (`jobs/face_swap.py:_check`).
  - The paint isn't checked.
  - The starting picture's check compares the product against the edited copies
    (`agents/scene_agent/picture_check.py:check`).
  - Nothing checks the person-in-room picture.
- Nothing compares the presenter in a starting picture with the portrait. The picture check
  asks about the product and physics only (`agents/scene_agent/picture_check.py:CHECK_INSTRUCTIONS`).
- OpenAI's image guide lists among its limitations: "may occasionally struggle to maintain
  visual consistency for recurring characters".

**How to apply it in this build**
- Measure first, for free: put the portrait, person-in-room and room alone beside each
  starting picture in the round export (`evals/round_export.py`), so Julian can see where
  drift starts.
- Shorter chain (Julian's call): also send talking-scene draws the portrait, labelled "her
  face and hair, exactly".
- Restate what stays (Julian's call): the shared picture's line names "same face, hair, skin
  tone and clothes".
- Input order: OpenAI's archived gpt-image-1 cookbook says the first image keeps the most
  detail. Its current docs say gpt-image-2 reads every input at high fidelity. Test the order
  rather than assume it.
- The automated end check is concept #61 (face embeddings), already taught.

**One real post:** Tang et al., "Banana100: Breaking NR-IQA Metrics by 100 Iterative Image
Replications with Nano Banana Pro". Editing models degrade over repeated edits, and 21 quality
metrics miss it. https://arxiv.org/abs/2604.03400v1

### 4. Online evaluation from production traces · good to know

**What it is**
- Measure each AI step from the records it already writes: first-try pass rate, redraws per
  kept picture, refusals by stage, unchecked share, cost per kept picture.
- Track them per round.

**Why it matters here**
- None of the 12 systems has an eval.
- The face swap was tried once on six photos (`jobs/face_swap.py:SWAP_PROMPT` comment). The
  person-in-room is "Untested" (`jobs/scenes.py:PERSON_IN_ROOM_PROMPT` comment).
- Yet every try and check is already a `ModelCall` row with purpose, outcome, cost and verdict
  (`gateway/gateway.py:_recorded`), and replays read them (`jobs/checked_pictures.py:tried_before`).

**How to apply it in this build**
- A read-only script over a round's sessions. It makes no paid calls. Per purpose: calls,
  successes, refusals (input or output), check fails per kept picture, $ per kept picture.
- Add it to the round export. Use it to decide where a check or a prompt change pays.

**One real post:** Hamel Husain, "Your AI Product Needs Evals" (log traces, track results over
time). https://hamel.dev/blog/posts/evals/

### 5. Reference leakage (a reference gives more than its job) · good to know

**What it is**
- A model given a reference copies more than you asked for: pose, framing, light, background.
- Name what each reference is for, and what not to take from it.
- AdForge already proved this for H3: "Photos are for looks only (a photo's pose gets copied)"
  (`docs/agents/proven-broll-rules.md`).

**Why it matters here**
- `jobs/scenes.py:PERSON_IN_ROOM_PROMPT` orders "stands straight and still … hands at their
  sides", with the comment "Untested: the risk that a video model copies the pose is read
  from the grades".
- Under ADR 0012 every ad opens on a talking scene, drawn from this standing-still picture. Its
  only line is "keep the same look and light" (`draw_starting_picture.py:make_picture`).
- Spec #210's Further Notes already list this as a risk to read from the grades: "the one
  drawn person-in-room picture (risk: the model copies her pose and framing)".

**How to apply it in this build** (Julian's call)
- Give the shared picture's line its job: "her face, hair and clothes, and the room; her pose
  comes from the pose box".
- In round 8's grades, look for talking pictures with arms at the sides.

**One real post:** OpenAI, "Gpt-image-1.5 Prompting Guide": "Reference each input by index and
description" and say how inputs interact (archived recipe).
https://developers.openai.com/cookbook/examples/multimodal/image-gen-1.5-prompting_guide

### 6. Fail-open vs fail-closed, decided per check · good to know

**What it is**
- When a check can't answer, either let the work through (fail open: keeps the ad moving) or
  stop it (fail closed: keeps a bad picture out).
- Decide by what a wrong picture costs against a stalled scene. Write it down per check.

**Why it matters here.** One situation, five policies:
- **Owner clean picture:** unchecked passes, with a developer notice only
  (`jobs/work.py:owner_photo_check`, "NOT APPROVED YET … never asked"). It then serves as a
  "real product" photo for the scene check.
- **Part picture:** unchecked passes; the owner's yes is the last check (`jobs/owner_parts.py:_redrawn`).
- **Face swap:** an unchecked try fails (`jobs/face_swap.py:_check`). But when every try
  fails, the stranger's real face is sent, with a red notice (`for_clip`). That is fail-open
  on the riskiest picture. #345 chose it.
- **Made picture:** unchecked → not stored (`jobs/made_pictures.py:make_picture`).
- **Scene picture check:** unchecked → filmed, owner told (`agents/scene_agent/picture_check.py:check`).

**How to apply it in this build** (Julian's call)
- One table in the docs: check → on no answer → who is told. Julian picks each row.
- The face-swap row first.

**One real post:** Teiva Harsanyi (Google), "Fail open, fail closed". Google's Safe Removal
Service fails closed when a dependency is down. https://read.thecoder.cafe/p/fail-open-fail-closed

### 7. Best-of-n sampling with a verifier · good to know

**What it is**
- Instead of draw → check → draw again, ask for several candidates in one call and let the
  check pick one.
- A redraw with the same prompt is only a new sample, so asking at once loses nothing.

**Why it matters here**
- `jobs/checked_pictures.py:made_and_checked` redraws with the exact prompt and pictures, one
  after another, up to 3 times.
- Each call sends the input pictures again.
- OpenAI's image API takes `n` ("generate multiple images at once in a single request"), and
  says complex prompts "may take up to 2 minutes".

**How to apply it in this build** (Julian's call: money)
- Only after concept 4 shows first tries fail often.
- Try `n=2` for face swaps and owner clean pictures; keep the first that passes.
- `tried_before` must record all `n` pictures.
- Check in one recorded `usage` field whether input tokens are billed once per request before
  counting on a saving.

**One real post:** Ramesh et al. (OpenAI), "Zero-Shot Text-to-Image Generation". DALL·E reranks
its samples with a contrastive model, N = 512. https://arxiv.org/abs/2102.12092

### 8. AI disclosure and content provenance (C2PA) · good to know, known

**What it is**
- Platforms label realistic AI-made people and altered photos.
- Content Credentials (C2PA metadata) let them do it automatically.

**Why it matters here**
- Every presenter is AI-made (`jobs/work.py:create_person`).
- `jobs/face_swap.py:SWAP_PROMPT` puts that face on a real model's body from the shop's photo
  ("Keep … the pose, the hands and the background").
- Known: `docs/what-makes-a-good-ad.md` chose "No reminder" to label AI content, because
  there are no real shop owners yet.

**How to apply it in this build** (Julian's call, when real owners arrive)
- Tell the owner the ad has AI-made people.
- Keep a per-ad record of which pictures are AI-made. `ModelCall` already holds it.

**One real post:** TikTok Newsroom, May 9 2024: auto-labelling AI content from other platforms
via C2PA Content Credentials.
https://newsroom.tiktok.com/en-us/partnering-with-our-industry-to-advance-ai-transparency-and-literacy

---

## Also seen here

- #1 Idempotency keys, #3 Timeouts and retry budgets:
  - `gateway/openai_adapter.py:OpenAIProvider.__init__` gives pictures a 120 s timeout, while
    OpenAI says complex prompts take up to 2 minutes.
  - A timeout is retried as `OutsideServiceDown` (`_asked`), so a long edit can be drawn twice.
  - One checked picture can be drawn up to 9 times: `jobs/checked_pictures.py:made_and_checked`
    (3 tries) over `gateway/gateway.py:_remade_past_the_filter` (3 makes each).
- #5 Failure isolation:
  - `jobs/work.py:person_and_room`: one room-alone failure fails every scene agent of the ad
    (`agents/scene_agent/__init__.py:run`).
  - The test asserts all four fail (`tests/test_person_in_room.py`).
- #9 Golden dataset:
  - No saved cases for any picture generator in `backend/evals/`.
  - The face swap's six test photos live only in a thread (`jobs/face_swap.py:SWAP_PROMPT`
    comment).
- #12 Append-only records: `jobs/owner_parts.py:_kept` redraws the part's library item in place,
  under the same number, and deletes its recolours.
- #19 Stopping conditions (no dollar cap), known: `agents/producer.py:MakePicture` can be called
  again and again; only the 100-calls cap stops it (`docs/limitations.md`).
- #23 Data provenance:
  - `jobs/library_items.py:came_from` knows "made by the producer from its own words".
  - `agents/scene_agent/draw_starting_picture.py:make_picture` passes only the agent's "take"
    line to the draw and the check.
- #43 Independent evidence for checks, known (ADR 0011 Consequences):
  - `agents/scene_agent/picture_check.py:check` judges the starting picture against the same
    inputs the draw got.
  - Those inputs may themselves be AI-made: Made, OwnerPart, FaceSwapped, cut-out or recolour.
- #46 App-side rate limiter: `gateway/gateway.py:_picture_made` paces only the Azure path. On
  OpenAI (20/min) every scene agent, swap and cut-out draws at once.
- #58 Feedback-driven retries: `jobs/checked_pictures.py:made_and_checked` redraws face swaps,
  clean pictures and part pictures with the same prompt; the check's problem is never passed
  back.
- #59 Pixel-preserving segmentation: `jobs/owner_answers.py:_clean_picture` and
  `jobs/work.py:_clean_picture`. An owner's photo always goes in as a picture-model redraw,
  never as itself.
- #61 Identity consistency check (face embeddings):
  - `jobs/face_swap.py:_check` asks only whether the old face is gone.
  - `jobs/work.py:person_and_room` and `presenter_in_place` have no identity check at all.
- #62 Human-in-the-loop approval: done right in `jobs/owner_parts.py:draw_part_picture`
  (`PART_NEEDS_YES` gate). The portrait has no such gate (`agents/producer.py:CreatePerson.run`).
- #63 Content-addressed keys:
  - Done right: `jobs/work.py:_edited_once` and `_draw_once` key paid pictures by {prompt, pictures}.
  - Weak: `jobs/work.py:create_person` keys by purpose only (see Bugs).

---

## Bugs and loose ends

From reading the code; none was run.

- **A refused before-picture can stall the whole ad.**
  - `jobs/work.py:_before_picture`: the plan is `pay_once`, so the same handoff gets the same
    prompt.
  - The draw isn't wrapped in `PlaceNotDrawn`, so a refused prompt fails every scene step
    each time, with no `PLACE_NOT_DRAWN` notice.
  - Nothing redraws it. The producer has no tool for it.
- **A scene with its own place still fails when the ad's room can't be drawn.**
  `jobs/work.py:person_and_room` draws the ad's room alone before it looks at `scene.place`,
  so `set_place` can't route around the failure.
- **Shared pictures are missing from grading.** `evals/round_export.py` exports
  `start-picture.png` per scene, but not the portrait, person-in-room, room alone or
  before-picture. The before-picture is also never shown in chat (`jobs/work.py:show_once` is
  given only the person and the room).
- **Ignored arguments.** `jobs/work.py:scene_place` ignores its `person_shown` argument and
  uses `scene.kind`. `agents/scene_agent/__init__.py:run` overwrites `step.person_shown` on the
  next line.
- **Weak replay key.** `jobs/work.py:create_person` reuses any earlier paid `draw_person` call by
  purpose alone. If the plan's person changed between a paid-but-unkept draw and the rerun, the
  old look is kept.
- **A cut-out from an answer has a vague target.** `jobs/owner_answers.py:take_the_answer`
  asks the picture model to keep "the thing the photo shows that answers this question:
  <question>". The model and the check must guess which object that is.
- **`plan_before_picture` gets the wrong scene list.** It runs even for an ad with no B-roll
  scene (`broll_scenes=[]`), and it leaves out talking scenes that now act on things
  (`jobs/work.py:_before_picture`).
- **Two picture prompts were never approved.** `ON_TOP` is marked "Not approved yet"
  (`jobs/work.py`), and so is `owner_photo_check`: "NOT APPROVED YET (audit item 24: never
  asked)".
- **Stale comments and purposes.**
  - In `gateway/catalog.py:MODEL_FOR_PURPOSE`, the comment above `make_starting_picture`
    describes the retired B-roll prompt writer.
  - `make_starting_picture` and `check_starting_picture` have no live caller. Only
    `gateway/fake.py`, `broll-test/` and `evals/calibrate_picture_check.py` still use them.
- **Test fake:** `gateway/fake.py:FakeModel.edit` fails any edit whose own purpose has no
  script when the retired `make_starting_picture` has one. A test can fail an unrelated edit.
- **Docstring and code disagree:** `gateway/gateway.py:_say_pictures_moved_to_azure` says the
  owner is told in red. Its kind `PICTURES_ON_AZURE` is a developer kind
  (`jobs/notices.py:post_pictures_moved_to_azure`).
- **Doc and code disagree:** CONTEXT "Before-picture" says the room alone is drawn from it "so
  the scenes match". `ROOM_ALONE_PROMPT` keeps only the walls, floor, surfaces and fittings.

---

## Checked by an independent reader

Checked against the code at `fd346df` (paths under `backend/`), reading only.

- Cross-shot continuity from a shared anchor · CONFIRMED · The before-picture is used in one
  place only: `jobs/work.py:person_and_room` feeds it to `ROOM_ALONE_PROMPT`. A scene's draw
  gets only the room alone or the person-in-room (`jobs/work.py:scene_place`). `_scene_facts`
  has no `second_state`. The handoff filters on `broll_kind`, and `round_export` exports only
  `start-picture.png`. Spec #303 has the "never see each other's pictures" line. Nothing
  changed.
- Instruction hierarchy · CORRECTED · `ON_TOP`, its "which wins" wording and `MakePicture`
  having no fixed lines all hold. Fixed three points:
  - The leak count was "about a dozen". It is 22 of the 25 first-run findings files, and all
    came through `person_looks` before the field fix, not through `ON_TOP`.
  - "Every scene is drawn from it" now reads talking scenes (through the person-in-room) and
    face swaps. B-roll scenes get the room alone.
  - Added that `remake_person`'s `looks` field has no "only the person" guard.

  Tag stays "must apply now": the remake path is live and unguarded.
- Generation loss across chained edits · CORRECTED, retagged good to know · The chain, the "last
  of up to 16 inputs" and "keep the same look and light" all hold, and no identity check exists.
  But "Only the face swap is checked" was wrong: the cut-out is checked as "alone and unchanged",
  and the starting picture's check compares the product against the edited copies. That line
  now lists each hop's check. Retagged because no graded run on this chain shows the presenter
  drifting, and the concept's own first step is to measure.

Cite check: every `file:symbol` cite in the concepts and the table names a file that exists and
a symbol in it. Fixed:
- Table, draw_person row: "`agents/producer.py` PRODUCER text" is now
  `agents/producer.py:INSTRUCTIONS`. `PRODUCER` is the `Agent` object; the "look at a new
  portrait" line is in `INSTRUCTIONS`.
- Concept 2: "`jobs/planning.py` `person_looks`" is now `jobs/planning.py:Plan.person_looks`.
  The field is on `Plan`, not `ScriptScene`.
