# Design mentor: assembly & scene cards

Area mode (design-mentor skill, steps 2–3), 2026-11-03.

**Design read:** `CONTEXT.md` (Finished ad, Caption, Overlay, Clip, Version, Clip version picker,
Try, Sent list, Piece, Reference mode, End card); spec #1 stories 44–53 and "Assembly",
"Overlays", "Editing", "What is stored"; spec #210 "Cut-and-send"; #327 as context.

**Code read:** `backend/jobs/assembly.py`, `cut_and_send.py`, `cards.py`, `card_versions.py`,
`sent_labels.py`, and what they call: `work.py` (`assemble_ad`, `versions`, `go_back`,
`bring_back_clip`, `_with_the_voice`), `agents/producer.py` (`AssembleAd`, `assemble_the_ad`,
`assembled_again`), `agents/card_picks.py`, `agents/tasks.py:_work_on`, `chat/api.py`
(`scenes`, `use_version`), `chat/sent.py`, `library.py:SentList`. Tests: `tests/ffmpeg_replay.py`,
`test_assembly.py`, `test_producer_assemble.py`, `test_producer_early_cut.py`,
`test_card_versions.py`, `test_versions.py`.

**Checked by running (free, local, no paid call, no ad judged):** rendered the app's own
`TEXT_STYLES` with ffmpeg 6.1 to measure text bands and wrapping; joined test clips of several
shapes the way `assembly.join` does; ran `assembly.captions` on the transcript in
`test_assembly.py`. Scripts and renders are in the session scratchpad, not the repo.

## How it works today

- `producer.py:assemble_the_ad` refuses until every scene is finished and music exists, hands
  back an earlier ad made from the same clips, overlays and music, else calls `work.assemble_ad`.
- `work.assemble_ad` plans the cuts (`assembly.cuts`), times the written line by the heard words
  (`assembly.timed_script`), groups captions (`assembly.captions`), runs `assembly.join`, and
  keeps the ad as the job's next `FINISHED_AD` version with its cuts and captions.
- `assembly.join` is one ffmpeg graph: per clip, picture trim/pad/scale/fps and voice
  atrim/apad; both concatenated; an ASS file drawn; music at `MUSIC_VOLUME` mixed under.
- Silent B-roll clips get their voice before they are kept (`work._with_the_voice`,
  `assembly.lay_voice_over`).
- The card: `card_versions.shown` builds the strip from `work.versions`; `card_versions.use`
  swaps by copying rows (`work.go_back` → `bring_back_clip`); `card_picks.assemble_after_swaps`
  re-assembles at the start of the producer's next run; `cards.make_cards` keeps 480 px copies.
- `cut_and_send.py` is now colour helpers and `_pieces`; the sent list shape is `library.SentList`.

## How it is tested today

- `test_assembly.py` (`real_ffmpeg`): tiny generated clips (72×128, one colour, a 440 Hz
  "voice"), 110 Hz "music"; checks by `colour_at`, `loudness` bands, `drawn_in`, ffprobe. Plus
  pure tests of `cuts`, `captions`, `timed_script`, `_timestamp` by hand-picked examples.
- Chat flows (`test_producer_assemble.py`, `test_producer_early_cut.py`, `test_end_to_end.py`,
  `test_card_versions.py`, `test_versions.py`, `test_pick_from_tries.py`): models faked at the
  gateway; ffmpeg replayed by `ffmpeg_replay.run` (key = arguments + bytes of every file read;
  failed runs never kept; per worker, in memory).
- Gaps: every fake clip is exactly 9:16 (`gateway/fake.py:_clip`, 72×128); voice and music fakes
  are tones at the same level; lip sync is checked 250 ms either side of the switch; no test
  makes assembly fail after a card swap.

## Concepts, ranked by impact on this build

### 1. Failure isolation (poison messages) — **must apply now**

**What it is:** one failing item must not block the work around it. A message that fails the
same way every time it is handled is a "poison message"; queues move it aside so the rest runs.

**Why it matters here:**
- A card swap re-assembles inside `tasks.py:_work_on`, before the producer's loop runs
  (`card_picks.py:assemble_after_swaps`).
- `producer.py:assembled_again` catches only `Refused` and `Failed`. `assembly.AssemblyFailed`
  (and `subprocess.TimeoutExpired` from `assembly._ffmpeg`) are neither, so they escape.
