# Plan: #66 — B-roll scenes, and Boreal for every clip

Builds on the branch `27-captions-music-overlays`. The decisions behind this plan, with the
reasons, are in `docs/learning/b-roll-decisions.md` (git-ignored). The research is in
`docs/learning/b-roll-research.md`.

A **B-roll scene** is a scene whose clip isn't the person talking to camera: the product
being used, a pan sizzling, a car on a road. The person's voice still says the scene's line
over it. Every other scene is a **talking scene**, as today.

## Decisions already made

- **The planner decides each scene's kind**, once, in the plan. It is stored in one new
  column, `Scene.shows`: blank for a talking scene, a plain-words description of what the
  scene shows for a B-roll scene ("a hand pours the sauce over a bowl of noodles"). No tool
  takes a kind: every tool reads `scene.shows`, so a B-roll picture can never be sent to be
  made into a talking clip by mistake.
- **The same tools make both kinds.** No new producer tools.
- **Scene 1 is always a talking scene**, checked by code when the plan comes back. No rule
  for the last scene, and no limit on B-roll scenes in a row.
- **How much B-roll is a guide, not a rule.** The planner's instructions carry a table of
  B-roll share by kind of product. It may go against it if its reason says why.
- **B-roll only shows what is supported**: by the page text, the product photos, or the
  shop owner. When a B-roll scene would need something they don't show, the planner asks
  the shop owner, who can attach a photo, describe it, or choose something else. It never
  picks for them.
- **The fact check reads each `shows`**, like a line. A scene whose `shows` hasn't passed
  can't be rendered: the scene tools already refuse a scene that isn't `fact_checked`.
