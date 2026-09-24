# Plan: #19 — the line's audio and its transcript

Builds on #18 and #45 (commits `65c22d9`, `a60b53a` on `18-starting-picture`). Spec (#1) and #19
were updated for the one decision that changed them: a transcript and its word timings are one item.

## Decisions already made

- The transcript and its word timings are **one** `ProducedItem` row, pointing at the audio it came from.
- The line's audio is **not** posted in the chat. The producer just tells the user it's done.
- Neither tool takes text. Both take only `scene: int`. The audio speaks the line stored in the database.
- Nothing compares the transcript with the line. That's a quality check, left to the critic later.
- Tests go through the chat, as #18's do. Only the outside services are faked.

## Phases

Each phase ends with the whole test suite green. Phases 1–3 change no behaviour the producer can see,
so they can be reviewed on their own.

---

### Phase 1 — ElevenLabs in the gateway

**Why first:** the tools need `gateway.transcribe()` to exist, and it can be tested without any tool.

1. **Settings** (`adforge/settings.py`): `ELEVENLABS_API_KEY`, `ELEVENLABS_BASE_URL`
   (default `https://api.elevenlabs.io`), next to the Inworld ones. Add them to the env example too, if one exists.
2. **Types** (`gateway/types.py`):
   - `TranscriptionHandoff(Handoff)`: `audio: str = Field(min_length=1)`, the audio's key in the file store.
   - `@dataclass Word`: `text: str`, `start: float`, `end: float` (seconds).
   - `@dataclass Transcription`: `text: str`, `words: tuple[Word, ...]`, `audio_seconds: float`
     (what ElevenLabs bills on).
   - `TranscriptionProvider(Protocol)`: `name`, `transcribe(*, model: str, audio: bytes) -> Transcription`.
     It raises `OutsideServiceDown` for errors worth retrying.
3. **Adapter** (`gateway/elevenlabs_adapter.py`, modelled on `inworld_adapter.py`):
   - `httpx.Client` with `xi-api-key` header, timeout 120, no retries of its own (the gateway retries).
   - `transcribe()` does a multipart POST to the speech-to-text endpoint with `model_id=scribe_v2` and
     word-level timestamps. Check the API docs for any option that cleans up the transcript
     (drops filler words, fixes repeats) and turn it **off**: the transcript must be exactly as spoken.
     Don't tag audio events.
   - Keep only entries of type `word` (skip spacing and audio events). Map each to `Word`.
   - `TransportError`, 429 or 5xx → `OutsideServiceDown`. Other 4xx → `raise_for_status()`.
   - A reply with no `words` or no `text` → `ValueError` (not retried).
4. **Catalog** (`gateway/catalog.py`):
   - `MODEL_FOR_PURPOSE`: `"speak_line": "inworld-tts-2"`, `"transcribe_line": "scribe_v2"`.
   - `PRICE_PER_HOUR_OF_AUDIO = {"scribe_v2": Decimal(...)}`, taken from ElevenLabs' pricing page
     (write down where it came from, as the other prices do).
   - `transcription_cost_usd(model, seconds)`.
5. **ModelCall** (`gateway/models.py`): add `audio_seconds = FloatField(null=True, blank=True)`, the
   billing unit for transcription, like `characters` is for speech. Migration. Show it in the admin.
6. **Gateway** (`gateway/gateway.py`):
   - `_elevenlabs()` cached like `_inworld()`, and `_transcribers()` returning the override or it.
   - `transcribe(*, job, purpose, audio_key) -> Transcription`: build the handoff, read the bytes with
     `file_store.read`, call the provider inside `_recorded`. Record `output` as
     `{"text": ..., "words": [...], "audio_seconds": ...}` so a restarted worker can rebuild the
     `Transcription` from the record without paying again. `_Bill` gets an `audio_seconds` field.
