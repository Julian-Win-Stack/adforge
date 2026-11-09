---
name: grading-page
description: "Build Julian's grading page for a finished test round of ads on the Azure VM: every ad, scene and try, what went to the image model and what went to the video model shown apart, the prior round beside it, a box under every version."
disable-model-invocation: true
---

# Grading page

After a test round, Julian grades every ad on one page. Build it without asking him anything. Only he grades: the page shows records, never a verdict of yours.

The scripts in `scripts/` built the round 8 page (Nov 3 2026), which he liked. They are a working reference, not a package. Copy them into the run folder on the VM and change what a round needs (step 1). `backend/evals/grading_page.py` is the old round 7 layout. Don't build from it, because its `_verdict` shows "not run" for unsure.

## What the page must show

These are his rules from his earlier rounds. Each one came from a complaint.

1. **Everything, up front, unasked** ("Literally everything. Show me everything."). Having to ask for a piece is the complaint to avoid.
2. **Per try, "Sent to the video model (H3)":** only what H3 itself got, read from H3's own call record (the `make_broll_clip` model call's handoff): starting picture, end picture, example pictures, reference videos, exact prompt, mode (start-frame or reference) and settings. Never list the photos behind the starting picture here: they went to the image model, not H3 (Nov 9: "I don't know which things are actually being sent"). Use full resolution, never "web size", and let him click an image to enlarge it.
3. **Every try in order,** first to kept. For each fix, show what changed and why, and fold the old prompt.
4. **Per scene:** each starting and end picture with the picture check's two answers and, under it, "Sent to the image model": every picture that image call got, in order, with its line, plus the image prompt, read from the image model's own call record (`draw_scene_picture` / `make_end_picture` handoff, matched to the picture by its output file). Then the photos the scene agent looked at, every step it took, and its give-up reason.
5. **The judge:** a plain summary of why it failed a try or was unsure, at the top, marked "summary from records, not a grade". Fold the judge's full text. Show "judge: unsure" for unsure or can't tell, never "not run".
6. **Boxes:** a Pass/Fail tap and a words box under every version, every scene and the whole ad. Add a "Your thoughts" box for the round. They autosave as he types, with no Save button, and keep a local draft that shows a warning if a save fails. Leave out cause or "first step that broke" taps. He writes long notes, often one box for several tries, so the tap stays optional.
7. **Every ad:** the product link, a Langfuse trace link, cost, the whole-ad video, and the ad's key before every scene number ("n01-peace-out-pore-strips · Scene 2"). Include ads with no B-roll. If an ad ran twice, show only the newer session.
8. **The prior round beside it:** the old whole ad side by side at the top, then a folded block with the old ad's scenes, clips and his old words. Scenes rarely line up one to one, so don't force a match.
9. **A "Start here" view:** a grade-these-first list (give-ups, then judge fails, then unsure), red warnings, and every answer given as the shop owner during the run.
10. **Folded at the bottom of each ad:** library, person and room, music, page videos, costs, all warnings, the full chat and every producer step.
11. One ad at a time, with a list of ads down the side. No sideways scroll at phone width.

## Steps

All of this runs on the Azure VM (`docs/azure.md`), from a folder outside the repo, for example `~/runs/<round>/page/`. The round's `ads.json` comes from `scripts/ad-runs/run.py`.

1. **Set the round.** In the copied scripts, replace the round 8 names with this round's: `R8`/`R7` and `r8`/`r7` in `extract.py`, `build.py` and `page.js`; the "Round 8"/"Round 7" titles; the `r8-drafts`/`r8-view` storage keys. Then check for one-off reruns, like round 7's `n04 … (picker fix)` session and its `-picker-fix` folder, and for the hardcoded "17.8 MB" note in `build.py`. Set `LANGFUSE_SESSIONS_URL` to the Langfuse project's address ending in `/sessions/`.
2. **Export the records.** From `/srv/adforge`:
   ```
   docker cp ~/runs/<round>/ads.json adforge-backend-1:/tmp/r8-ads.json
   docker compose -f deploy/compose.yml exec -T backend python manage.py shell < extract.py
   docker cp adforge-backend-1:/tmp/r8-data.json data.json
   docker cp adforge-backend-1:/tmp/r8-files.json files.json
   ```
   Pipe the script in with `<`. Look for its `OK` line among the app's start-up lines.
3. **The prior round's grades:** `ArtifactData` list, collection `grades`, `query.limit` 200, `out_dir` `r7grades`. `build.py` reads `r7grades/grades/<doc id>.json`. Also save them to `/mnt/project-files/runs/<prior round>/julian-grades/` if they aren't there yet.
4. **Summaries.** List every try that the judge failed or was unsure on, or that has a try before it. Split the list between two subagents. They write `sum-out-1.json` and `sum-out-2.json`, each `{"<clip pk>": {"why": …, "changed": …}}`.
   - `why` is 1–2 plain sentences on what the judge said went wrong or couldn't tell.
   - `changed` is 1–3 sentences comparing the prompt with the try before, and whether the starting picture changed.
   - Records only: a subagent hasn't seen the video, so it gives no opinion on quality.
   - Fields per clip: pk, ad, scene, line, verdict, checks, failures (check, problems, `answer.fault`, `answer.fault_frame_times`), `answers.product[].references_never_show`, `detail_not_shown`, previous and current prompt, whether the starting picture is the same, the film call's `reason`, and the previous try's failures.
   - Save the script that builds this list beside the others.
