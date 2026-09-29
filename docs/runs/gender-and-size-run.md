# Second run: the person's gender and the product's size

Run on 2026-09-28, after the two fixes that came out of the first run's review
([first-run.md](first-run.md) problems 1 and 2; trace findings 2 and 8 in
[first-run-trace-findings.md](first-run-trace-findings.md)). Spec: #1, "Plan" step 4. Tickets:
#84 (gender) and #85 (size).

## What changed

- **The plan says whether the person is a man or a woman** (`person_gender`, required, one of
  two values). Code puts it into the portrait's prompt ("The person: a woman. …") and in front of
  the voice's description ("A woman's voice. …"). Before, 19 of the 29 planned jobs in the local DB
  had a voice description with no gender word, and Inworld picked one at random.
- **The plan says how big the product is** (`product_size`, required: tiny, handheld or large).
  Code owns one pose sentence per size (`POSES` in `backend/jobs/scenes.py`) and hands the same
  sentence to the model that plans every talking scene's starting picture (a `pose` field, which
  replaces the old instruction "held at chest height") and to the video model as the talking
  scene's motion prompt. Before, every talking scene was asked for the product at chest height,
  and the clip prompt said "beside their face" whatever the picture showed.
- Both are refused by the plan's validation when missing, not asked for in a sentence (ADR 0001).
- Jobs planned before the fields existed have them blank: their person is made from the words
  alone and they are posed as handheld, as every job was then.

## How each ad was checked

1. In the DB: `product_size` and `person_gender` on the job.
2. The `draw_person` and `design_voice` rows in `gateway_modelcall` carry the gender in their
   handoff; the `choose_starting_picture` rows for talking scenes carry the pose; the `make_clip`
   rows for talking scenes carry the same pose sentence.
3. The starting pictures and the clips' middle and last frames, tiled per scene (Pillow and
   `ffmpeg` in the backend container), to see the product at its real size and the pose held.
4. The voice: median pitch of each line's audio, estimated by autocorrelation on the WAV (adult men
   speak around 85–155 Hz, adult women around 165–255 Hz). Checked against the first run's chair
   job, where a woman spoke with a man's voice: its lines sit at 114–127 Hz. The pitch check is
   a rough guide; listening to the ad in the chat is the real check.

## Ads

### Branch Swivel Chair (#14 in the first run: tiny chair in her hands, man's voice on a woman)

- Plan: size **large**, gender **woman**. 4 scenes, 2 talking, 2 B-roll. Cost $0.64, 154 s after
  the length question ("keep the longer version").
- Handoffs: both person handoffs carry "a woman" / "A woman's voice."; both talking scenes'
  picture handoffs and motion prompts carry the large pose; "chest height" appears nowhere.
- Pictures: in both talking scenes the chair stands on the floor at its real size, the woman
  beside it with a hand on the back; the clip holds that pose to its last frame.
- Voice: median pitch 176–195 Hz across the four lines. A woman's voice.

### gorjana Melrose Diagonal Studs (#13 in the first run: studs a few pixels on an open palm)

- Plan: size **tiny**, gender **woman**. 3 scenes, 2 talking, 1 B-roll. Cost $0.49, 169 s.
- Handoffs: both person handoffs carry the gender; both talking scenes' picture handoffs and
  motion prompts carry the tiny pose.
- Pictures: scene 3 is the pose working as meant, one stud between finger and thumb filling the
  frame, held to the clip's end. Scene 1 found a flaw in the pose sentence: it said "between
  finger and thumb, or wears it", the picture chose "wears it" (studs in her ears), and the video
  model, told to hold something up close, invented a white box for her to hold. **Fixed during the
  run:** the tiny pose is the finger-and-thumb hold only. One pose sentence must have one reading,
  or the picture and the clip can still disagree. Written into #1 and #85. Not re-run (the fix is
  in the checklist below).
- Voice: median pitch 154–167 Hz across the three lines, on the border between the two ranges
  (the first run's studs voice, which the review didn't flag, sits at 170–222 Hz). **Listen to
  this one**; the pitch check can't call it.

### Anker 313 Power Bank (#06 in the first run: man's voice on a woman)

- Plan: size **handheld**, gender **woman**. 4 scenes, 2 talking, 2 B-roll. Cost $0.81, 216 s
  after a question about the USB-C port ("leave it out of the ad").
