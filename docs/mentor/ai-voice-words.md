# Design mentor: voice, words and music

**Read:** ADR 0012 in full, spec #327, spec #1 ("The person and music", Assembly), CONTEXT.md (Person,
Line's audio, Transcript, Caption, Clip, Finished ad, Voice words, Word check),
`docs/adr/0012-legacy/plans/19-line-audio-and-transcript.md`; code at `fd346df` (main merged in),
backend paths below are under `backend/`.

Teaching only: nothing in the app changed. Applying any concept is a design change and needs
Julian's yes first.

---

## Systems in this group

Cost per ad is an **estimate** from catalog prices times the calls the code makes, not a bill.
A line of 20–25 words gets a 9–11 s clip (`jobs/checks.py:h3_clip_seconds`).

| System | What it is asked | Model, and why | In → out | What checks its output | Eval | How it fails | Cost per ad (est.) |
|---|---|---|---|---|---|---|---|
| **H3 speaks the line** (voice words, speech tags, voice change) | Code appends to every video prompt: "On/Off camera, the presenter says with {voice words}. [mouth in sync, talking only] `<d>[English] {line}</d>` Sound: room tone, the action's sounds and the presenter's voice. No music." (`agents/scene_forms.py:video_prompt`). Voice words = `jobs/work.py:_how_the_voice_sounds` (gender + plan's `person_voice`), the same in every clip, kept on the sent list (`agents/scene_agent/film.py:Film.run`). | `creatify/boreal-h3` (`gateway/catalog.py:MODEL_FOR_PURPOSE`, `make_broll_clip`). Why: ADR 0012, so she can demonstrate while talking and one path makes every scene. | Line + voice words + picture → one clip with its own sound. | The word check (words only). Nothing checks voice identity, gender, loudness, or music in the clip. | None. | No failure of its own. Voice change: `agents/voice_changes.py:consent` quotes, fingerprints the ad (sha256), waits for the exact "Yes, change the voice"; `changed_after` and `jobs/work.py:clip_voice_is_current` make older clips stale. Refusals are `Refused`, no notice. | No separate price: inside H3's $0.0792/s, about $0.71–0.87 a clip. A voice change re-films every scene: about $2.1–3.5 for one try each (the quote in `voice_changes.consent`). |
| **transcribe_line** | No prompt. `jobs/work.py:_clip_sound` pulls the clip's sound to WAV; `gateway/elevenlabs_adapter.py:ElevenLabsProvider.transcribe` sends `timestamps_granularity=word`, `tag_audio_events=false`, `no_verbatim=false`. No language, no keyterms. | `scribe_v2` (`gateway/catalog.py:MODEL_FOR_PURPOSE`). Why: it "matched the script word for word" in the one spike test, free plan (`docs/adr/0012-legacy/video-model-tests.md`); spec #1 picked the cheapest that worked. | WAV → text + words with start/end (`type == "word"` only; `logprob`, `language_code` dropped). | Nothing. Stored "exactly as heard" (`jobs/work.py:_transcribe_clip`). | None. | 429/5xx/transport → `OutsideServiceDown`, retried. No words → `ValueError` in `gateway/gateway.py:transcribe`. Any error → `jobs/work.py:transcribe_clip` posts `STEP_NOT_MADE` (developer, Sentry) and returns None; captions for that clip are left out. | $0.22/h: about $0.0006 a clip, $0.002–0.016 an ad (3–4 scenes, 1–6 clips each). |
| **check_words** | `jobs/word_check.py:QUESTION`: fail a different, missing or extra name, number, price or claim (incl. words cut off at the end); pass what a listener wouldn't notice. Handoff `Words(line, said)`, whole texts. Only reached when `tidy(line) != tidy(said)` (`jobs/word_check.py:check`). | `gpt-6-luna` (`gateway/catalog.py:MODEL_FOR_PURPOSE`). Why: #327 asks for "a cheap model the catalog already prices". No comparison test, no catalog comment. | `{line, said}` → `Answer{verdict: pass/fail, reason}` (structured output, `Literal` verdict). | Nothing. Its verdict is final; `agents/scene_agent/lifecycle.py:_passes` fails the try on "fail". | None. Every check is recorded as `WORD_CHECK` (developer, not a failure) with line, said, verdict, `settled_by`. Nothing reads those records. | No transcript → "unchecked". `OutsideServiceDown`, `AnswerTookTooLong`, `BlockedBySafetyFilter`, `UnusableReply` → "unchecked". "Unchecked" → `CHECK_IT_YOURSELF` (owner, Sentry) from `agents/scene_agent/film.py:_check_words`; the clip passes if the judge passes. `pay_once=True` reuses an answer for the same line and words. | $0.10/$0.50 per M tokens: about $0.0001–0.0002 a call, only on a mismatch; under $0.002 an ad. The real cost is the re-film a fail triggers: about $0.71–0.87. |
| **make_music** | The producer gives a mood; code wraps it: "{mood}. Background music for a short video ad, played under a person speaking. Instrumental only, no vocals, no singing." (`jobs/work.py:MUSIC_PROMPT`, `music_prompt`). Length = sum of finished clips' seconds, or predicted from word count (`jobs/work.py:music_seconds`). | `sonilo/v1.1/text-to-music` on fal (`gateway/catalog.py:MODEL_FOR_PURPOSE`). Why: "15.06 s for 15 s asked, no singing, voice still clear" in the one spike; ElevenLabs music needed a paid plan (`docs/adr/0012-legacy/video-model-tests.md`). | `{prompt, seconds}` → an m4a file (`gateway/fal_adapter.py:FalProvider.compose`, blocking `fal.run` POST, 300 s timeout). | Nothing. No check for vocals, length or loudness. | None. | 429/5xx/transport (timeouts included) → `OutsideServiceDown`, retried; still down → the tool answers "Failed: …" (`tests/test_producer_music.py`). No notice of its own. Paid-but-not-kept music is reused on restart (`jobs/work.py:create_music`). | $0.0025/s × 27–44 s: about $0.07–0.11 a make; each new mood is another. |

---

## How it is tested today

- **Pure logic:** `tests/test_word_check.py`
  - `tidy` matches "$10"/"ten dollars", "55%"/"fifty-five percent", case and punctuation.
  - `tidy` keeps real differences ("£10"/"$10", "<5%", "1/2", "10-20").
  - `check`: a code match records "code"; a difference reaches the model; no answer and no
    transcript give "unchecked"; `QUESTION` is pinned.
- **Through the chat:** `tests/test_h3_scene_words.py`
  - Wrong words come back beside the judge and are filmed again.
  - Written numbers pass with no model call; with `QUALITY_CHECKS` off the clip is still
    transcribed.
  - When Scribe or the model is down, the clip is kept and the owner is told to listen.
  - Good words don't save a clip the judge failed.
  - Six word fails end the run.
- **H3 input:** `tests/test_h3_scene_film.py:test_h3_scene_films_without_line_audio_and_transcribes_its_own_sound`
  - The speech tags, voice words, on/off camera and "No music" are in the prompt.
  - No voice is designed; the clip's own sound is transcribed; a restart pays once.
- **Voice change:** `tests/test_h3_switch.py:test_a_voice_change_waits_for_cost_consent_then_refilms_every_scene`.
- **Adapters, against a local HTTP server:** `tests/test_providers.py`
  - ElevenLabs: the form fields sent, only `word` entries kept, a 503 retried, no words refused,
    a missing duration measured.
  - fal: the music is fetched, a 5xx is retried, a 4xx is not.
- **Music and assembly:** `tests/test_producer_music.py` (length, reuse, moods, restart);
  `tests/test_producer_assemble.py` (whole clips, music under the voice, captions).

**What they can't see:**
- The fake Scribe (`gateway/fake.py:FakeModel.transcribe`) hears exactly the scripted text at a
  fixed pace. Real Scribe's habits never reach a test: "$10" written as digits, punctuation stuck
  to words, fillers, misheard brand names, the language it guesses.
- Every fake H3 clip with speech tags gets the same constant tone (`FakeModel.submit_broll`). So
  these never happen in a test: a silent clip, a clip with no audio stream, speech cut off at the
  end, a loudness difference, another voice, music H3 added.
- No test covers what the ad keeps when the 6-clip cap ends on word fails.
- No test or eval measures whether `gpt-6-luna` passes and fails the right differences.
- The fake music is a hum, so vocals in the music can't happen.

---

## The concepts, ranked by impact on this build

### 1. Severity-tiered checks (hard vs soft gates): **must apply now**

**What it is**
- Not every failed check is the same size. A soft fail ("the motion looks stiff") can ship with
  a warning. A hard fail ("she says the wrong price") must never ship.
- Each check gets its own on-fail rule: retry, fix, ship with a warning, or refuse to ship.

**Why it matters here**
- When the 6-clip cap ends the run, or the agent gives up, the scene keeps its **last** try in
  the ad, whatever failed it: `jobs/work.py:_as_the_last_try_filmed_it` takes `landing[-1]`,
  called from `_ended` and `agents/scene_agent/__init__.py:finish`.
- A word-check fail and a judge fail lead to the same end (`agents/scene_agent/lifecycle.py:_passes`).
- So a clip where H3 said "twenty dollars" for "$10" can reach the finished ad.
  - The caption still shows "$10" (`jobs/assembly.py:timed_script` times the written line).
  - So the viewer reads one price and hears another.
- Case: try 5 has the right words but the judge fails it. Try 6 passes the judge but says the
  wrong price. Try 6 goes in the ad.
- #327 user story 7: "a wrong name, price or claim never reaches my customers". ADR 0002 makes
  facts the hard rule.
- **Known in part:** `docs/limitations.md` "When the fixing agent gives up, the ad keeps a clip
  the judge failed". ADR 0012 widened that limitation to wrong words without a decision on it.

**How to apply it in this build** (Julian's call: what reaches the ad and what the owner is told)
- Make word fails hard and judge fails soft.
- When choosing the try to keep, take the newest try whose word check is not "fail". Only a
  judge fail may ship.
- If every try failed its words, don't put any of them in the ad. Use the existing
  `SCENE_AGENT_NEEDS_CHOICE` path ("This scene waits for your choice") and name the wrong words
  ("she says twenty dollars, the line says $10").
- One chat-flow test: 6 word fails, then the card and the ad show no word-failed clip.

**One real post:** Guardrails AI docs, "Validator on-fail actions": each validator has its own
action, from `NOOP` (log and pass) to `REFRAIN` ("returns None instead of output that isn't safe
to return"). https://www.guardrailsai.com/docs/concepts/validator_on_fail_actions

---

### 2. Transient vs persistent failures (retry only what a re-film can fix): **must apply now**

**What it is**
- A transient failure can go away if you try again: H3 slurs a word once.
- A persistent failure comes back every time with the same inputs. Retrying it only spends money.
- Before you retry, ask whether the cause can change on the next try.

**Why it matters here**
- After a word fail, filming again re-sends the same line, voice words and clip length:
  - `jobs/work.py:sent_handoff` sets `seconds = h3_clip_seconds(len(step.line.split()))` every
    time;
  - the video form never touches the spoken words (`agents/scene_forms.py:VideoForm`);
  - only a new line through `change_scene` (fact checked) changes them, and nothing points the
    agent there after a word fail.
- `agents/scene_agent/instructions.py:INSTRUCTIONS` says nothing about word fails: what they mean,
  or that a re-film won't change the clip's length.
- Persistent causes in this code:
  - **The clip is too short for the line.** Clip length counts raw tokens
    (`jobs/checks.py:h3_clip_seconds`). Worked example (run locally): "Grab yours for $129.99 today
    and save 25% before midnight." is 10 tokens → a 5 s clip. Said aloud it is about 17 words,
    6.8 s at the fixed 2.5 words/s. Every re-film gets the same 5 s. What H3 does with too many
    words is untested (cut, rush or garble: `ai-video-model.md` concept 1), but whatever it does
    to fit, a re-film gives it no more room.
  - **A name Scribe always mishears** (concept 4).
  - **Voice words that ask for a slow pace** (see Bugs).
- Six fails cost about 6 × $0.71–0.87 = $4.3–5.2 for one scene with a 9–11 s clip. The 5 s
  example above costs 6 × $0.40 ≈ $2.4. Then concept 1's end puts a failed clip in the ad.
- `gateway/gateway.py:call_model` with `pay_once` hands back the same verdict for the same
  words. That is correct, but it shows the repeat is not new evidence.

**How to apply it in this build** (Julian's call: money and what the agent may do)
- Classify each word fail from the transcript, in a pure function:
  - **Ran out of time:** the last heard word ends within about 0.3 s of the clip's end, and the
    missing words are the line's tail. That is persistent. Don't re-film at the same length.
    Either the clip length uses a spoken-word count (summary #36), or the line is shortened, or
    the owner is asked.
  - **The same difference on two tries:** persistent. Stop re-filming for words, keep the best
    try (concept 1), and tell the owner.
  - **Anything else:** transient. One more re-film is reasonable.
- Put the class in the film result, beside "Word check: fail", so the agent sees it
  (`agents/scene_agent/lifecycle.py:_told`).
- Measure first: in round 1, count the word fails that repeat on the next try.

**One real post:** Microsoft Azure Architecture Center, "Transient fault handling": "Don't retry
operations that can't succeed", and "Create a plan to handle operations that continue to fail at
every attempt." https://learn.microsoft.com/en-us/azure/architecture/best-practices/transient-faults

---

### 3. Fail-open vs fail-closed (chosen per cause): **must apply now**

**What it is**
- When a check can't decide, it either lets the work through (fail open) or stops it (fail
  closed).
- Pick per cause. "The checker is down" and "the checker looked and found nothing" are
  different facts.

**Why it matters here**
- Julian chose fail open for "Scribe or the word model gives no answer" (ADR 0012, 22:01, "don't
  block the things"). The code also fails open when Scribe *did* answer:
  - A clip with **no audio stream**: `jobs/work.py:_clip_sound` runs ffmpeg `-vn`. I ran it
    locally on a silent mp4: "Output file does not contain any stream", exit 234.
  - A clip whose sound has **no words**: `gateway/gateway.py:transcribe` raises
    `ValueError("… heard no words in the audio")`.
  - Both are caught by `jobs/work.py:transcribe_clip` (`except Exception` → None), so
    `jobs/word_check.py:check` gives "unchecked".
  - `agents/scene_agent/lifecycle.py:_passes` doesn't fail "unchecked". So a scene where H3 never
    spoke passes, goes in the ad, and the owner is told only "Please listen to the clip yourself".
- The clip judge looks at frames only (`jobs/clip_judge.py:cut_pictures`; no sound is sent), so
  nothing else hears the silence.

**How to apply it in this build** (Julian's call: what fails a clip and what the owner is told)
- Split the causes in `transcribe_clip`:
  - Service down, timeout, safety filter, unreadable answer: unchecked, fail open (his choice).
  - No audio stream, or Scribe heard no words: a word-check **fail**, "the clip has no speech".
    It counts against the cap, and the agent films again.
- One chat-flow test: the fake H3 returns a silent clip, and the try fails.

**One real post:** Teiva Harsanyi (Google), "Fail Open, Fail Closed": the choice "depends on the
system and its risks", and his team's Google service fails closed when it can't make a fully
informed decision. https://read.thecoder.cafe/p/fail-open-fail-closed

---

### 4. Contextual biasing for speech-to-text (keyterms and a known language): **must apply now**

**What it is**
- You tell the speech-to-text model which rare words to expect, such as the product's name, and
  which language it is hearing. Brand names are what transcribers get wrong most often.
- Bias only on the names. Biasing on the whole line makes the transcriber hear what you hoped
  for, and then the check checks itself.

**Why it matters here**
- `gateway/elevenlabs_adapter.py:ElevenLabsProvider.transcribe` sends no `keyterms` and no
  `language_code`, so Scribe guesses the language. H3 is told `[English]` in every prompt.
- The repo already saw it: `jobs/assembly.py:timed_script`'s docstring says Scribe wrote "Beard
  Brand" for "Beardbrand".
  - `jobs/word_check.py:tidy` makes those "beard brand" and "beardbrand", so they differ.
  - That costs a model call, and the model may call it "a different name": a paid re-film,
    possibly up to the cap (concept 2).
- The word model isn't told which words are the product's name either. `jobs/word_check.py:Words`
  holds only `line` and `said`.
- Every plan must say the product's name in at least one line, refused in code otherwise
  (`jobs/planning.py:Plan._name_said`).

**How to apply it in this build** (Julian's call: what the check sees; the extra cost is tiny)
- Send `keyterms` with the plan's product name as the ad says it, and any brand words from the
  line, plus `language_code="en"`.
  - Scribe v2 batch takes up to 1,000 keyterms of at most 5 words each.
  - The API reference (https://elevenlabs.io/docs/api-reference/speech-to-text/convert) lists
    a 20% surcharge: about $0.0007 → $0.0008 a clip.
- Don't send the whole line as keyterms (the self-check problem above).
- Add `names` to `Words` so the model knows which words must be said exactly.
- Record the code-match share before and after, from the `WORD_CHECK` records.

**One real post:** ElevenLabs docs, "Keyterm prompting" (Scribe v2 batch; the model "uses
surrounding context" to decide when to write a term).
https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/batch/keyterm-prompting

---

### 5. Online evaluation (code metrics on every clip's transcript): **good to know**

Retagged: nothing is lost today. Every transcript row keeps its word times in the database, so
these numbers can be worked out after round 1 for free.

**What it is**
- Score every real output as it is made, with cheap code, and keep the numbers for later.
- You don't wait for a graded round to learn what the model is doing.

**Why it matters here**
- Three open choices rest on round 1's evidence:
  - "The first round's transcripts give the real speed, which replaces 2.5" (spec #327, build
    question 1; ADR 0012 says the grades "may change the fixed speed")
    (`jobs/checks.py:FIXED_WORDS_PER_SECOND`);
  - "whether long silent endings need cuts" (ADR 0012);
  - whether voice drift is too much (ADR 0012).
- The data exists: `jobs/work.py:_transcribe_clip` keeps every word's start and end. Only the
  captions are timed from it (`jobs/assembly.py:timed_script`). Nothing measures speed, silences
  or gaps from it.
- `evals/round_export.py` exports each clip's ruling, so the heard text, the word-check verdict
  and `settled_by` go out (`word_check` in `ruling-<clip>.json`). Word times don't. The grading
  page (`evals/grading_page.py`) shows no word-check verdicts.
- Creatify's known H3 weak spot, "long lines garble in the middle" (#327 Further Notes), would
  show as long gaps between words.

**How to apply it in this build** (Julian's call only when a number changes the 2.5 constant or a
rule)
- One pure function per clip, from `transcript.words` and `clip.seconds`. Record its output as a
  developer record, not a failure, and add it to the round export:
  - spoken words per second (use a spoken-word count, summary #36);
  - silence before the first word, and after the last word (the plan wants about 1 s);
  - the last word's end against the clip's end (cut off?);
  - the longest gap inside the line;
  - `settled_by` (code or model) for the word check.
- It's free: no model calls, and it runs on data already stored.

**One real post:** LangChain docs, "Set up online code evaluators": "Online evaluations provide
real-time feedback on your production traces", written as plain code.
https://docs.langchain.com/langsmith/online-evaluations-code

---

### 6. Word-level confidence scores: **good to know**

**What it is**
- Speech-to-text models give each word a confidence (here a `logprob`): how sure they are of it.
- A transcriber's language model can "repair" a slurred word into the word it expected. The low
  confidence is the trace of that.

**Why it matters here**
- Scribe returns `logprob` per word (ElevenLabs API reference:
  https://elevenlabs.io/docs/api-reference/speech-to-text/convert).
  `gateway/elevenlabs_adapter.py:ElevenLabsProvider.transcribe` drops it, and
  `gateway/types.py:Word` has no field for it.
- So when a price H3 garbled is transcribed as the right price, the match is exact and free
  (`jobs/word_check.py:check`). It passes, and nothing shows it was unclear.
- When a word differs, the model can't tell "clearly said wrong" from "unclear audio".

**How to apply it in this build** (Julian's call if it ever changes a verdict)
- Keep `logprob` on each stored word.
- Hand the word model the low-confidence words, marked, and record them with the verdict.
- Set a cut-off only after Julian has graded some clips: measure precision and recall on his
  marks, not a vendor's number.

**One real post:** Deepgram docs, "Understanding word confidence scores": flag the low-scoring
tail for review; low confidence on names and numbers matters more; pick the cut-off on your own
data. https://developers.deepgram.com/docs/confidence

---

### 7. Speaker embeddings (measuring voice drift and the voice's gender): **good to know**

**What it is**
- A small local model turns a voice into a vector. The cosine similarity of two clips' vectors
  says how alike the two voices sound.
- It's the same tool speaker verification uses.

**Why it matters here**
- Only words keep the voice the same: the same voice words in every prompt
  (`agents/scene_forms.py:video_prompt`, `jobs/work.py:_how_the_voice_sounds`).
- ADR 0012 accepts drift. If round 1 "shows too much drift", the fallback is "our voice first,
  then lip sync". Today only Julian's ears measure it.
- Spec #1's history: in 6 of 21 finished ads a woman on screen spoke with a man's voice. The
  gender word now goes into every H3 prompt, but nothing checks the result.
- ADR 0012 puts "checking … voice drift in code" out of scope. Measuring for grading is not
  gating.

**How to apply it in this build** (Julian's call: anything beyond a recorded number)
- Free and local: embed each clip's WAV (`_clip_sound` already makes one) with Resemblyzer.
  Record its similarity to the ad's first clip, per ad, on the grading page.
- Before designing the fallback: fal's page for Creatify's general Boreal model (not H3) lists
  `audio_url` "driving audio", "preserved in the output instead of generated speech". Check
  whether H3 has it. https://fal.ai/models/creatify/boreal/llms.txt

**One real post:** Resemble AI, Resemblyzer: "compare different voices and get a value on how
similar they sound", from 5–30 s of speech; Apache-2.0. https://github.com/resemble-ai/Resemblyzer

---

### 8. Audio event classification (music and singing in the sound): **good to know**

**What it is**
- A small model labels what is in a piece of audio: speech, music, singing, footsteps.

**Why it matters here**
- Two sounds in the ad are asked for in words, and nothing checks either:
  - H3 is told "No music" (`agents/scene_forms.py:video_prompt`), and #327 notes H3 "invents
    music or murmuring" without concrete sounds.
  - Sonilo is told "no vocals, no singing" (`jobs/work.py:MUSIC_PROMPT`).
    `gateway/fal_adapter.py:FalProvider.compose` keeps whatever comes back.
- `jobs/assembly.py:join` mixes the clip's whole sound with the music bed. Music H3 added plays
  on top of the music bed.

**How to apply it in this build** (Julian's call: whether a score fails a clip or remakes the
music)
- Free and local: run YAMNet on each clip's sound and on the music bed. Record the "Music",
  "Background music" and "Singing" scores (AudioSet classes 132, 262, 24).
- Grade them against Julian's ears in round 1 before any rule uses them.

**One real post:** TensorFlow, "Sound classification with YAMNet": 521 AudioSet classes, mono
16 kHz input. https://www.tensorflow.org/hub/tutorials/yamnet

---

## Also seen here

- #36 Text normalization before length estimates: `jobs/checks.py:h3_clip_seconds` is fed raw
  `len(line.split())` by `jobs/work.py:sent_handoff`, `music_seconds`,
  `agents/scene_agent/lifecycle.py:_validate_line` and `agents/voice_changes.py:consent`. It now
  sets H3's paid length. `jobs/word_check.py:tidy` already writes numbers out, but it says
  "$24.99" as "twenty four point nine nine dollars", so it can't be the counter as it stands.
- #41 ASR round-trip check: built as `jobs/word_check.py:check` and `tidy`, with its own
  normaliser, not shared with `jobs/assembly.py:_plain`. The model gets the whole texts, not the
  aligned difference, and no WER is recorded.
- #29 Loudness normalisation: `jobs/assembly.py:join` and `music_filters` still mix with
  `normalize=0` and no `loudnorm`. Now each H3 clip brings its own level of voice and room sound.
- #71 Forced alignment: `jobs/assembly.py:timed_script` times the written line from each clip's
  Scribe words with `difflib`.
- #1 Idempotency keys: `gateway/fal_adapter.py:FalProvider.compose` is a blocking `fal.run` POST
  with a 300 s timeout. An httpx timeout is a `TransportError` → `OutsideServiceDown` →
  `adforge/retry.py:with_retries` asks again, so the music can be paid for twice.
- #48 Webhooks instead of polling: the music uses the blocking endpoint
  (`gateway/fal_adapter.py:FalProvider.compose`); `settings.FAL_QUEUE_URL` is still unused.
- #9 Golden dataset: `jobs/word_check.py:check` records every verdict as `WORD_CHECK`. Nothing in
  `backend/evals/` reads them, so there is no graded set of (line, heard, right verdict).
- #34 Calibrating an LLM judge: `jobs/word_check.py:QUESTION` on `gpt-6-luna` has never been
  graded. `evals/grading_page.py` shows no word verdicts for Julian to mark.
- #53 Cost-sensitive decision threshold: `jobs/word_check.py:QUESTION`. A wrong fail costs a
  re-film ($0.71–0.87). A wrong pass ships a wrong fact.
- #58 Feedback-driven retries: `agents/scene_agent/lifecycle.py:_told` gives the agent the line,
  what was heard and why, but the agent has no way to change the speech (concept 2).
- #43 Independent evidence for checks (done right): `agents/scene_agent/film.py:_check_words`
  checks the clip's own transcript, not the prompt. Concept 4 keeps it that way by not biasing
  Scribe on the line.
- #37 Constrained decoding (done right): `jobs/word_check.py:Answer.verdict` is a `Literal`.
- #20 Cost reconciliation: `gateway/gateway.py:transcribe` raises "no words" inside `_recorded`,
  so a Scribe call ElevenLabs billed is stored with `cost_usd` None.
- #33 Contract tests against the real thing: `gateway/fake.py:FakeModel.transcribe` hears exactly
  the scripted text, and `FakeModel.submit_broll` gives every H3 clip the same tone.

---

## Bugs and loose ends

- **A clip with no speech passes.** No audio stream (`jobs/work.py:_clip_sound`, ffmpeg exit 234,
  run locally), or Scribe heard no words (`gateway/gateway.py:transcribe`). Either way
  `jobs/work.py:transcribe_clip` returns None, the clip is "unchecked", and
  `agents/scene_agent/lifecycle.py:_passes` keeps it. The ADR says unchecked only when there is
  "no answer". See concept 3.
- **`tidy` misses writing-only differences** (run locally on `jobs/word_check.py:tidy`):
  - curly `Don’t` vs straight `Don't`;
  - an em dash between spaces (`works — fast`);
  - `&` vs "and";
  - `3-in-1` becomes "three - in - one";
  - `24/7` keeps the "/";
  - `$24.00` becomes "twenty four point zero zero dollars";
  - `$9.99` never matches "nine ninety-nine";
  - `2nd` becomes "twond".

  Each sends a free match to the model. That lowers the code-match share #327 user story 51 asks
  Julian to measure.
- **The speech tags aren't escaped.** `agents/scene_forms.py:video_prompt` puts the line raw into
  `<d>[English] {line}</d>`. A line with `<`, `>`, `[` or `]` may break H3's tag; the repo's own
  word-check tests use "Contains <5% sugar". How H3 parses tags isn't known here. Check before
  round 1.
- **The voice sentence is broken.** `video_prompt` plus `jobs/work.py:_how_the_voice_sounds`
  write "On camera, the presenter says with A woman's voice. A warm, relaxed woman…".
- **The voice words carry a pace.** `jobs/planning.py` asks for `person_voice` as "age, accent,
  tone, pace". A "slow" pace fights the fixed 2.5 words/s that sets the clip's length.
- **The word check is recorded twice after a worker stop.** `jobs/word_check.py:check` posts
  `WORD_CHECK` before `agents/scene_agent/film.py:_check_words` saves the ruling. A stop between
  the two re-runs the check and records it again (`pay_once` stops it paying twice), which skews
  the code-match share.
- **`WORD_CHECK` is outside `notices.KINDS`.** It is defined in `jobs/word_check.py`, so
  `tests/test_who_sees_a_notice.py` never covers it.
- **Round 1's speed has no exported data.** `evals/round_export.py` exports the heard text
  inside each clip's ruling, but no word times, and `evals/grading_page.py` shows no word verdicts.
- **Dead code:**
  - `agents/producer.py:_heard_otherwise` and `_letters_and_digits` are never called.
  - In `agents/scene_agent/__init__.py:_conversation`, `seconds = audio.seconds …` is overwritten
    on the next line.
- **Stale words:**
  - `agents/producer.py:CreateMusic` still says the music lasts "as long as the person's voice
    takes to say the script with a few seconds to spare". `jobs/work.py:music_seconds` sums the
    clips.
  - `docs/limitations.md` "A new line's clip is made again" still says the voice and transcript
    are made again, and that "a B-roll line is said to camera".
- **The music can end before the ad.** `agents/producer.py:assemble_the_ad` uses the latest music
  whatever its length. After a longer line the bed fades out early (`jobs/assembly.py:music_filters`)
  under the last scene, the call to action. Spec #1 allows this.

---

## Checked by an independent reader

Checked against the code at `fd346df`, Creatify's and ElevenLabs' pages not re-read except where named.

- Severity-tiered checks · CONFIRMED · nothing changed. `_as_the_last_try_filmed_it` keeps `landing[-1]`; `finish` (cap and give-up) calls `work._ended`; `_passes` fails word and judge fails alike; captions time the written line.
- Transient vs persistent failures · CORRECTED · the 5 s worked example costs about $2.4 for six tries, not $4.3–5.2 (that is for 9–11 s clips); "the end is cut every time" became "a re-film gives it no more room", since what H3 does with too many words is untested.
- Fail-open vs fail-closed · CONFIRMED · nothing changed. Ran ffmpeg `-vn` on a silent mp4 here: exit 234; `transcribe_clip` catches every error and returns None, which `check` makes "unchecked".
- Contextual biasing for speech-to-text · CONFIRMED · only the cite fixed (below). Also backed by the first run: Scribe split or misspelt three brand names (`docs/runs/first-run-trace-findings.md` item 3).
- Online evaluation · CORRECTED, retagged good to know · the "replaces 2.5" quote is spec #327 build question 1, not ADR 0012; the round export does carry the heard text and verdict in each clip's ruling, just no word times; captions are computed from the word times. Nothing is lost today, as the words stay in the database.

Cite fixes:
- `jobs/planning.py:_name_said` → `jobs/planning.py:Plan._name_said` (it is a validator on `Plan`).
- Every other `file:symbol` cite in this report names a file and a symbol that exist.

Flagged, not changed (outside the concepts): "Bugs and loose ends" still says `evals/round_export.py` "exports no transcripts"; it exports the heard text inside each ruling, but no word times.
