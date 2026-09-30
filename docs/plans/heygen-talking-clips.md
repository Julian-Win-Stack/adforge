# Plan: talking clips through HeyGen Avatar IV, B-roll stays on Boreal

Why: the blind-graded eval (#87, `docs/video-eval/README.md`). HeyGen passed 8 of 8 talking
clips; Boreal passed at most 3 of 8 with either prompt. B-roll on Boreal met its mark and is
not part of this job.

Order, as the repo's rules say: **save the grades → spec (#1) → code.**

## Step 0: don't lose the grades

Commit `docs/video-eval/` and `backend/video-eval/` on their own, before anything else. The
user's 44 blind grades live only there. Back up `backend/media/video-eval/` (415 MB,
git-ignored) somewhere outside the repo.

## Decisions made

1. **Two pairs of purposes in `catalog.py`**: `make_talking_clip`/`collect_talking_clip` →
   `heygen/avatar-iv`, `make_broll_clip`/`collect_broll_clip` → `creatify/boreal`.
   `make_clip`/`collect_clip` go away. This keeps the catalog's rule, "change a model here,
   nowhere else". The gateway picks the clip provider **from the model name**, so `collect_*`
   always asks the service that made the clip.
2. **HeyGen costs $0.05/s.** HeyGen's own enterprise page: Photo Avatar 0.1 credits/s at
   $0.50 a credit (read 2026-09-29), matching third-party pages. The one-off $0.035/s wallet
   reading in `docs/video-model-tests.md` is older and rougher. The price is the same at 720p
   and 1080p.
3. **A talking clip's `seconds` is the audio's length, at least 1.** HeyGen ignores it and
   makes the clip as long as the audio (within 0.05 s in the eval), so the submit-time bill,
   seconds × price, is right to within a cent. The 1-second floor stays: it is the least a
   clip handoff takes (`LEAST_CLIP_SECONDS`), and dropping it would refuse a line said in
   under a second. It costs at most 5 cents on such a line.
4. **Plain `type: image` request**, `expressiveness: low`, 9:16, 1080p: exactly what passed 8
   of 8. No photo-avatar step (untested, and one more paid call).
5. **Every ad is 1080×1920 at 25 fps.** Talking clips come from HeyGen that way; B-roll clips
   (Boreal, 720×1280, 24 fps) are scaled up 1.5× (same 9:16 shape, nothing stretched) and set
   to 25 fps when the ad is assembled. Checked with ffmpeg on 2026-09-29: joining a 1080p and
   a 720p clip fails outright; after scaling it works, but mixed rates leave a variable frame
   rate, which some upload sites handle badly.
6. **No data migration** for clips asked for under the old `make_clip` purpose. The app only
   runs locally; at worst one Boreal clip is paid for twice.

## Step 1: spec (#1)

- "AI services": talking clips → HeyGen Avatar IV (image request, `expressiveness: low`,
  9:16, 1080p, $0.05/s); B-roll clips → Boreal on fal at 720p, scaled to 1080p in the ad.
  Link #87 as the reason.
- "Quality checks": nothing new; the 44 graded clips are the critic's future answer key
  (#47).

## Step 2: skipped

No ticket, and the tickets linked to #1 are left as they are (the user's call, 2026-09-29).

## Step 3: code (test-first, `mattpocock-skills:tdd`)

1. **Settings.** `HEYGEN_API_KEY`, `HEYGEN_BASE_URL` (`https://api.heygen.com`),
   `HEYGEN_TIMEOUT_SECONDS` in `backend/adforge/settings.py`, as in
   `git show 65b2a95:backend/adforge/settings.py`. Restart the backend container so it reads
   the key from `.env` (it was started before the key was added). Never print the key.

2. **`backend/gateway/heygen_adapter.py`**, rebuilt from `git show 65b2a95:…heygen_adapter.py`
   and the working `backend/video-eval/heygen_clips.py`, with the 2026-09-29 doc changes:
   - upload picture and audio (`POST /v3/assets`), then `POST /v3/videos` (`paid=True`);
   - a lost reply after that POST raises `ClipFailed`, never retries (same rule as Boreal);
   - status: `waiting`, `pending`, `processing` → working; `completed` → `video_url`;
     `failed` → `failure_message`; a 404 whose code is **`not_found`** (was
     `video_not_found`) → failed; any other 404 re-raised;
   - `audio=None` is refused (HeyGen only makes talking clips);
   - `download` fetches `video_url` without the API key.
   Bring back the old adapter's tests (`git show 65b2a95 --stat | grep -i heygen`) and update
   them to the new reply shapes.

3. **`catalog.py`**: the two purpose pairs (decision 1) and HeyGen's price (decision 2).

4. **`gateway.py`**: `_clips(model)` picks the provider by model; `submit_clip`/`collect_clip`
   pass it the purpose's model. The test override (`use_model(fake)`) still wins for both.

5. **`jobs/work.py`**: `make_clip` picks the purpose pair from `step.shows` (the fork already
   there), and passes it to `_clip_asked_for` and `_paid_for_before`, which now hard-code
   `"make_clip"`/`"collect_clip"`. Talking `seconds` from decision 3. `_silent_clip_seconds`
   stays for B-roll.

6. **Assembly: one frame size for every clip.** `assembly.join` feeds clips straight into
   ffmpeg's `concat` filter, which fails when inputs differ in size. A HeyGen talking scene
   (1080×1920) next to a Boreal B-roll scene (720×1280) would break every mixed ad. Scale each
   part to `FRAME_WIDTH × FRAME_HEIGHT` at 25 fps before `concat` (decision 5). Test: join a
   1080p 25 fps and a 720p 24 fps clip; the ad is 1080×1920 at a constant 25 fps.

7. **`gateway/fake.py` and the existing tests**: rename the scripted purposes; add a test
   that a B-roll step asks the Boreal purpose and a talking step the HeyGen one, and that
   `collect_*` goes to the same provider that made the clip.

## Step 4: before wiring it in (`failure-brainstorm`)

Already known, check each has an answer:

- Lost reply after the paid POST: `ClipFailed`, no retry, as above.
- `video_url` expires: fetched and saved at once in `collect_clip` (already so).
- First status `waiting`: counted as working.
- HeyGen's changelog mentions 1.5× billing for burst concurrency: a job's scenes may ask for
  clips at the same time. Find out what "burst" means before a real run; if needed, cap
  talking clips to one at a time.
- A HeyGen asset upload fails after the picture uploaded but before the audio: nothing paid
  yet, so it's safe to retry.

## Step 5: prove it

- `uv run pytest` and the type checker, green.
- One real ad through the app with at least one talking and one B-roll scene: check the
  ad plays, clips join, lips match, and the job's recorded clip cost against the HeyGen
  wallet change.
- `test-review` over the new and changed tests.

## Not this job

Posting results on #87 and closing it; the tuned B-roll prompts into the app
(`backend/video-eval/tuned_prompts.py`); the critic (#47); re-running treadmill, mat and
chair to prove `d5084b0`.