- **Boreal makes every clip**, through fal (`creatify/boreal`), at 720p. HeyGen is removed.
  The user tested Boreal as a talking model: it keeps our voice audio as given (noted on #68).
- **The shop owner isn't told which scenes are B-roll.**
- **B-roll may show the person or not.** If it does, it's the same person.
- **Known limits, written in the spec:** nothing checks that a B-roll clip matches its
  description, or that a person in it looks like the ad's person. Both wait for the critic.
- **The fact check of `shows` also sees the product photos.** A look only a photo shows
  (a swatch of the cream) supports a `shows`. This changes the spec's "planning checks look
  only at text". Agreed on 2026-09-26.
- **The B-roll picture and motion prompts must not claim or make up anything.** The model
  that writes them is told so, and the tool adds a fixed sentence to both prompts itself,
  so the rule is in every prompt sent, whatever the model writes (as `create_music` adds
  "instrumental"). Agreed on 2026-09-26.
- **Footage from the shop owner comes later.** In this work they can attach a photo
  (it becomes a product photo through `use_photos`) or describe what's missing. Using a
  video they send as a B-roll clip is a separate piece of work.
- **The product's name is said in at least one talking scene.** Not a ban on saying it
  in B-roll: it may be said there too. The plan gives `product_name`, the name as the ad
  says it (often the brand or a short name, as the page states it). Enforced twice: the
  planner's instructions say it, and code rejects a script with no talking line that
  contains the name (ignoring case). The code check is on the plan only. Later rewrites
  and shortening aren't checked: losing the only on-camera mention there is rare, and
  handling it isn't worth the extra rules. Agreed on 2026-09-26.
- **Evals come later.** Not in this work.
- **Blocked tickets don't stop the build.**

## Needs the user's OK before building

Proposed by Claude, not yet confirmed:

1. **Spec first** (Phase 0), then the code.
2. **A B-roll scene goes picture → audio → transcript → clip**, like a talking scene. The
   audio and transcript are needed for the captions and the cuts.
3. **The B-roll starting picture** is made from a product photo and the portrait; the
   person appears only if `shows` needs them.
4. **The B-roll motion prompt** ("oil sizzles, a spatula stirs") is written by the same
   model call that writes the picture prompt, and stored on the step.
5. **Clip length:** Boreal is asked for exactly the line's audio length (at least 1 s),
   with no sound, so none of the clip's motion is cut away. The voice is then laid over the clip and the clip cut to the
   audio's length, **inside the clip step**, so a stored clip always carries its voice and
   lasts as long as its audio, whatever its kind. Assembly doesn't change.
6. **A B-roll line takes at most 18 s to say** (Boreal makes up to 20 s; 2 s to spare),
   checked with the voice's measured speed in the length fit.
7. **Captions and overlays are unchanged**; the B-roll picture keeps the product out of the
   top and bottom text bands.
8. **One look:** B-roll is asked for in the portrait's setting and light, with a phone look.
9. **A `shows` that fails the fact check** is rewritten with the line, twice, then the shop
   owner is asked. They get a third choice besides keeping it or giving their own line:
   **make it a talking scene** (clear `shows`).
10. **Old scenes** have a blank `shows`, so they stay talking scenes.

## Phases

Each phase ends with the whole test suite green. Tests go through the chat, as the others
do. Only the outside services are faked.

---

### Phase 0 — The spec and the tickets

**Why first:** the repo's rule is that a decision goes into the spec before the code.

1. **Spec (#1)**, once the user says go:
   - *What the producer can do*: plan gives each scene an optional `shows`; the starting
     picture and clip tools make a B-roll scene when it has one.
   - *Planning checks*: the fact check reads `shows`; scene 1 is talking; a B-roll line
     is at most 18 s; the "make it a talking scene" choice.
   - *AI services*: the clip row becomes Boreal on fal at 720p, for both kinds.
   - *Assembly*: a clip always carries its voice; B-roll gets the voice laid over it.
   - *Quality checks*: the two known limits (done).
2. **#66**: replace "Research so far" and "Questions to settle first" with the decisions,
   and link this plan.
3. **#68**: the B-roll test is no longer needed to choose a model. Close it, or keep only
   the checks worth a paid run (the product keeping its shape while it moves).

---

### Phase 1 — Boreal in the gateway, replacing HeyGen

**Why first:** everything after it makes clips through it, and talking scenes must keep
working before B-roll is added.

1. **Check fal's model page for `creatify/boreal`** before writing code: the input fields
   (image, audio, prompt, duration, resolution), whether one endpoint does both talking
   and image-to-video or there are two, the 720p price per second, and whether it takes
   fractional seconds. Write the findings at the top of the adapter.
2. **Adapter** (`gateway/fal_adapter.py`, or a new `boreal_adapter.py` if it grows):
   - fal's **queue** API, not `fal.run`, to match `submit` → `status` → `download`:
     `POST https://queue.fal.run/{model}` returns a `request_id`; poll its status; fetch
     the result, which holds the video's URL.
   - Keep HeyGen's rule for a paid request: a reply lost after sending raises `ClipFailed`,
     never retried, so nothing is paid for twice.
   - `resolution: "720p"`, 9:16.
3. **Types** (`gateway/types.py`): `ClipHandoff` gets `audio: str | None` (none for
   B-roll) and `seconds: int | None` (how long a B-roll clip to ask for). A validator:
   exactly one of the two is set. `ClipProvider.submit` takes the same.
4. **Catalog**: `make_clip` and `collect_clip` → `creatify/boreal`, with its 720p price per
   second, and where the price came from. Remove `heygen-avatar-iv`.
5. **Gateway** (`submit_clip`): bill a talking clip on the audio's seconds and a B-roll
   clip on the seconds asked for. `_clips()` returns the Boreal provider.
6. **Remove HeyGen**: `heygen_adapter.py`, its settings and env entries. Old `ModelCall`
   records stay as they are.
7. **Fake** (`gateway/fake.py`): the fake clip provider makes a silent clip for a B-roll
   request of the length asked for, so the voice-laying in Phase 5 is tested for real.
8. **Tests**: the adapter against recorded fal replies (as the others are); a lost reply
   after a paid submit fails without asking again; the price.

---

### Phase 2 — Storing the kind

1. **`Scene.shows`**: `TextField(blank=True)`, help text: "What a B-roll scene shows, in
   plain words. Blank for a scene where the person talks to camera."
2. **`Scene.change_line(line, shows=...)`**: a new `shows` also sends a finished scene back
   to planned, as a new line does.
3. **`SceneStep.shows`**: the scene's `shows` when the step started, next to `line`.
4. **`SceneStep.motion_prompt`**: `TextField(blank=True)`, what the video model is asked
   for a B-roll clip. Blank for talking clips, which use `CLIP_MOTION_PROMPT`.
5. **`Job.product_name`**: `CharField(blank=True)`, the product's name as the ad says it,
   from the plan.
6. Migration. Show the fields in the admin.

---

### Phase 3 — The planner decides

1. **`PlannedScene.shows: str | None`** (`jobs/planning.py`), with a description the model
   sees. Blank-only text counts as none.
2. **`Plan` validators**: scene 1's `shows` must be none ("The first scene is the person
   talking to camera."). `Plan.product_name` is required, and at least one talking
   scene's line must contain it, ignoring case. A broken answer fails while it's read, as
   the overlay-length check does. `Job.product_name` stores it.
3. **`PLAN_INSTRUCTIONS`** gains, in the file's plain style:
   - what a B-roll scene is, and that the voice says the line over it;
   - when to use one: for a line about how the product is used, what it does, or proof,
     and only for what the page, the photos or the shop owner support;
   - the share table by kind of product, as a guide, from `b-roll-research.md` "What this
     means for AdForge" §2, with its "only if the page says" and "never" columns;
   - no before/after of bodies or skin, no made-up screens, no invented results;
   - the picture should show what the line says while it says it;
   - a B-roll line is one short sentence;
   - when a B-roll scene would need something nobody shows or says (what the serum looks
     like out of the bottle), decide "ask": offer to attach a photo, describe it, or
     choose something else;
   - the reason also says why these scenes show the product without the person talking.
   - The photos rule changes: photos may be used for what the product looks like, for a
     scene's `shows`, still never for facts such as label text.
4. **`plan()`** (`jobs/work.py`) stores `shows`.
5. **Tests**: a plan with a B-roll scene is stored with its `shows`; a plan with B-roll as
   scene 1 is refused and asked for again; an old plan with no `shows` still works.

---

### Phase 4 — The planning checks read `shows`

1. **Fact check** (`jobs/checks.py`, `jobs/work.py`):
   - `LineToCheck` gains `shows: str | None`.
   - `FACT_CHECK_INSTRUCTIONS`: a `shows` is checked like a line. Everything it shows must
     be stated by the page or the shop owner, or shown by the photos: what
     the product is used for, with what, what it does, and any result.
   - The fact check is handed the product photos in the ad's colour.
   - A failed verdict says whether the line, the `shows`, or both are wrong.
2. **Rewrite**: `RewrittenLine` gains `shows`. `REWRITE_INSTRUCTIONS`: fix whichever failed,
   keep a B-roll scene B-roll unless nothing supported can be shown.
3. **Asking the shop owner** (`_about_line`, `RunPlanningChecks`): the question shows the
   `shows` too, in plain words, never the term "B-roll". The choices become keep, give
   their own line, or have the person say it (clears `shows`).
4. **Length fit**: a B-roll line whose measured-speed length is over 18 s goes back to be
   shortened, like a script over its target.
5. **Tests**: a `shows` the page doesn't support is rewritten, then asked about; "have the
   person say it" makes a talking scene; a B-roll line over 18 s is shortened.

---

### Phase 5 — Making a B-roll scene

1. **Starting picture** (`jobs/scenes.py`, `make_starting_picture`):
   - `BROLL_PICTURE_INSTRUCTIONS`: the picture is the first frame of a scene that shows
     `shows`. Pick the photo that suits it. Write the picture prompt: the product exactly
     as in the photo, label unchanged; the scene as described and nothing more; the
     portrait's setting and light; the person only if `shows` needs them, then the same
     person; a casual phone look; the product clear of the top and bottom bands; no text.
     Also write the motion prompt: what moves, and how, in the scene.
   - **Nothing made up**, said plainly in the instructions: show only what `shows`
     describes. No result, use, feature, texture, colour or amount that `shows` doesn't
     state; nothing that makes the product look bigger, better or more effective than
     described; no parts of the product the photos don't show.
   - **The tool adds a fixed sentence** to the picture prompt and the motion prompt
     before sending them, saying the same: show only what is described, add or change
     nothing about the product. Code adds it, so it is in every prompt sent even if the
     model leaves it out. A test checks it's there.
   - `BrollPictureChoice` = `StartingPictureChoice` + `motion_prompt` + its reason.
   - The step stores `shows` and `motion_prompt` with the rest.
2. **Stale-picture checks** (`MakeClip`, `MakeStartingPicture`): a picture made for an
   earlier `shows` is out of date, like one made for an earlier line.
3. **Clip** (`make_clip`, `_clip_asked_for`):
   - Talking scene: as today, through Boreal.
   - B-roll scene: submit the picture, the step's motion prompt, and
     `max(audio.seconds, 1)` seconds, no audio.
   - `_clip_asked_for` matches on the handoff actually sent, not on `CLIP_MOTION_PROMPT`.
   - After collecting a B-roll clip: if it's shorter than the audio, fail the step with a
     clear reason. Otherwise lay the voice over it and cut it to the audio's length with
     ffmpeg (a new function in `jobs/assembly.py`), and keep that as the clip.
4. **Producer** (`agents/producer.py` `INSTRUCTIONS`): some scenes show the product rather
   than the person talking; the tools and their order are the same; never tell the shop
   owner which scenes are which.
5. **Tests**: a whole ad with one B-roll scene, through the chat, with the fake services:
   the B-roll clip is asked for with no audio and the right seconds; the kept clip has
   the voice and the audio's length; the finished ad's captions cover the B-roll scene; a
   Boreal clip shorter than the audio fails the step; a restart mid-clip pays nothing
   twice.

---

### Phase 6 — One real ad

1. Make one real ad for a product that should get B-roll (a pan, a food product), with the
   quality checks off, as the first run was.
2. Check by eye: the B-roll scene shows what its `shows` says, the product keeps its shape
   and label, the voice lines up, captions and the overlay read well at 720p.
3. Write what was found on #66, with the cost.

## Failures to design for

From #66, with where each is handled:

| Failure | Handled by |
|---|---|
| The product changes shape, or the logo bends | Not caught until the critic (known limit); checked by eye in Phase 6 |
| The clip comes back shorter than the voice | Phase 5: the step fails with a clear reason |
| The clip comes back with sound we didn't ask for | Phase 5: the voice laid over replaces the clip's sound |
| The reply to a paid submit is lost | Phase 1: `ClipFailed`, never asked again |
| The model refuses a real brand | The step fails with fal's reason, and the producer tells the shop owner |
| A B-roll picture sent to be a talking clip, or the reverse | Can't happen: the tools read `scene.shows` |
| The voice and the next scene don't line up | Phase 5: every clip lasts exactly as long as its audio, so assembly is unchanged |