5. **Build:** `python3 build.py test`. It writes `grading.html`, `test.html` (local media paths) and `need.json`.
6. **Media out of the container.** Put `need.json`'s assets and files in `list.txt`, then:
   ```
   docker cp list.txt adforge-backend-1:/tmp/r8-list.txt
   docker exec adforge-backend-1 sh -c 'cd /app/media && tar cf - -T /tmp/r8-list.txt' | tar xf - -C media --one-top-level=raw
   ```
   Copy each file to `media/<hashed name>`, and each `files` key also to `m/<hashed name>`. The hashed name is the first 14 hex characters of the key's SHA-1 plus its lowercase extension. Use real copies, because uploads refuse hard links. Then delete `media/raw`.
7. **Fit the limits.** Budget the media from `files.json` before uploading:
   - **Asset store:** 1 GiB and 5,000 files per artifact. Round 8 alone was 1.6 GB. Leave out talking clips (each talking scene gets a button that plays it inside the whole ad), the judge's frame sheets and separate voice audio.
   - **Published `files`:** 256 MB per version, and at most 64 MB and 255 files per publish. Library, portrait, person, before picture and music go here.
   - **15 MB per file:** re-encode inside the backend container, which has ffmpeg (`ffmpeg -v error -y -i <in> -c:v libx264 -preset slow -crf 20 -maxrate 5400k -bufsize 8000k -c:a copy -movflags +faststart <out>`), and say so on that ad. A page video over the limit gets its link instead.
   - **`.m4a` isn't served:** publish the same bytes as `.mp4` under `m2/`, which `build.py` handles.
8. **Check it offline** with `browser_test.js` in a throwaway Playwright container (commands below). Change the ad keys it clicks to this round's.
9. **Publish, then upload.** Publish `grading.html` first with `icon` "checklist" and `capabilities: {"db": {}, "assets": {}}`. Run `python3 prepare_media.py`, then have ONE subagent do every upload, so hundreds of result lines stay out of your context:
   - asset batches: `url`, `asset: true`, `file_paths`
   - prior-round videos: `asset: true`, `from_url`, up to 10 `asset_ids` (they get new ids)
   - `files` batches: `url`, `file_path` grading.html, `root`, `files` as a list
10. **Map and republish:** `python3 map_uploads.py <transcript.jsonl> <prior-ids.json>`, rename `prior-map.json` to `r7-map.json`, run `python3 build.py test` again, and republish with the same `url`, leaving out `capabilities`.
11. **Check:** every media key in the page's JSON resolves (0 missing). Write a `probe` doc to `grades`, read it back and delete it.
12. **Report** the link in one short reply, with what you tested and what you didn't. Video playback can't be tested on the VM, so ask him to tap one video. Point him to "Start here".

Offline browser check (the install is the only step with network):
```
mkdir -p pw
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v "$PWD/pw":/p -w /p mcr.microsoft.com/playwright:v1.49.0-noble \
  sh -c 'npm init -y >/dev/null; PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm i playwright@1.49.0 --silent'
docker run --rm --network none -u $(id -u):$(id -g) -e HOME=/tmp -e NODE_PATH=/p/node_modules \
  -v "$PWD":/w -v "$PWD/pw":/p -w /w mcr.microsoft.com/playwright:v1.49.0-noble node browser_test.js
```

## Grades in the page's database

Collection `grades`, one doc per box, shaped `{"grade": "pass"|"fail"|null, "note": "...", "at": "<ISO time>"}`.

| Doc id | Box |
|---|---|
| `round__notes` | Whole round |
| `<key>__whole` | Whole ad |
| `<key>__scene-<n>` | Scene, all its tries |
| `<key>__clip-<ProducedItem pk>` | One version of a clip |

Round 7's page used `<ad>__whole-ad`, `<ad>__scene-N`, `<ad>__scene-N__kept` and `<ad>__scene-N__remake-K`.

When he has graded, read the collection back and save it to `/mnt/project-files/runs/<round>/julian-grades/` (grades.json and grades.md, his exact words, each complaint its own item), without asking. Negative words count as a fail, and no feedback counts as a pass.

## Known gaps to fix when you next use it

- The scripts hardcode the round names (step 1). Turn them into arguments as you go.
- `prepare_media.py` and `map_uploads.py` were assembled from inline commands and only syntax-checked. `build.py`'s `LANGFUSE_SESSIONS_URL` read hasn't been run yet either.
- Video playback was never tested. A container with real Chrome (`channel: "chrome"`) can decode H264.
- `map_uploads.py` reads upload ids from the session transcript with regexes matching the tool's Nov 2026 wording.