- `tasks.py:run_producer` then tells the owner "I had to stop for a moment. Send me any message
  and I'll pick it up." (`loop.STOPPED`). The swap note stays `assembled=False`.
- Their next message runs `_work_on` again and hits the same failure first. The session is
  stuck until the code changes. (Found by reading the code; not run.)
- In the producer's `use_version` / `keep_try`, the swap commits, then the same failure makes
  `loop._run` answer "Failed: an unexpected error stopped the tool": the producer is told it
  failed when the swap happened.
- Related known limitation: "After a failed assembly the shop owner can't ask for it again"
  (`docs/limitations.md`). This is worse: the session stops answering.

**How to apply it:**
- In `assemble_the_ad`, turn `AssemblyFailed` and the timeout into one known failure (a
  `Failed` with ffmpeg's last line), so both paths can say it in words.
- In `assembled_again`, catch it: mark the swap note assembled, add "the swap is in; the ad
  couldn't be put together", post a developer notice `Kind` with ffmpeg's stderr.
- Have `UseVersion` / `KeepTry` report the swap as done even when the re-assembly fails.
- Chat test: swap on the card with `assembly._ffmpeg` failing, then send a message: the producer
  must answer it.
- Notice wording is yours. Whether the owner can then ask again is the known limitation: his call.

**#327:** unchanged.

**One real post:** Uber, [Building Reliable Reprocessing and Dead Letter Queues with Apache
Kafka](https://www.uber.com/en-US/blog/reliable-reprocessing/): "repeatedly failed messages
can clog batch processing", so failed ones go to their own topics.

### 2. Canonical intermediate format (normalise every clip before the join) — **must apply now**

**What it is:** put every input into one exact format (size, pixel shape, frame rate, audio rate
and channels) before you join anything, so the join never meets a surprise.

**Why it matters here:**
- `assembly.join` does `scale=1080:1920` with no aspect handling and no `setsar=1`.
- ffmpeg's concat needs every segment's parameters to match. "Resolution must be converted
  explicitly by the user" ([docs](https://ffmpeg.org/ffmpeg-filters.html#concat)).
- Tested here: a 1080×1920 clip joins with 720×1280 and 432×768. It fails with 768×1344,
  768×1360, 768×1366 and 1088×1920: "SAR 64:63 do not match … SAR 1:1". No ad is made.
- Today it works only because every model happens to return an exact 9:16 frame (`EDIT_SIZE`
  1152×2048, H3 "takes its shape" from the starting picture, HeyGen asked for 9:16).
- Nothing checks the shape when a clip is kept. The fakes are always exact 9:16, so no test can
  meet this failure. Combined with concept 1, one odd clip can stop a whole session.

**How to apply it:**
- Per clip in `join`: `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,
  setsar=1,fps=25`. Crop or pad (black bars) is Julian's call.
- Per voice: `aresample=48000,aformat=channel_layouts=stereo`, so the audio side is fixed too.
- When a clip is kept, ffprobe its width, height, SAR and audio stream; post a developer notice
  when it's off-shape.
- A `real_ffmpeg` test that joins 1080×1920 with 768×1344 and 1088×1920.

**#327:** still needed (H3 only). Also, every clip must then carry sound: a clip with no audio
stream fails `[n:a]` in `join`, so add a silent track (`anullsrc`) when it's missing.

**One real post:** FFmpeg docs, [concat filter](https://ffmpeg.org/ffmpeg-filters.html#concat):
"All corresponding streams must have the same parameters in all segments".

### 3. Loudness normalisation (EBU R128, LUFS) — **must apply now**

**What it is:** measure how loud each sound is, in LUFS (perceived loudness, ITU-R BS.1770), and
set it to a target with a fixed gain, with a ceiling on true peaks. You get the same loudness
every time, whatever loudness each source came at.

**Why it matters here:**
- `assembly.MUSIC_VOLUME = 0.2` is "about 14 dB down" from the music's *own* level, not from the
  voice. A loud music file stays loud; a quiet one disappears.
- Nothing measures the voice, the music or the finished ad. `join` mixes with `normalize=0`
  and no limiter, so nothing stops peaks going over full scale. (No search hit for `loudnorm`,
  `ebur128` or `LUFS` in the repo.)
- The test `test_producer_assemble.py:test_the_music_plays_under_the_voice_and_is_cut_to_the_ads_length`
  compares two tones that the fake makes at the same level, so it can't see this.
- No graded ad has flagged it yet. Today one TTS voice speaks every line, so the voice levels
  match without anyone setting them.

**How to apply it:**
- Two-pass `loudnorm` (or `ebur128` then a fixed `volume`): measure each clip's voice and the
  music, then apply a linear gain to a target.
- Example targets for his yes: speech about −16 LUFS; music set to whatever *measured* gap he
  wants (his 14 dB, made real); true peak at most −1.5 dBTP on the mix.
- Keep the measured values on the ad record, for concept 5.
- Free: local ffmpeg, a few seconds per ad. The targets are Julian's call.
- This is not ducking (music dipping under speech), which #327 turned down.

**#327:** needed more. Each H3 clip makes its own voice at its own level (Creatify admits it
"shifts slightly in tone, pace and loudness"), and #327 keeps `MUSIC_VOLUME` as it is.

**One real post:** Spotify, [Loudness normalization](https://support.spotify.com/us/artists/article/loudness-normalization/):
tracks are played at −14 LUFS per ITU 1770, with true peak under −1 dBTP. The ffmpeg filter:
[loudnorm](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).

### 4. Safe zones (platform UI covers parts of the frame) — **must apply now**

**What it is:** TikTok and Reels draw their own buttons, username and post caption over the ad.
Text placed there is hidden. Ad specs give "safe zones" to keep text inside.

**Why it matters here:**
- Measured from `assembly.TEXT_STYLES`: captions are drawn at y≈1640–1800 of 1920 (bottom
  6–15%), the overlay at y≈82–190, or 82–261 when it wraps (top 4–14%).
- Those styles are applied by `assembly.write_text_to_draw`, which writes every caption and
  overlay as an ASS line in the "Caption" or "Overlay" style. `assembly.join` then burns that
  file into the ad (`subtitles=` filter).
- Third-party Meta guides say to keep the top 14% and the bottom 35% of Reels ads free of text.
  TikTok says not to place text "on areas where buttons, usernames, and captions may appear".
- So both text bands sit where the platforms draw their own UI. The bands were chosen to clear
  the face and the product (spec #1 "Assembly"), not the platform's UI.
- Julian grades in the app's player, so he would never see the overlap.

**How to apply it:**
- One proposal for his yes: captions moved up to about 60–65% of the height, the overlay just
  under the top 14%, and the framing prompt asked for room there.
- Add a debug render with the safe zone drawn over it, for grading.
- Design is his call: moving text up can cover the product.

**#327:** unchanged; H3's framing prompt would carry the room instead.

**One real post:** TikTok Ads help, [TopView safe zones](https://ads.tiktok.com/help/article/tiktok-reservation-topview):
"Elements that appear out of the safe zone might be covered or cropped." Numbers for Meta Reels:
[adnova guide](https://adnova.ai/blogs/meta-ad-safe-zones-guide) (third party; top 14%, bottom 35%).

### 5. Post-render quality control (automated media QC) — **must apply now**

**What it is:** after making a file, measure it with code (stream lengths, size, loudness, peaks,
silences) and flag what's out of range, every time, in production.

**Why it matters here:**
- `work.assemble_ad` keeps the ad with `seconds=planned[-1].end` and never probes the file it
  made.
- The 08 Oct bug (the last line cut 1.2 s early) reached all 5 graded ads before anyone noticed
  (comment in `assembly.join`). An audio-vs-video length check would have caught it on the first ad.
- Tests use tones and exact-shape fakes, so real-world drift only shows up when Julian listens.

**How to apply it:**
- After `join`, check:
  - the audio and video streams are the same length, within 1 frame
  - 1080×1920, SAR 1:1, 25 fps
  - integrated loudness and true peak (`ebur128`)
  - no silence longer than about 0.3 s inside a voice span (`silencedetect`)
  - every caption sits inside its voice span
- Out of range → a developer notice `Kind` (job.warnings + Sentry). The ad is still shown. Keep
  the measured values on the ad.
- Free, local. Which checks become owner-facing is his call.

**#327:** unchanged; a speech-present check per clip helps the new word check.

**One real post:** Amazon Science, [How Prime Video uses machine learning to ensure video
quality](https://amazon.science/blog/how-prime-video-uses-machine-learning-to-ensure-video-quality):
automated detectors for 18 defect types (audio artifacts, A/V sync and more), because manual
review "doesn't scale well".

### 6. Text measurement (fit text by rendered width, not character count) — **must apply now**

**What it is:** whether text fits depends on the font and the glyphs ("W" vs "i"), so measure
its width in pixels with the real font instead of counting characters.

**Why it matters here:**
- `planning.py:MOST_OVERLAY_CHARACTERS = 30`, with the comment "longer than this would wrap
  into a second line, down towards the face" (`PlannedScene._a_few_words`).
- Rendered with `TEXT_STYLES`: 26 characters fit; at 27 they wrap. "Dishwasher safe, now
  $24.00" (27) → two lines, box 108 → 180 px tall, down towards the face.
- Captions too: `CAPTION_WORDS = 3` caps words, not width. "Hand-thrown, holds 350", a
  caption from the app's own test plan, wraps to two lines and climbs towards the product.

**How to apply it:**
- Measure with the same font file `TEXT_STYLES` names (Pillow `FreeTypeFont.getlength`), at a
  size calibrated once against a libass render in a `real_ffmpeg` test.
- Overlay: refuse or shrink it (`\fs`) when it is over the band's 960 px.
- Captions: split groups by width as well as word count.
- The validator lives in `planning.py` (another area); the styles are here.

**#327:** unchanged.

**One real post:** Pillow docs, [ImageFont](https://pillow.readthedocs.io/en/stable/reference/ImageFont.html):
`getlength` "returns length (in pixels…) of given text".

### 7. Content-addressed caching (the key covers every input, the recipe too) — good to know

**What it is:** decide "already made" by hashing everything the output depends on: the input
contents *and* the recipe (the command, the settings, the code version). Same hash, same output.

**Why it matters here:**
- `producer.py:assemble_the_ad` reuses an ad when (clip row id, overlay) per scene and the
  music row id match.
- The key compares row ids, not contents. A swap copies a clip into a new row
  (`work.bring_back_clip`), so swap then Undo makes a new ad version identical to the first
  (`test_card_versions.py:test_undo_swaps_the_version_before_back_in_for_nothing` reads the
  copy's id).
- The recipe is missing from the key: change `MUSIC_VOLUME`, the styles or `CAPTION_WORDS`, and
  "assemble again" still says nothing changed.
- The transcript used for the captions isn't in the key either.
- `cards.py:card_key` is `cards/{file}.jpg`, so changing `CARD_PICTURE_SIDE` keeps the old copies.
- The test helper does it right: `ffmpeg_replay.run` hashes the arguments and every input file's
  bytes.

**How to apply it:**
- Store `made_with = sha256(ASSEMBLY_RECIPE, per scene: clip.file, transcript id, line, overlay;
  music.file)` on the ad, and reuse only on an equal digest.
- Bump `ASSEMBLY_RECIPE` whenever assembly changes.
- Card key: `cards/{file}-{side}-q{quality}.jpg`.
- Re-rendering after a recipe change is a small behaviour change: his yes.

**#327:** captions come from each clip's own transcript, so the transcript must be in the key.

**One real post:** Bazel, [Remote caching](https://bazel.build/remote/caching): an action cache
keyed by the action's hash (inputs, command line, environment) plus a content-addressed store.

### 8. Immutable versions with a movable pointer — good to know

**What it is:** never change a version, and keep "which one is live" as a separate pointer you
move (git: commits plus a branch ref). Each version's number is fixed when it is made.

**Why it matters here:**
- "In the ad" isn't stored. It's worked out as the newest clip that says the line
  (`card_versions._in_the_ad`).
- To put an old clip in, code copies rows (`work.bring_back_clip`, `bring_back_picture`), and the
  card hides the copies by matching files (`card_versions.shown`).
- So there are two numberings: the card says v2, while the producer is told "as v4 back to v2"
  (both asserted in `test_card_versions.py`).
- Version numbers are positions, worked out again on every read from rows filtered by step
  status (`work.versions`, `_was_shown`, `scene_pictures.visible`) and ordered by `created_at`.
- `work.go_back` refuses only while a picture or clip step runs, so a swap can land while a
  scene agent is between tries. Its picture, drawn earlier, becomes visible later and sorts
  before the copy, so later numbers move up. (Mechanism read from code; not reproduced.)

**How to apply it:**
- Store `number` on each version the first time it's shown, never recomputed.
- Store the pointer: `Scene.clip_in_ad` (FK). Swap and Undo move it; no copies. `assemble_ad`
  reads the pointer.
- Derive the old rows once in a migration.
- Big change; doing it with #327, which rebuilds the scene path anyway, is his call.

**#327:** unchanged (versions are still clips).

**One real post:** Git book, [Git References](https://git-scm.com/book/en/v2/Git-Internals-Git-References):
a branch is "a simple pointer or reference to the head of a line of work".

### 9. Optimistic concurrency control (the tap names what it saw) — good to know

**What it is:** a change request carries what the client saw ("swap clip #812"), and the server
refuses if that has changed since, instead of acting on whatever is there now.

**Why it matters here:**
- `chat/api.py:use_version` posts only `version` (a position).
- `card_versions.use` works out the strip again and swaps whatever sits at that position now.
- With concept 8's moving numbers, a tap can act on a strip the owner never saw.

**How to apply it:**
- The card sends the clip id it showed. `use` refuses with 409 ("the card changed, have a look
  again") when that clip isn't the one at that number.
- About ten lines, plus one test.

**#327:** unchanged.

**One real post:** MDN, [If-Match](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/If-Match):
prevents the "lost update problem"; a mismatch gets 412.

### 10. A/V sync tolerance budget, tested at frame precision — good to know

**What it is:** name how far sound and picture may slip (broadcast: audio at most 40 ms early,
60 ms late), and test to that number.

**Why it matters here:**
- `assembly.join` counts each part's frames from the ad's start and pads each voice to its exact
  length, so drift doesn't build up across scenes.
- But the lip-sync test (`test_assembly.py:test_a_broll_picture_plays_whole_under_the_next_line_and_the_talking_lips_stay_in_step`)
  samples 250 ms either side of the switch, so a 200 ms slip would pass.
- The lip-match skip also takes `words[0].start` from the transcript (`assembly.cuts`), so the
  transcriber's timing error goes straight into lip sync. Nothing states a budget for it.

**How to apply it:**
- Write the budget down (1 frame = 40 ms).
- Assert the exact first frame of the new colour (ffprobe `-show_frames`) and the exact start
  of the tone (`silencedetect`), each within 1 frame.

**#327:** the lip-match skip goes; sync then lives inside each H3 clip, but the join's frame and
sample counting must stay, or clips drift as they are joined.

**One real post:** [Audio-to-video synchronization](https://en.wikipedia.org/wiki/Audio-to-video_synchronization)
(EBU R37: +40/−60 ms; ITU-R BT.1359: noticed from 45 ms early / 125 ms late). Primary:
[EBU R 037](https://tech.ebu.ch/publications/r037).

### 11. Property-based testing — good to know

**What it is:** instead of hand-picked examples, state rules that must hold for *any* input, and
let a library (Hypothesis) generate hundreds of inputs and shrink any failure to the smallest one.

**Why it matters here:**
- `assembly.cuts`, `captions`, `timed_script`, `_spread` and `_timestamp` are pure and tested by
  examples only.
- Run here: `assembly.captions` raises `ZeroDivisionError` for a scene with no timed words. Only
  the caller's refusal (`producer.py:_clip_and_transcript`) keeps that from happening.

**How to apply it:**
- Rules to state:
  - voices play back to back
  - a picture never starts before its voice
  - a talking scene's picture offset equals its voice offset (lips in step)
  - captions stay inside their scene's voice span, in order
  - every written word is captioned once, in order
  - `_timestamp` never writes 60 seconds
- Hypothesis isn't installed; vet it before adding it (dependency-vetting skill).

**#327:** the play-whole cut list is already in its seam 2 as pure logic: a natural first target.

**One real post:** Goldstein et al., [Property-Based Testing in Practice](https://conf.researchr.org/details/icse-2024/icse-2024-research-track/90/Property-Based-Testing-in-Practice)
(ICSE 2024; 30 interviews at Jane Street).

### 12. Forced alignment (time the known script against the audio) — good to know

**What it is:** when you already know the words, an aligner finds when each one is said in the
audio, rather than you matching a transcriber's guesses back to your script.

**Why it matters here:**
- `assembly.timed_script` lines up the written words with the heard words using `difflib`, and
  spreads unmatched words by letter count.
- `NUMBER_MARGIN_SECONDS = 0.6` is a guess, because the transcriber timed "$60" only to the end
  of "sixty".
- Comparing "$14.99" with "fourteen ninety-nine" needs text normalisation (numbers written out)
  in any case.

**How to apply it:**
- ElevenLabs Forced Alignment takes audio plus the text and returns word times, at Scribe's
  rate. At the catalog's $0.22/hour that's about $0.002 per 30 s ad. Paid, so it's his go.
- The free way is to keep `difflib` and add a number-verbalising step before comparing.

**#327:** the number margin goes with the cut, but captions are still timed by `timed_script`
from each clip's transcript, and the word check needs normalised text too.

**One real post:** ElevenLabs docs, [Forced Alignment](https://elevenlabs.io/docs/capabilities/forced-alignment).

### 13. Caption chaining (minimum gap and minimum duration) — good to know

**What it is:** subtitle practice closes small gaps between captions (so text doesn't blink off
for a frame), keeps a minimum gap, snaps times to frames and gives each caption a minimum time
on screen.

**Why it matters here:**
- `assembly.captions` ends each group at its last word and starts the next at its first word.
- On the transcript in `test_assembly.py`: gaps of 0.04–0.06 s (1–1.5 frames at 25 fps) between
  groups, so the caption blinks off for a frame; the shortest group is on screen 0.8 s.

**How to apply it:**
- Close any gap under about 0.5 s by extending the earlier caption to the next one.
- Snap times to frames (`FRAME_RATE`).
- Give each group a floor (for example 0.5 s), borrowed from the silence after it.
- What the owner sees: his call.

**#327:** unchanged.

**One real post:** Netflix, [Subtitle Timing Guidelines](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines):
minimum 2-frame gap; gaps of 3–11 frames closed ("chaining").

### 14. Typed records at the storage boundary (tolerant reader) — good to know

**What it is:** read stored JSON back through one schema that accepts old shapes, instead of
indexing raw dicts all over the code.

**Why it matters here:**
- `ProducedItem.cuts` and `captions` are written with `asdict` and read back as raw dicts
  (`producer.py:assemble_the_ad` uses `cut.get("overlay")`, `_voice_over_the_clip_before`).
- The field's help text lists 6 of the 11 keys that are stored.
- The sent list does this right: `library.SentList`, read through `sent_list_of` with lenient
  stored values.

**How to apply it:** one reader (`assembly.Cut` with defaults for keys old rows lack) that every
caller uses.

**#327:** play-whole changes what a cut means, and old ads keep the early-cut shape, so both shapes
need one reader.

**One real post:** Martin Fowler, [Tolerant Reader](https://martinfowler.com/bliki/TolerantReader.html).

### 15. Idempotency keys — good to know

**What it is:** recognise "the same event again" by an id you choose (job, scene, step, kind),
not by comparing the text it produced.

**Why it matters here:**
- `cut_and_send._say` skips a notice when any job warning has exactly the same text.
- So a reworded message is posted twice, and a later clip with an identical problem is never
  told.

**How to apply it:** store a key `(kind, scene, step or clip id)` with the warning and skip on the
key.

**#327:** unchanged.

**One real post:** Stripe, [Designing robust and predictable APIs with idempotency](https://stripe.com/blog/idempotency).

## Also seen here

- **An explicit state machine:** assembly has no state of its own. `producer.py:assemble_the_ad`
  runs ffmpeg inline in the producer's tool call, and no record says "assembling / failed / done".
  That is why "after a failed assembly the shop owner can't ask for it again"
  (`docs/limitations.md`): nothing remembers that it failed.
- **Atomic counters with F():** `work.assemble_ad` reads `Max("version")` and then creates
  version + 1 (so does `work._next_version` for bring-backs). It is safe only because one
  producer runs per session and `ProducedItem.Meta`'s `one_item_per_version` constraint would
  raise on a clash.

## Small things noticed (not concepts)

- `producer.py:AssembleAd` tells the producer the captions are "the words as spoken". They show
  the line as written (`assembly.timed_script`; CONTEXT "Caption").
- `cards.py:_card_picture` uses `convert("RGB")`. A transparent page PNG (photos keep RGBA,
  `photos.py`) gets a black background on the card.
- `ProducedItem.cuts` help text is out of date (see 14).
