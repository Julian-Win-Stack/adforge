# Plan: B-roll on Boreal-H3 (build 1)

- Date: 2026-10-04
- Status: draft, for the user to agree. Nothing is built yet.
- The decisions behind it, with the reasons: `docs/broll-picture-logic.md` ("Decided", items
  1 to 51). This plan says only how they are built and tested. Where the two disagree, the
  logic doc wins and this plan is fixed.

## What build 1 is, in one paragraph

B-roll clips move from the old Boreal (on fal) to Boreal-H3 (on Creatify's own API). Each
B-roll scene is made one of two ways: **way 1**, a starting picture we make, sent as the
clip's first frame; or **way 3**, real shop photos sent as example pictures, named in the
prompt as "Image 1", "Image 2". The planner says more about each B-roll scene (its kind, who
is in it, how the product is used, what result it promises, which photos it needs). The
clip is no longer cut where its line ends: it plays to its end, and the next line starts
over that end (the early cut). There is **no judge** in build 1: the user grades the clips.

## Settled with the user on 2026-10-04

Questions reading the code raised, now items 32 to 40 in the logic doc:

1. Asking the shop owner (item 20) and the "Should we ask?" eval are in build 1 (item 32).
2. The one real ad at the end of build 1 needs no permission (item 33).
3. Two B-roll scenes in a row: the second plays from its start; the next talking scene
   catches up (item 34).
4. A B-roll line's length is never asked about. Too short after 2 rewrites: kept. Too long:
   shortened up to 3 times, then the scene becomes a talking scene and the job records a
   warning (item 35).
5. A line whose real audio is over 15 s is rewritten shorter, without telling the shop
   owner, before the clip is paid for (item 36).
6. Built on the current branch (item 37).
7. Every photo gets a Face note; photos the picker never saw get their own small call
   (item 38).
8. A rewritten B-roll scene is rewritten whole (item 39).
9. Way 3 needs no new tools: way 1 and way 3 are branches inside the same tools (item 40).
10. A picture made from the shop owner's words, for them to approve, comes later, not in
    build 1 (item 41, ticket #95).

## Settled with the user on 2026-10-05

Items 42 to 51 in the logic doc. Where they change a phase below, the phase is updated.

1. "Describe it in words" is not offered in build 1: the question offers "attach a photo"
   or "use without proven result" (item 42).
2. The ad's colour is the one with the most clear photos; on a tie, Photo 1's (item 43).
3. A needed photo can be any photo except one showing the product in another colour
   (item 44).
4. The main photo is picked by the existing picture chooser, for both ways (item 45).
5. The fact check also sees each B-roll scene's needed photos (item 46).
6. A line whose real audio is over 15 s is shortened up to 3 times, then becomes a talking
   scene with a warning (item 47).
7. No photo clearly shows the product: the planner asks for one (item 48).
8. The "Should we ask?" eval has 8 cases, each run 3 times (item 49).
9. No judge until the graded run; no B-roll for real shop owners until a judge exists
   (item 50, #109). Measuring left for later: #108, #110, #111 (item 51).

## Phase 0: tests before building (about $2, each needs the user's yes first)

These two tests could change the build, so they run first. Their scripts go in
`backend/broll-test/`, like the earlier tests. The exact cost is worked out and shown
before each runs.

### Test A: how long the planner's B-roll lines come out (item 24)

- **What:** the planner's instructions with the new hint ("a B-roll line has at least about
  10 words", no upper number, and "one short sentence" taken out), run 3 times on each of
  the 8 test products (2, 3, 5, 6, 7, 8, 1, 9), from the second run's saved page text and
  photos. Text out only: no pictures, no videos.
- **Measured:** each B-roll line's length two ways: the saved voice's measured speed (what
  the planning check will use), and the line actually spoken by that voice (what the clip
  gets).
- **Shown to the user:** every B-roll line with its seconds, and how many fall under 4 s or
  over 14 s (those would be rewritten). The user reads whether the lines still sound
  natural or squeezed.
- **Cost:** about 24 planner calls and some speech, roughly $1 to $2.
- **What it can change:** the "about 10 words" number, or the hint's wording.

### Test B: does the Face note match the user (items 26, 28)

- **What:** the photo picker with the new Face field, run on the second run's kept marked
  screenshots for the 8 test products.
- **Shown to the user:** a contact sheet of every photo it keeps, each marked Face yes/no.
  The user marks where they disagree.
- **What counts:** a stranger's face marked "no" is the error that matters (that photo
  would be preferred for a job). A "yes" on a photo without a face only costs a cleaner
  choice.
- **Cost:** about 8 picker calls, roughly $0.50.
- **What it can change:** the field's wording; if it can't be made reliable, a separate
  call that looks at each kept photo on its own.

## Phase 1: the spec and the tickets (needs the user's OK)

The repo's rule: a decision goes into the spec before the code.

1. **Spec (#1):** what changes in "AI services" (Boreal-H3 on Creatify for B-roll),
   "Planning checks" (B-roll lines 4 to 14 s; the fact check reads usage and result),
   "What the producer can do" (the planner's new B-roll fields; asking when a photo or the
   "how to use" is missing) and "Assembly" (the early cut). Written with the user's OK.
2. **Tickets:** one per phase below, linked to the spec, only once the user says so.
   Issue #94 is not edited or commented on.

## Phases of the build

Each phase ends with the whole test suite green. Tests are written first, through the chat
as the others are (`backend/tests/test_producer_broll.py` and friends), with the outside
services faked. No phase calls a paid service; the first paid run is in Phase 10.

**Testing focus (the user, 2026-10-05):** mostly integration tests that run each ticket's
main flow end to end through the chat; unit tests too, but only in support (a validator,
the seconds rule, a price). Phase 10 adds one faked end-to-end test of a whole ad with a
talking scene, a way 1 B-roll scene and a way 3 B-roll scene.

---

### Phase 2: Boreal-H3 in the gateway, replacing the old Boreal

**Why first:** everything after it makes B-roll clips through it.

1. **Adapter** `gateway/creatify_adapter.py`, from the working test client
   `broll-test/boreal_h3.py`:
   - Pictures are put on fal's storage first and sent as links (as the test client does).
   - `POST https://api.creatify.ai/api/boreal/` with `model: "boreal-h3"`,
     `resolution: "768p"`, `duration` (whole seconds), `aspect_ratio: "9:16"`, the prompt,
     and either `image_url` (way 1) or `reference_image_urls` (way 3).
   - `prompt_enhancement` comes from a setting, `auto` by default, so the after-build test
     with `none` changes a setting, not code.
   - Polls `GET /api/boreal/{id}/` until `done`, `failed` or `rejected`, then fetches
     `video_output`.
   - Keeps the existing rule for a paid request: a reply lost after sending raises
     `ClipFailed` and is never sent again, so nothing is paid twice.
   - Keys: `CREATIFY_API_ID` and `CREATIFY_API_KEY` in settings and `.env.example` (the
     real keys are already in `.env`, never printed).
2. **Types** (`gateway/types.py`): B-roll and talking clips now take different things, so
   B-roll gets its own handoff: a start picture **or** up to 5 example pictures (never
   both, checked), whole seconds from 5 to 15, and the prompt. The talking clip's
   `ClipHandoff` and HeyGen don't change.
3. **Gateway** (`gateway/gateway.py`): `submit_broll_clip` bills `seconds × price`.
   `collect_clip` works for both, as today.
4. **Catalog:** `make_broll_clip` and `collect_broll_clip` become `creatify/boreal-h3`, at
   $0.0792 a second (0.4 credits a second at 768p, $99 for 500 credits, API Starter,
   2026-10-03). Up to 5 example pictures are free, so nothing else is billed.
5. **Fake** (`gateway/fake.py`): makes a silent clip of exactly the seconds asked for, and
   records what was sent, so tests can check the pictures and the prompt.
6. **Removed:** `gateway/boreal_adapter.py`, the `creatify/boreal` entries, and
   `_silent_clip_seconds` in `jobs/work.py` (it was tuned to the old Boreal's frame
   counting). Old `ModelCall` records stay as they are.
7. **Until Phase 7**, B-roll scenes keep working the old way with the new model: the
   starting picture as `image_url`, the fewest whole seconds that cover the line (at least
   5), and the clip still cut to its audio. So the app works at the end of every phase.
8. **Tests:** the adapter against the real replies already saved from the tests
   (`docs/runs/second-run-review/check/*.json`); a lost reply after a paid submit fails
   without asking again; a refused task (`failed`/`rejected`) gives Creatify's reason; the
   price; a handoff with both a start picture and example pictures is refused.

---

### Phase 3: storing what the new logic needs

One migration. Each new field shown in the admin.

1. **`ProductPhoto.has_face`**: yes or no. A stranger's face in the
   photo (item 26).
2. **`Scene`**, for B-roll scenes (blank for talking ones):
   - `broll_kind`: "does a job" or "looks good" (item 12).
   - `person_shown`: "none", "a hand only" or "a face or body" (item 27).
   - `usage`: the usage fact, from the page's "how to use" (item 10).
   - `result`: the result it promises, for "does a job" (item 11).
   - `needs`: what the scene needs that the main photo can't show, each with the photos
     that show it (`[{"what": "the gel", "photos": [3, 5]}]`). Empty means way 1.
   - These are what item 22 asks build 1 to save, so the judge can be tried on its clips
     later.
3. **`SceneStep`**, on the picture step: `way` (1 or 3) and `pictures_sent`: the pictures
   in the order sent, each with its job (`[{"image": 1, "photo": 1, "job": "the bottle"}]`).
   The prompt already has a field. The clip step points at the picture step it was made
   from, as it points at the picture today (way 3 has no picture).
4. **Old scenes** have these blank: they are made as before (way 1, no person rule).

---

### Phase 4: photo notes (item 26)

1. **The picker** (`jobs/photos.py`): `PickedPhotos` gets one fixed-choice field, the marks
   of kept photos that show a stranger's face. The instructions say what counts: a face
   you could recognise; a body, a hand or an arm doesn't count. Plain code turns it into
   `has_face` on each photo it saves.
2. Photos saved any other way (the declared-photo fallback, the shop owner's attachments)
   get their own small Face-note call when saved (item 38).
3. **Tests:** a picked photo with a face is saved with `has_face` yes, one without with no;
   a fallback or attached photo gets its note from its own call.

---

### Phase 5: the planner (items 10, 12, 20, 24, 27)

1. **`PlannedScene`** (`jobs/planning.py`) gets, for B-roll scenes: `broll_kind`,
   `person_shown`, `usage`, `result` and `needs`, each with a description the model sees.
2. **Validators** (a broken plan fails while it's read, as today):
   - A B-roll scene has a kind and a person label; a talking scene has none of them.
   - A "does a job" scene has a usage fact and a result.
   - A needed photo is one of the job's photos (any colour check is the planner's: the
     instructions say never a photo of the product in another colour, item 44).
   - At most 5 pictures in all: the main photo, the needed photos, and the portrait when
     the person is "a face or body" (item 27).
3. **`PLAN_INSTRUCTIONS`:**
   - The two kinds and how to pick ("looks good" when unsure).
   - Who is in the scene, in the three labels; the person shown is always the presenter.
   - Read the usage from the page's "how to use" and plan the scene with it; for "does a
     job", write the result you can see, and the scene ends on it.
   - "A B-roll line has at least about 10 words." The old "a B-roll line is one short
     sentence" goes: the two disagree.
   - Name what the scene needs that the main photo can't show, and the photos that show it.
   - **Asking (items 20, 32):** ask, before planning, only when a
     needed photo doesn't exist or a "does a job" product's page has no "how to use". The
     question offers two answers: attach a photo, or "use without proven result", where the
     producer says how it would show the scene and the shop owner approves or changes it
     (item 42). What they type is a fact from the shop owner (ADR 0002).
     Nothing else is asked because of B-roll.
   - **No photo clearly shows the product (item 48):** ask the shop owner to attach one.
   - **The colour (item 43):** pick the colour with the most photos where the product is
     clearly seen; on a tie, Photo 1's colour.
4. **`plan()`** (`jobs/work.py`) stores the new fields.
5. **The "Should we ask?" eval** (items 21, 32, 49): the 8 cases of item 49, each with
   the right answer (ask or don't), run against the planner's instructions. Claude drafts
   them, the user approves them; each runs 3 times and is reported as a pass rate. Kept
   with the other evals, run on demand, not in the test suite (it calls a real model).
6. **Tests:** a plan's B-roll fields are stored; a "does a job" scene without a usage fact
   is refused and asked for again; a scene that would send 6 pictures is refused; an old
   plan without the fields still works.

---

### Phase 6: the planning checks (ADR 0002, item 24)

1. **Fact check** (`jobs/checks.py`): `LineToCheck` gets `usage` and `result`; the
   instructions say they are checked like "shows": what the page or the shop owner states.
   It is also shown each B-roll scene's needed photos, besides the marked ones (item 46).
2. **Rewrite** (item 39): `RewrittenScene` gives back the B-roll fields too, and they
   are checked again.
3. **B-roll line length:** `SHORTEST_BROLL_LINE_SECONDS = 4` and
   `LONGEST_BROLL_LINE_SECONDS = 14`, read with the voice's measured speed, like today's
   `LONGEST_LINE_SECONDS = 18` (which stays for talking lines).
   - Over 14 s: shortened, by the existing `shorten_line`, told the most words.
   - Under 4 s: a new `lengthen_line` (its own instructions, told the fewest words; "keep
     what the line is for; add only what the page or the shop owner states").
   - Either way only that one line. Lines in between are left alone. Nothing is asked of
     the shop owner (item 35):
     - still under 4 s after 2 lengthenings: kept;
     - still over 14 s after 3 shortenings: the scene becomes a talking scene (its B-roll
       fields cleared), and the job records a warning for us to find the cause.
4. **Tests:** a B-roll line under 4 s is lengthened and checked again; one over 14 s is
   shortened; a talking line of 16 s is left alone; a line still short after 2 tries is
   kept; one still long after 3 becomes a talking scene with a warning, and nobody is asked;
   a usage fact the page doesn't state fails the fact check.

---

### Phase 7: the picture step, way 1 and way 3 (items 8, 9, 11, 12, 14, 15, 26 to 29)

1. **Which way:** plain code. The scene's `needs` is empty: way 1. Otherwise: way 3.
2. **The main photo:** picked by the existing picture chooser from the marked photos, the
   best one for the scene (item 45), for way 1 and way 3 alike.
3. **Way 1:** the image maker gets the main photo, plus the portrait when the person is
   "a face or body". The prompt (written by the model, rules below) says to take only the
   product from the photo; the setting is described in words. The picture is the "before".
4. **Way 3:** code builds the example pictures, in order: the main photo, then for each
   need, a photo from its list without a face if there is one, otherwise the first (item
   28), then the portrait when the person is "a face or body". No picture is made.
5. **The prompt writer** (`jobs/scenes.py`, `BROLL_PICTURE_INSTRUCTIONS` rewritten): one
   model, the shared rules plus only the scene's kind's rules (item 12). It gets the line,
   `shows`, the usage fact, the result, the person label and the pictures with their jobs,
   and writes:
   - way 1: the picture prompt and the video prompt;
   - way 3: the video prompt, which names every picture by number with its one job, and
     for a photo with a face, what to ignore ("Image 2 is only for the gel's colour. Don't
     show the woman in it."). An answer that leaves a picture out fails while it's read.
   - Rules from "Prompt rules" in the logic doc: one continuous shot; the product does
     what the line claims, held as the usage fact says; the main action in the middle of
     the frame, nothing about the top and bottom; ends on the result ("does a job") or on
     the product at its best ("looks good"); only the presenter, any hand is fine; the
     product shown, not described; no "no speech, no sound, no text".
   - `NOTHING_MADE_UP` is no longer added to B-roll prompts (decided 2026-10-03).
6. **Stale steps:** a picture step made for an earlier line, "shows" or B-roll fields is
   out of date, like one made for an earlier line today.
7. **Tests:** way 1 sends the main photo and no portrait for "a hand only"; the portrait
   for "a face or body"; way 3 sends the pictures in order, a photo without a face before
   one with; a prompt that skips "Image 2" is refused; the stored step has its way, its
   pictures and its prompt.

---

### Phase 8: the clip (items 13, 23)

1. **Seconds asked for:** the fewest whole seconds that cover the line's real audio, at
   least 5.
   - The real audio is known once the line's audio is made. A B-roll line whose audio is
     over 15 s (item 36): the audio step shortens the line (fact checked again), and the
     producer is told to make its audio again. The shop owner isn't told. No clip is paid
     for until the audio fits. At most 3 shortenings; still over 15 s, the scene becomes a
     talking scene and the job records a warning (item 47).
2. **Sent:** way 1, the starting picture; way 3, the example pictures. With the prompt.
3. **Kept whole:** the clip isn't cut to its line. Its sound is replaced by the line's
   audio, followed by silence to the clip's end (`lay_voice_over` stops cutting). The
   clip's `seconds` is its real length; its audio keeps its own.
4. **Tests:** a 4.2 s line asks for 5 s, a 6.3 s line for 7 s; the kept clip lasts what
   was made, with the voice at its start; way 3's clip step needs the picture step
   finished, not a picture; a restart mid-clip pays nothing twice.

---

### Phase 9: assembly, the early cut (item 23)

1. **`Cut`** (`jobs/assembly.py`) keeps the picture and the voice apart: which part of the
   clip's picture plays and when, and which part of its sound plays and when.
2. **The rule, in `cuts()`:**
   - The voice never stops: each scene's voice starts where the last one's ends, as today.
   - A B-roll scene's picture plays whole. While it finishes, the next line has already
     started.
   - A talking scene after it skips the start of its picture by the same amount, and keeps
     all its sound, so the lips match the words.
   - Two B-roll scenes in a row (item 34): the second plays from its start, its picture a
     little behind its line, and the next talking scene skips that much more.
   - A B-roll scene that ends the ad plays its end over the music only. The music is
     already made 5 s longer than the script, which covers it.
3. **Captions** follow the voice; an **overlay** shows while its scene's picture plays.
4. **The finished ad's length** is its picture's. The planning length check doesn't
   change: the ad still lasts as long as its voice, plus at most a second of tail when it
   ends on B-roll.
5. **`join()`:** the picture parts and the voice parts are joined separately, then put
   together with the music.
6. **Old clips** (cut to their audio by the old Boreal) have nothing to overlap, so old ads
   come out the same.
7. **Tests:** with fake clips, for each of: talking then B-roll then talking; B-roll then
   B-roll then talking; ending on B-roll. Check where each picture and voice part plays,
   that the voice has no gap, and the captions' timing. One real ffmpeg test
   (`test_ffmpeg.py`) checks the joined file's length and that sound and picture line up.

---

### Phase 10: the producer, the words, and one real ad

1. **Producer** (`agents/producer.py`): the "make starting picture" and "make clip" tools'
   messages fit way 3 ("Scene 3 is ready for its clip"), and the clip tool's check reads
   the picture step, not the picture (item 40). The instructions don't change: same
   tools, same order, and the shop owner is still never told which scenes are B-roll.
2. **`CONTEXT.md`**: "Clip" (a B-roll clip isn't cut to its line), "Finished ad" (the early
   cut), "Starting picture" (way 3 has none), and "B-roll scene" (made by Boreal-H3, two
   ways).
3. **One real ad**, quality checks off, on one product (the toilet cleaner, 8: it has both
   ways and a result to end on).
   It checks that the whole thing runs end to end, not the clips' quality. No permission
   needed for this one ad (item 33).

## After building: the tests (each needs the user's yes and its exact cost)

1. **The graded run (item 30).** Full ads for the 8 test products, quality checks off. The
   user watches each B-roll clip and grades it pass or fail, writing down what went wrong
   on each fail. Clips are watched, not judged from still frames. Rough cost: $35 to $50
   for all 8 (about 3 B-roll clips an ad at about $0.40 to $0.60, plus the talking clips,
   pictures and voice); the exact figure is counted from the plans before any clip is
   paid for.
2. **The shop's people with the portrait (items 27, 28).** Scenes where a shop photo shows
   a person and the scene shows the presenter, and scenes where it shows no one. Does the
   shop's person leak in? First with `prompt_enhancement: auto`; only if they leak, again
   with `none`. If they still leak: cropping (plan B in item 28) is planned.
3. **Added text (item 29).** Watched for in every clip of the graded run, on the collagen
   and the power bank above all. Text drawn into a clip can't be taken out.
4. **Written down:** the results go into `docs/broll-picture-logic.md` ("Test results"),
   with the cost.

## Builds 2 and 3 (planned only after build 1's graded run)

- **The decision (item 30):** if more than 1 in 5 B-roll clips fail, build 2 is worth
  building. If almost all pass, the judge waits.
- **Build 2, the picture judge (items 18, 19, 21):** a model answers yes/no questions about
  the way 1 starting picture before the video is paid for. Fixed questions in code, and
  usage questions written per product from the saved usage fact, built from the user's list
  of what went wrong. A "no": remade up to 2 more times with the reason added, then the
  scene made simpler, then a talking scene with a grey notice. The shop owner is never
  asked because a picture failed. Guarded by the "Wrong no" eval: pictures the user graded
  good must not get a "no".
- **Build 3, the video judge (item 16):** watches the whole clip, many frames in order. Used
  only once it agrees with the user's grades of the clips already made; where it disagrees,
  its questions are fixed, not the grades.
- Each gets its own plan, written from what build 1's run shows.

## Failures to design for

| Failure | Handled by |
|---|---|
| The reply to a paid Creatify request is lost | Phase 2: `ClipFailed`, never sent again |
| Creatify refuses the task | Phase 2: the step fails with Creatify's reason; the producer tells the shop owner |
| A line's real audio is over 15 s | Phase 8: rewritten shorter before paying (item 36) |
| A planner B-roll line is too short or too long | Phase 6: rewritten; too short is then kept, too long becomes a talking scene with a warning |
| A needed photo doesn't exist | Phase 5: the shop owner is asked at planning, before any picture |
| The shop's person shows up in the video | Not caught in build 1; the after-build test 2, then plan B |
| Printed text drawn into a clip | Not caught in build 1; watched for in the graded run |
| The lips don't match after a B-roll scene | Phase 9: the talking clip's picture is skipped by the overlap, its sound kept |
| The clip ends badly, product wrong, no result shown | Not caught in build 1: the user's grades, then builds 2 and 3 |