7. **Fake** (`gateway/fake.py`):
   - `speak()` now takes the call's purpose into account for failures. Today it only checks
     `_fail_if_scripted("measure_voice")`, so a test can't make `speak_line` fail. Pass `purpose`
     through from the gateway, or check both purposes.
   - `speak()` makes each audio unique (e.g. first sample = a counter, as `edit()` does for pictures)
     and remembers `self.heard[audio_bytes] = text`.
   - `transcribe()`: if a transcript was scripted for `"transcribe_line"`, return it. Otherwise return
     the words the audio was spoken from, evenly timed at `words_per_second`. Record each call in
     `self.transcribed` so tests can count them. Fail if scripted, like the others.

**Tests** (`tests/test_providers.py` and `tests/test_gateway.py`):
- Adapter against a local HTTP server (like the `openai_server` fixture): it sends the model id and
  the audio, and returns only the `word` entries with their timings.
- 503 then 200: retried, two `ModelCall`s recorded, the first failed, the second with its cost,
  `audio_seconds` and duration.
- A transcript comes back unchanged even when it doesn't match any line (a repeated word stays repeated).

---

### Phase 2 — Storage

**Why:** the new tools need somewhere to store their results before they can be written.

`jobs/models.py`, one migration (`0015_line_audio_and_transcripts`):

- `SceneStep.Kind`: add `LINE_AUDIO = "line_audio"`, `TRANSCRIPT = "transcript"`.
- `SceneStep.made_from`: FK to `ProducedItem`, `null=True`, `on_delete=PROTECT`, `related_name="+"`.
  What the step makes from, fixed when it starts: the voice for a line's audio, the audio for a
  transcript. (Built as `audio` first; review showed the voice must be fixed at the start too.)
- `ProducedItem.Kind`: add `LINE_AUDIO = "line_audio"`, `TRANSCRIPT = "transcript"`.
- `ProducedItem` new fields:
  - `made_from`: FK to `self`, `null=True`, `on_delete=PROTECT`, `related_name="+"`.
    Audio → the voice that spoke it. Transcript → the audio it was transcribed from.
  - `seconds`: `FloatField(null=True)`. How long the audio lasts.
  - `text`: `TextField(blank=True)`. The transcript exactly as heard.
  - `words`: `JSONField(default=list, blank=True)`. `[{"word", "start", "end"}, ...]`.