- Handoffs: both person handoffs carry the gender; both talking scenes' picture handoffs and
  motion prompts carry the handheld pose, which is the old behaviour kept on purpose.
- Pictures: the power bank held at chest height beside the face in both talking scenes, label to
  camera, held to the clip's end.
- Voice: median pitch 190–208 Hz across the four lines. A woman's voice.

### gorjana studs, run again with the fixed tiny pose

- Plan: size **tiny**, gender **woman**. 3 scenes, 2 talking, 1 B-roll. Cost $0.44 to the point
  where scene 1's clip failed with a connection error on the video service and the producer
  stopped rather than risk a double charge (plumbing, first-run.md problem 7; asked to retry in
  a second message, after which it finished: $0.49 in all, 11.1 s long).
- Handoffs: both talking scenes' picture handoffs and motion prompts carry the new tiny pose,
  with no "or wears it".
- Pictures: both talking scenes hold one stud between finger and thumb beside the face, and
  scene 3's clip keeps that to its end. No invented box. The stud is still smaller in frame than
  "fills a good part of the frame" asks: the picture model reads "up close" as beside the face.
  A follow-up if it matters: word the tiny pose as held right in front of the lens.
- Voice: median pitch 182–193 Hz. A woman's voice.

## What the run says

- **Gender**: 4 of 4 ads planned a woman, and all 4 voices read as a woman's by pitch, 3 of
  them clearly (182–208 Hz) and the first studs run on the border (154–167 Hz). In the first run
  the same products got 114–130 Hz voices on women. The person handoffs carried the gender in
  every job.
- **Size**: the chair stands on the floor at its real size (was toy-sized in her hands), the
  studs are held between finger and thumb (were a few pixels on a palm), the power bank is as
  before. In every talking scene the picture handoff and the clip's motion prompt carried the
  same pose sentence, and no clip invented limbs.
- **One flaw found and fixed mid-run**: a pose sentence with two readings ("or wears it") is two
  poses, and the picture and the clip can pick different ones.

## Checklist for the next real ads

Restart the worker after pulling the code (`docker compose restart worker`; the backend runs
the migration on its own restart). Then per ad, about $0.5–0.8 each:

1. **Voice matches the portrait**: play the voice in the chat next to the portrait.
2. **Job fields**: `select product_size, person_gender, person_looks, person_voice from jobs_job
   order by created_at desc limit 1;` in `docker compose exec db psql -U adforge adforge`.
   Both set; the looks and voice agree with the gender.
3. **Person handoffs**: `select purpose, handoff from gateway_modelcall where job_id = '…' and
   purpose in ('draw_person', 'design_voice');` The prompt says "The person: a woman." or
   "a man."; the description starts "A woman's voice." or "A man's voice.".
4. **Pose in the picture handoffs**: `select handoff->>'scene', handoff->>'pose' from
   gateway_modelcall where job_id = '…' and purpose = 'choose_starting_picture';` One row per
   talking scene, all the same sentence, the one for the job's size; never "chest height" for a
   large product.
5. **Pose in the clip prompts**: `select handoff->>'motion_prompt' from gateway_modelcall where
   job_id = '…' and purpose = 'make_clip';` Talking scenes carry the same sentence as step 4;
   B-roll scenes carry the model's own prompt, unchanged by this work.
6. **The pictures**: open each talking scene's starting picture (Django admin, produced items of
   kind `starting_picture`) and scrub the finished ad. A large product on the floor at its real
   size; a tiny one between finger and thumb; a handheld one beside the face. No extra hands.

Products worth running: the treadmill (#24 in `docs/test-products.md`, large; it asks for a
price first), the teething ring (#16, handheld, wrong voice in the first run), and a garment
(the sweater, #04) to decide whether clothing needs a fourth size, "worn".

## Seen next to this work, not fixed

- Inworld refuses a non-ASCII voice description (a curly apostrophe). The description is now
  composed in `_how_the_voice_sounds` in `backend/jobs/work.py`; one `.encode("ascii",
  "ignore")` there, or a replacement table, would fix it. Asked for before doing it.
- The video service's connection error stopped the second studs ad; the producer refused to
  retry on its own (first-run.md problem 7).
- The tiny pose could ask for the product nearer the lens; see the studs section above.