- Update the `file` help text (it now also holds a line's audio).
- Admin (`jobs/admin.py`): show the new fields in `ProducedItemInline` / `ProducedItemAdmin`, and
  `audio` in the `SceneStep` inline and admin.

No behaviour yet, so no new tests. The existing suite must still pass after the migration.

---

### Phase 3 — Make the background runner handle any kind of step

**Why:** `run_scene_step` (`agents/tasks.py`) only knows starting pictures. Do this as a pure
refactor **before** adding new kinds, so #18's existing tests prove nothing broke.

1. In `agents/tasks.py`, define what each kind brings:

   ```python
   @dataclass(frozen=True)
   class StepWork:
       name: str                                    # "starting picture", used in messages
       make: Callable[[SceneStep], ProducedItem]    # the work, in jobs/work.py
       finished: Callable[[SceneStep, ProducedItem], str]   # what the producer is told
       shown: Callable[[ProducedItem], list[AttachedFile]]  # what the chat shows, often nothing

   STEP_WORK = {SceneStep.Kind.STARTING_PICTURE: StepWork(...)}
   ```
2. `run_scene_step` looks up `STEP_WORK[step.kind]`, runs `make` inside `charged_to(...)`, then in
   one transaction: posts a message only if `shown(item)` isn't empty, marks the step finished and
   stores `finished(...)` as its result. The failure branch uses `name`:
   `f"Background step failed: scene {n}'s {work.name} couldn't be made: ..."`.
3. `wake_producer` stays at the end, unchanged.

**Tests:** none new. All of `tests/test_producer_scenes.py` must pass unchanged, message text included.

---

### Phase 4 — "Make the line's audio"

1. **Work** (`jobs/work.py`), `make_line_audio(step) -> ProducedItem`:
   - `made = step.produced.first()`; if it exists, return it (same restart rule as the picture).
   - `voice = step.made_from`, the voice fixed when the tool started the step.
   - `paid_for = _paid_for_before(job, "speak_line", charged_to=step.tool_call)`, reused if present,
     otherwise `speak(job=job, purpose="speak_line", voice_id=voice.voice_id, text=step.line)`.
     Speak **`step.line`**, the copy taken when the step started, not `scene.line`.
   - Create `ProducedItem(kind=LINE_AUDIO, scene, step, version=next, file, seconds=_seconds(...),
     made_from=voice)`. Pull the "next version" code out of `make_starting_picture` into a
     `_next_version(scene, kind)` helper and use it in both.
2. **Runner entry**: `STEP_WORK[LINE_AUDIO] = StepWork(name="line's audio", make=make_line_audio,
   finished=..., shown=lambda _: [])`. The finished text:
   `"Background step finished: scene 1's line's audio is ready (version 1, 2.4 seconds). It isn't
   shown to the shop owner. Transcribe it next. Tell the shop owner."`
3. **Tool** (`agents/producer.py`), `MakeLineAudio(Tool)`, `name = "make_line_audio"`, only field
   `scene: int`. Docstring says it speaks the scene's line as it stands, in the person's voice, works
   in the background, only for a line that passed the fact check. `run()`:
   1. `scene = _a_checked_scene(call, self.scene)`
   2. `voice = latest(job, VOICE)`; none or no `voice_id` → `Refused("the person hasn't been made yet,
      and the audio is in their voice. Create the person first.")`
   3. **Free reuse**: finished `LINE_AUDIO` step with `line=scene.line` whose item's `made_from` is
      `voice` → hand back `"...already made for this line in this voice (version N), so nothing was
      made or paid for again. Making it cost $X."`
   4. Running `LINE_AUDIO` step → `Refused("scene N's audio is already being made. You'll be told
      when it's ready.")`. Same `IntegrityError` race handling as `MakeStartingPicture`.
   5. Create the step, `transaction.on_commit(run_scene_step.delay)`, return
      `"Started scene N's audio. It isn't made yet: you'll be told when it's ready."`

   Checks 3 and 4 have the same shape as in `MakeStartingPicture`. If copying them feels heavy, pull
   out a `_start_step(call, scene, kind, ...)` helper that all three tools share. Decide when writing it.

---

### Phase 5 — "Transcribe the audio"

1. **Work** (`jobs/work.py`), `transcribe_line_audio(step) -> ProducedItem`:
   - Restart rule: return `step.produced.first()` if it exists.
   - `paid_for = _paid_for_before(job, "transcribe_line", charged_to=step.tool_call)`, rebuilt from
     the record if present, otherwise `transcribe(job=job, purpose="transcribe_line",
     audio_key=step.made_from.file)`.
   - Create `ProducedItem(kind=TRANSCRIPT, scene, step, version=next, text, words,
     made_from=step.made_from)`. Store it **as returned**: no cleaning, no matching to the line.
2. **Runner entry**: `name="transcript"`, `shown=lambda _: []`, finished text:
   `'Background step finished: scene 1's audio (version 1) was transcribed (version 1). It was heard
   as: "…". Tell the shop owner.'`
3. **Tool**, `TranscribeLineAudio(Tool)`, `name = "transcribe_line_audio"`, only field `scene: int`.
   `run()`:
   1. `scene = _a_checked_scene(call, self.scene)`
   2. Running `LINE_AUDIO` step for this scene → `Refused("scene N's audio is still being made.
      You'll be told when it's ready; transcribe it then.")`
   3. Audio for the current line but in an earlier voice → refused too (added in review).
   3a. `audio` = newest `LINE_AUDIO` item for the scene whose `step.line == scene.line`. None →
      `Refused("scene N has no audio for its current line. Make the line's audio first.")`. If older
      audio exists for a different line, add: "Its audio was made for an earlier line."
   4. **Free reuse**: a `TRANSCRIPT` item with `made_from=audio` → hand it back with its text and cost.
   5. Running `TRANSCRIPT` step → refuse, as for the audio.
   6. Create `SceneStep(kind=TRANSCRIPT, line=scene.line, made_from=audio)`, start it, return
      `"Started transcribing scene N's audio. ..."`

---

### Phase 6 — The producer

`agents/producer.py`:
- Add `MakeLineAudio` and `TranscribeLineAudio` to `PRODUCER.tools`.
- In `INSTRUCTIONS`, change the "usual order" sentence to end: *"...then, for each scene, make its
  starting picture and its line's audio, which can run at the same time, and transcribe the audio
  once it's ready."* Change nothing else. The tools enforce the order.

---

### Phase 7 — Tests through the chat

New file `tests/test_producer_line_audio.py`. Reuse `HeldSteps`, `checked`, `steps` from
`test_producer_scenes.py` (move them to `conftest.py` if importing across test files is awkward).

**End-to-end:**
1. **Audio in the background.** Tool returns "started", no audio item, step running. Run held: audio v1
   exists, step finished, producer given the `step_finished` text, producer's reply in the chat,
   **no attachment** posted.
2. **Transcript in the background.** Same shape. `made_from` is the audio. The producer's text holds what was heard.
3. **One scene, whole workflow.** Picture and audio started in one turn, both run, then transcribe
   started and run. All three steps finished, producer told three times, `paid_for()` shows each
   purpose paid once (`choose_starting_picture`, `make_starting_picture`, `speak_line`, `transcribe_line`).
4. **Mixed results mid-turn.** Audio finishes (`meanwhile`) while the producer answers about the
   picture. Given on the next turn, none lost, every step has `producer_read_at`.
5. **Too early.** Transcribe with no audio, then while audio runs. Both refused with the reason,
   no `transcribe_line` model call.
6. **ElevenLabs stays down.** Fake raises `OutsideServiceDown` every attempt. Step `failed` with reason,
   producer told `"Background step failed: scene 1's transcript couldn't be made: ..."`, each attempt recorded.
7. **Inworld stays down** for `speak_line`: same, for the audio.

**Rules:**
8. Audio for an unchecked line → refused, nothing paid.
9. Transcribe for an unchecked line → refused, nothing paid.
10. Audio before the person exists → refused.
11. Audio asked twice, same line → one `speak_line` call, second result says nothing was paid.
12. Line changed (set in the DB), audio asked again → v2 made, v1 kept, `speak_line` called twice.
13. Line changed after audio v1 → transcribe refused ("earlier line"), no call.
14. Transcribe asked twice for the same audio → one `transcribe_line` call.
15. Scripted misspoken transcript ("the the mug") is stored word for word.
16. Worker stopped after `speak_line` was paid (`WorkerStopped`, as in #18's test) → step run again
    keeps the paid audio, one `speak_line` call. Same for transcribe.
17. The audio tool has no argument for text: its schema's only property is `scene`.

---

### Phase 8 — One run against the real services

Before merging, with real `INWORLD_API_KEY` and `ELEVENLABS_API_KEY`: make one scene's audio and
transcript through the app. Check the audio plays, the transcript matches what you hear, and the
cost recorded looks right. Write ElevenLabs' real reply shape into `docs/real-api-replies.md`.
Fix the adapter if the real reply differs from what the fake assumed.

---

## Checklist against #19's acceptance criteria

| Criterion | Where |
|---|---|
| Make the line's audio is a tool; the producer can't give it other text | Phase 4, test 17 |
| Transcribe is a tool; words as spoken, with start and end | Phases 1, 5, test 15 |
| Audio and transcript (with timings) stored as versions, never overwritten | Phase 2, test 12 |
| Both refuse an unchecked line, shown by a test | Tests 8, 9 |
| Something a checkpoint covers is handed back, charges nothing | Tests 11, 14, 16 |
| ElevenLabs through the gateway: validated, retried, recorded, faked | Phase 1, test 6 |

## Left out on purpose

- Comparing the transcript with the line: the critic, later.
- Posting starting pictures in the chat goes against #6 ("the user does not see scenes"). That's a
  separate question from this ticket, still to decide.
- Limits on renders and retries for scene work: #21.
