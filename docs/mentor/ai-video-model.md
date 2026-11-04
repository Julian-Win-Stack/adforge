# Design mentor: the video model, H3 for every scene

**Read:** ADR 0012 in full; spec #327; spec #210 ("Cut-and-send", "Contracts");
`docs/creatify-ad-lessons.md`; in `docs/adr/0012-legacy/`: `video-model-tests.md`,
`creatify-problems.md`, `real-api-replies.md`, `plans/broll-boreal-h3.md`; `docs/rate-limits.md`;
`docs/limitations.md`; CONTEXT.md (Clip, Reference mode, Sent list, Piece, Talking scene, B-roll
scene); Creatify's current Boreal-H3 API pages. Code at `fd346df` (main merged in); backend paths
below are under `backend/`.

Teaching only: nothing in the app changed. Applying any concept is a design change and needs
Julian's yes first. The paid-call side (idempotency, cost reconciliation, webhooks, circuit
breaker) is in `gateway.md` and isn't taught again. H3's voice, the transcript and the word check
are in `ai-voice-words.md`.

---

## Systems in this group

Cost per ad is an **estimate**: catalog prices times the calls the code makes. It is not a bill.
A line of 20–25 words gets a 9–11 s clip (`jobs/checks.py:h3_clip_seconds`). An ad has 3–4 scenes.

| System | What it is asked | Model, and why | In → out | What checks its output | Eval | How it fails | Cost per ad (est.) |
|---|---|---|---|---|---|---|---|
| **make_broll_clip / collect_broll_clip** (every scene's clip) | `jobs/work.py:sent_handoff` turns the scene agent's sent list into a handoff. Start-frame mode: the checked starting picture as `image_url`. Reference mode: that picture as Image 1 plus one page-video piece (`settings.BROLL_MOST_PIECES = 1`). Always `prompt_enhancement=False`. Seconds come from `h3_clip_seconds(len(step.line.split()))`. Sent by `gateway/creatify_adapter.py:CreatifyProvider.submit_broll` at 768p; `aspect_ratio` "9:16" only in reference mode. | `creatify/boreal-h3` for both (`gateway/catalog.py:MODEL_FOR_PURPOSE`): "H3 renders are submitted, then collected; only submission is paid." Why: ADR 0012 (she can demonstrate while talking; one path with the judge and fixes; the Creatify first versions Julian liked). B-roll moved to H3 in `plans/broll-boreal-h3.md` Phase 2. H3 was never tested head to head for talking: `video-model-tests.md` tested talking-photo models fed our audio. | `gateway/types.py:BrollClipHandoff` (picture XOR up to 9 example pictures; pieces ≤ 15 s in all; whole seconds 5–15; prompt) → task id. `ClipCollectHandoff(video_id)` → an mp4 key with H3's sound (`gateway/gateway.py:collect_clip`). Real length is measured after (`jobs/work.py:_make_clip` → `assembly.seconds_of`). | Code: the handoff validators; `CreatifyProvider.status` maps `failed`/`rejected`. Nothing compares the returned length with the length asked. Models: the clip judge (`jobs/clip_judge.py:judge_clip`) and the word check (`jobs/word_check.py:check`), both run from `agents/scene_agent/film.py:Film.run`. Nothing checks lips, voice or music. | None. Julian's graded rounds are the only measure (`evals/round_export.py`, `evals/grading_page.py`), and round 1 on H3-voiced ads hasn't run. The blind A/B harness (`video-eval/blind.py`, `arms.json`) was built for the old Boreal on fal. | `CreatifyProvider._send`: a 4xx (not 429) raises `ClipFailed` "refused"; a 5xx (not 503) or a lost reply raises `ClipFailed` and is never resent; 429, 503 and connect errors raise `OutsideServiceDown`, which is retried. `collect_clip` polls every 10 s with no deadline; after 600 s the chat gets an agent line (`_make_clip.tell_its_slow`), not a notice. In the film tool a `ClipFailed` becomes a tool answer (`agents/loop.py:why_it_failed`). **No notice Kind is posted**, and the try still counts. | `catalog.video_cost_usd`: $0.0792/s × (clip + piece seconds). About $0.71–0.87 per try, plus $0.16–1.19 for a piece; collecting costs $0. First tries only: about $2.1–3.5 an ad. The cap is 6 clips per run (`film.py`), so the ceiling is about $21 an ad without pieces and $49 with full pieces. Creatify's pages disagree on the price (see "Also seen here" #20). |
| **What H3 is sent** (the prompt, the sent list, the film tool) | The scene agent fills `agents/scene_forms.py:VideoForm` (11 boxes) through `agents/scene_agent/film.py:Film`, under `agents/scene_agent/instructions.py:INSTRUCTIONS`. Code wraps the boxes in `scene_forms.video_prompt`: "Film this in real-time speed.", the UGC opener, the boxes joined, "After the last word, …", "The clip starts exactly like Image 1." (reference mode only), the lock line, the voice words (`jobs/work.py:_how_the_voice_sounds`), `<d>[English] {line}</d>`, then "Sound: … No music." | The prompt's writer is `scene_agent` → `gpt-6.1-sol` (`catalog.MODEL_FOR_PURPOSE`; ADR 0011/0012: one agent per scene). Its reader is H3. No comment or ADR gives a reason for the prompt's order beyond "Creatify order" (`tests/test_scene_forms.py`). | `jobs/library.py:SentList`: `mode` (start_frame/reference), `starting_picture`, `pieces`, `prompt`, `voice_words`, `picture_photos` (for the judge only), `scene_run`. Spec #210's tiers (`pictures`, `left_out`, `recolours`) are empty for new clips: `Film.run` builds `pictures=[]`. The list is kept on the try (`AgentFixTry.sent`) and on the clip (`library.keep_sent_list`). | Before paying, `Film.run` refuses: a picture that isn't passed or unchecked; a stale picture (`jobs/scene_pictures.py:matches`); a run's 7th clip; negative words in the boxes (`lifecycle.BANNED`); a line outside 4–14 s (`lifecycle._validate_line`); a piece the agent hasn't looked at (`agents/scene_agent/reference.py:pieces_for`). Also `SentList._within_the_video_apis_limits`. Nothing checks the prompt's length (the form only advises "about 106 to 150 words"), and code's own lines skip the ban. | None. No test measures whether H3 follows each line of the prompt. `docs/limitations.md`: about half the B-roll prompt rules were never graded. | A refusal comes back as a tool answer and nothing is paid. A piece too short to send falls back to start-frame mode with a `PIECE_NOT_SENT` developer notice (`jobs/cut_and_send.py:_say_piece_left_out`). | Building the prompt is free. The agent's turns belong to the agent-architecture group. Each film call buys one H3 render (row 1), one Scribe call (about $0.0006, `catalog.transcription_cost_usd`) and the judge's calls. |
| **Talking scenes on H3** (ADR 0012: lips, voice and motion from one clip) | The same film tool. With `kind == "talking"`, `video_prompt` writes "On camera, … The presenter's mouth moves in sync with the words." The fallback order is in `INSTRUCTIONS`: fix the cause, then a simpler action, then showing the product to the lens. The picture is drawn from the person-and-room picture (`jobs/work.py:person_shown_for`, `drawn_for`). The first scene stays talking (`jobs/planning.py:check_opening_kind`). | `creatify/boreal-h3`, one render for picture, voice and lips (`jobs/work.py:_clip_purposes` returns the same purposes for both kinds). Why: ADR 0012: "HeyGen moves lips but ignores motion text." | As rows 1–2. Out comes a clip with her voice; its transcript is kept with the clip (`jobs/work.py:transcribe_clip`). | The judge is told she talks (`jobs/clip_judge.py:check_words` adds `TALKING_SCENE_CONTEXT`). The word check checks the words. Lips and voice go unchecked by design (#327 Out of Scope). | None. The old human rubric's "lips_in_step" (`video-eval/blind.py:RULES`) is legacy. | As rows 1–2. A give-up offers "send a photo, or give us a different line" (`jobs/work.py:scene_way_out`), through `SCENE_AGENT_GAVE_UP` or `SCENE_AGENT_NEEDS_CHOICE` (`agents/scene_agent/__init__.py:finish`). | The same price per second as B-roll. HeyGen and Inworld spend is gone. Scene 1 is always talking, so every ad has at least one talking clip. |

---

## How it is tested today

- **The adapter, against a local stand-in server** (`tests/test_providers.py`):
  - the exact request body for start-frame, example pictures and pieces;
  - `prompt_enhancement: "none"`;
  - billing: 7 s comes to $0.5544;
  - `failed` and `rejected` raise `ClipFailed` with Creatify's reason;
  - a lost reply isn't resent, and a 503 is.
  - It replays `tests/creatify/*.json`: two finished clips from Oct 3 (B-roll prompts with no speech tags, `prompt_enhancement: "auto"`) and one refusal.
- **Pure logic:**
  - `tests/test_scene_forms.py` pins the exact prompt text for both kinds: speech tags, sound line and after-last-word.
  - `tests/test_h3_clip_length.py` pins 0–10 words → 5 s, 20 → 9, 25 → 11 and 35 → 15, and refuses 36.
- **Through the chat, outside services faked:**
  - `tests/test_h3_scene_film.py`, for both kinds, with and without a worker restart. H3 is handed 5 s, enhancement off, the line in speech tags, the voice words, "On/Off camera" and "No music". No voice is designed. The transcript comes from the clip's own sound.
  - `tests/test_scene_film.py`: the checked picture is H3's only input; a judged fix happens in the same chat; a restart doesn't pay twice; the quality switch; banned words are refused before paying.
  - `tests/test_scene_reference.py`: reference mode sends the picture and the piece only; a piece too short falls back with a notice; the agent must look at the moment first.
  - `tests/test_h3_scene_words.py`: a word fail is filmed again; six fails end the run (the same form, filmed 6 times).
  - `tests/test_h3_switch.py`: every scene is made on H3; a voice change waits for consent; the way out depends on the kind; old clips and new H3 clips play together.
  - `tests/test_scene_talking_agent.py`, `tests/test_retired_scene_steps.py`.
- **What they can't see.** `gateway/fake.py:FakeModel.submit_broll` returns a tone exactly `seconds` long whenever speech tags appear, and Scribe is scripted. So nothing tests:
  - how fast H3 really speaks against the clip's length: cut-off, rushed or garbled words;
  - whether H3 reads code's words aloud ("Off camera, the presenter says with A woman's voice…");
  - whether "No music" holds;
  - lip sync, or a mouth appearing in off-camera B-roll;
  - the face or voice drifting between clips;
  - whether reference mode really starts on Image 1;
  - Creatify's content checker refusing real ad prompts;
  - the real price, render time and concurrency.
- No real H3 reply with speech has ever been recorded.
- No test covers a `ClipFailed` inside the film tool (none would find a notice).

---

## Concepts, ranked by impact on this build

| # | Concept | Tag |
|---|---|---|
| 1 | Isochrony: fitting speech into a fixed time slot, with headroom | **must apply now** |
| 2 | Conditioning modes: image-to-video, first–last frame, reference-to-video | **must apply now** |
| 3 | Canary release with go/no-go criteria | **must apply now** |
| 4 | Sampling variance and pass@k (re-roll vs re-prompt) | good to know (retagged) |
| 5 | Yield-adjusted cost (cost per usable clip) | **must apply now** |
| 6 | Negation in prompts (the pink-elephant problem) | good to know |
| 7 | Prompt upsampling (the provider's prompt rewriter) | good to know |
| 8 | Anti-corruption layer (a provider-neutral clip request) | good to know |
| 9 | Audio-visual sync scoring (lip-sync metrics) | good to know |

---

### 1. Isochrony: fitting speech into a fixed time slot, with headroom · **must apply now**

**What it is**
- The clip's length is fixed before H3 says a word, and the words must fit inside it.
- Dubbing teams call this isochrony.
- You estimate how long the speech takes, then leave a margin for the estimate being wrong.

**Why it matters here**
- `jobs/checks.py:h3_clip_seconds` sets the length to ceil(words ÷ 2.5) + 1, at most 15 s. `jobs/work.py:sent_handoff` sends it as H3's `duration`.
- The +1 s is for her action after the last word (ADR 0012), not a margin for pace. The only slack left is the rounding.
- 2.5 words/s is at or near the top of what has been seen:
  - Creatify's voice spoke 1.6–2.5 words/s (`creatify-problems.md` #1);
  - Creatify fit "17 to 27 words in 10 s" (#327 build question 1): 1.7–2.7 words/s. The top of
    that range is faster than 2.5, which hints H3 may fit its pace to the slot.
- Worked from the formula:

  | Words | Clip | Words run past the end below | At 2.0 words/s |
  |---|---|---|---|
  | 20 | 9 s | 2.22 words/s | 1 s over |
  | 25 | 11 s | 2.27 words/s | 1.5 s over |
  | 35 | 15 s | 2.33 words/s | 2.5 s over |

- H3's `duration` is a fixed field: whole seconds, 5–15 (Creatify's API page). Nobody knows what H3 does with too many words. It may cut off, rush, or garble ("long lines garble in the middle", #327 Further Notes), or hand back a longer clip than asked (Creatify's own agent: "4 seconds requested came back as 5 to 10", `creatify-problems.md` #1).
- `agents/scene_agent/__init__.py:_conversation` tells the agent "The line takes N s" from the same estimate, as if it were a fact.
- The fake hides all of this (see "How it is tested today"). `FakeModel.clips_short_by` can make clips shorter, but no test sets it, and no fake clip is ever longer than asked.
- B-roll is off camera, which dubbing treats as looser. But ADR 0012 plays every clip whole, so a B-roll overflow also loses words.
- `ai-voice-words.md` concepts 2 and 5 cover the other half: classifying a cut-off tail as a persistent failure, and measuring the real speed.

**How to apply it in this build** (Julian's call: a threshold and money)
- Set the clip's length at a slower pace than the line's target. For example, keep writing lines for 2.5 words/s, but work out the clip's seconds at 2.1 words/s. That adds about 1–2 s a clip: $0.08–0.16 a try at catalog price.
- Lines over about 30 words would then need shortening to stay inside 15 s. That is a threshold change.
- Once real transcripts exist, set the rate from a slow percentile (the 10th–20th), not the mean.
- In the smoke run (concept 3), pick scenes on purpose at about 20, 28 and 35 words. That shows what H3 does when the words run long.

**One real post:** Amazon Science, "Isochrony-aware neural machine translation for automatic dubbing"
(Interspeech 2022): "ensuring that target speech segments have roughly the same duration of the
corresponding source speech segments."
https://www.amazon.science/publications/isochrony-aware-neural-machine-translation-for-automatic-dubbing

---

### 2. Conditioning modes: image-to-video, first–last frame, reference-to-video · **must apply now**

**What it is**
- A video model can be steered by different inputs, and each one pins different things:
  - **Image-to-video:** your picture *is* frame 1. This gives the strongest hold on the face, the room and the product.
  - **First–last frame:** you also give the last frame, and the model fills in between.
  - **Reference-to-video:** pictures, videos and audio are only references. The model draws its own frame 1 from them, so the hold is looser.

**Why it matters here**
- `jobs/work.py:sent_handoff` sends start-frame mode as `image_url`. Reference mode sends the starting picture as `reference_image_urls[0]`, beside the piece.
- In reference mode, `agents/scene_forms.py:video_prompt` adds "The clip starts exactly like Image 1." Creatify's API says "A reference render takes no `image_url` or `end_image_url`." So the passed picture check (`agents/scene_agent/picture_check.py:check`) doesn't fix frame 1 in place. That sentence is only a wish.
- Talking scenes can now get a piece:
  - `jobs/planning.py:H3PlannedScene` inherits `PlannedScene._only_broll_is_sent_pictures`, which keys on `shows is None`. Every H3 scene has a `shows`, so the rule never fires;
  - the planner's text says "Either kind may name drawing photos" and doesn't limit the piece to B-roll;
  - `agents/scene_agent/reference.py:pieces_for` sends a planned piece whatever the kind.
  - In reference mode, her face and lips come from a reference only.
- Features H3 has that the code doesn't use:
  - `end_image_url` (H3 only, needs `image_url`) could pin the "after the last word" ending. It worked once before, on silent B-roll (`tests/creatify/done-from-start-and-end-pictures.json`, Oct 3).
  - `reference_audio_urls` (up to 3, free, reference mode only) could anchor the voice from clip to clip.
- Features H3 lacks: H3 "takes no `audio_url` or `negative_prompt`" (Creatify API page). ADR 0012's fallback, "our voice first, then lip sync", can't run on H3 as documented. It needs another model.

**How to apply it in this build**
- Write a one-page H3 capability table beside ADR 0012: each input, what it pins, its cost, and whether AdForge uses it. That is a doc fix.
- (Julian's call) Talking scenes: start-frame only. Or, if they keep pieces, tell the judge that the clip may not open on Image 1.
- (Julian's call) Two probes in the smoke run, about 4 × $0.79:
  - voice words alone vs the first passed clip's sound sent as "Audio 1";
  - one `end_image_url` scene for the "ends on an action" rule.

**One real post:** Creatify's Boreal-H3 API docs: "`image_url` animates a start frame (image to
video)", "`end_image_url` adds a closing frame", "A reference render takes no `image_url` or
`end_image_url`"; reference audio "needs at least one image or video reference".
https://docs.creatify.ai/api-documentation/boreal/model-boreal-h3.md

---

### 3. Canary release with go/no-go criteria · **must apply now**

**What it is**
- Put a change in front of a small slice first.
- Before the slice runs, write the few signals that decide go or no-go, and what an acceptable value for each one is.
- Then compare the slice with a control.

**Why it matters here**
- #339 switched every new ad to H3-voiced scenes. No H3 clip with speech has been made from AdForge's own prompts (see "How it is tested today").
- The spec plans 3 smoke ads, then 12 (#327 Out of Scope). Nothing written says what the 3 must show before the 12 run.
- The first question is whether H3 speaks at all through this API.
  - The ADR's evidence is Creatify's own agent: "Creatify's agent gives H3 no voice file; it describes the voice in words in every prompt" (#327 Further Notes).
  - Creatify's H3 API page says "Boreal-H3 takes no `audio_url`. To add sound, send `reference_audio_urls` with an image or video reference."
  - Its request schema tells the prompt to include "any dialogue or sound it should contain".
  - AdForge sends no reference audio, so one clip answers this.
- Several unknowns can be read by code, for free, from data the app already keeps:
  - **words past the end:** the transcript's last word against `clip.seconds` (concept 1);
  - **code's words read aloud:** "off camera", "presenter" or "with a woman's voice" in the transcript (`jobs/work.py:transcribe_clip`);
  - **render failures** and their reasons (`ModelCall` rows for `collect_broll_clip`);
  - **start-frame match:** frame 0 against the starting picture, only for start-frame mode.
- Others need Julian's eyes: music, lips, a mouth in B-roll.
- `docs/adr/0012-legacy/video-model-tests.md` is the prior art: $2.90 bought a model pick before v1.

**How to apply it in this build** (Julian's call: the criteria and money)
- One short list, written before the smoke run: each unknown, how it is read, and the bar for each.
  - Example bar: "0 of 9 clips lose words at the end"; "0 clips speak code's words".
- The control: the same 3 products' round-8 ads.
- H3 cost of the 3 smoke ads: about $6–9 each (concept 5), so $19–28. That is an estimate.

**One real post:** Google SRE Workbook, "Canarying Releases": "We define canarying as a partial
and time-limited deployment of a change in a service and its evaluation"; you "must be able to
compare the canary signals to the control signals."
https://sre.google/workbook/canarying-releases/

---

### 4. Sampling variance and pass@k (re-roll vs re-prompt) · good to know

Retagged: it doesn't hurt the build today. It matters when round 1's grades are read, and each
try's prompt and picture are already stored, so re-rolls can be found then.

**What it is**
- The same prompt makes a different clip every time.
- H3 has no seed: none appears in Creatify's request fields.
- One clip is one sample. A prompt's quality is its pass rate over several samples.

**Why it matters here**
- `agents/scene_agent/instructions.py:INSTRUCTIONS` says "Read the clip's frames and judge's words, then fix the cause". The film tool's own description (`agents/scene_agent/film.py:Film`) does allow "fix the picture or film again as needed", but nothing tells the agent when a re-roll is the right move. So a one-off glitch tends to get a prompt change, and nothing can show the change helped.
- `agents/scene_agent/lifecycle.py:_passes` rules on one clip.
- ADR 0012: round 1's grades "may change the fixed speed, the arm's-length rule or whether long silent endings need cuts". One sample per scene can't tell a bad rule from bad luck.
- `tests/test_h3_scene_words.py:test_six_word_failures_end_the_run_and_offer_every_try` films the same form 6 times. So code allows a re-roll with the same prompt. No field marks a try as a re-roll, though each try's prompt and picture are kept (`AgentFixTry.video_prompt`, `AgentFixTry.picture`), so re-rolls can be found after the fact.
- Known: `docs/limitations.md`, "The fixing agent trusts the clip judge".

**How to apply it in this build**
- Record a fingerprint per try: a hash of `SentList.prompt` plus the picture's file. Then the pass rate per prompt can be counted from `AgentFixTry` rows. This is code structure.
- (Julian's call: money) Before a round-1 grade changes a rule, film that scene 3 times with the same input. That costs about $2.40 per probe.
- (Julian's call: what the agent decides) Tell the agent:
  - after a one-off glitch (a melted hand), a re-roll with the same form is a fair move;
  - after a fail that repeats, change the prompt.
- Report round 1 as pass@1 per scene kind and pass@k per scene, with the counts beside them.

**One real post:** Anthropic, "Demystifying evals for AI agents": "Because model outputs vary
between runs, we run multiple trials"; "pass@k measures the likelihood that an agent gets at least
one correct solution in k attempts."
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

---

### 5. Yield-adjusted cost (cost per usable clip) · **must apply now**

**What it is**
- What a scene really costs is the price per try ÷ the pass rate, up to the cap.
- A $0.79 clip that passes half the time costs about $1.58 per usable clip.

**Why it matters here**
- ADR 0012 says the paid run waits for "its exact cost first". Nothing turns a pass rate into a quote before a run. After a round, `evals/results.py` counts each scene's remakes and their cost from the round export, but not spend per passed scene.
- How spend builds up:
  - `gateway/catalog.py:video_cost_usd` bills each try;
  - `agents/tasks.py:_cost` tells the producer what a step spent;
  - `agents/scene_agent/film.py:Film.run` caps a run at 6 clips, and every owner note opens a new run.
- Expected clips per scene, with pass rate p and a cap of 6, is (1 − (1 − p)^6) ÷ p:
  - at p = 0.5 that is 1.97 clips; at p = 0.3, 2.94.
  - For 4 scenes of 10 s, that is about $6.2 or $9.3 an ad on H3 alone.
  - A first-tries-only quote ($3.2) is 2–3 times too low.
- The price itself is in doubt, by about 3 times. The catalog uses 0.4 credits/s, as the Oct 3 bills did. Creatify's H3 page has a table of 0.13 credits/s at 768p, but an example on the same page bills at the API page's rates. See "Also seen here" #20.

**How to apply it in this build** (Julian's call: money)
- Quote the smoke run and round 1 as three numbers: the first-try floor, the expected cost at p = 0.3–0.5, and the cap ceiling.
- After round 1, work out H3 spend per passed scene, for each kind, from `ModelCall` rows. It is one query and no model call.
- Use the same number to compare any second video model later (concept 8).

**One real post:** Creatify, `model-eval-framework.md` in their ai-ad-prompt-guide: "Cost per
usable output = (Total credits spent) / (Number of usable outputs)"; "Generate each prompt 3 times
per model (AI output varies)."
https://github.com/Creatify-AI/ai-ad-prompt-guide/blob/main/references/model-eval-framework.md

---

### 6. Negation in prompts (the pink-elephant problem) · good to know

**What it is**
- Text-to-picture and text-to-video models often read "no X" as "X".
- Say what is there instead of what isn't.

**Why it matters here**
- The agent's boxes are already refused for negative words (`agents/scene_agent/lifecycle.py:BANNED`, checked in `agents/scene_agent/film.py:Film.run`). That is done right.
- Code's own lines skip the rule. `agents/scene_forms.py:video_prompt` ends every H3 prompt with "No music."
- H3 takes no `negative_prompt` (Creatify's API page), so the prompt is the only place to say it.
- Nothing in the app listens for music. `ai-voice-words.md` concept 8 covers detecting it.
- Known: `docs/limitations.md` lists "never write 'no sound', 'no speech', 'no text'" as a never-graded rule.

**How to apply it in this build** (Julian's call: what H3 is told)
- Write the sound line positively, for example "Sound: only room tone, the action's sounds and her voice." Drop "No music."
- Test it in the smoke run on one scene: today's line vs the new one, 2 samples each.

**One real post:** Alhamoud et al., "Vision-Language Models Do Not Understand Negation" (CVPR 2025):
"modern VLMs struggle significantly with negation, often performing at chance level."
https://arxiv.org/abs/2501.09425

---

### 7. Prompt upsampling (the provider's prompt rewriter) · good to know

**What it is**
- Video models are trained on long, detailed captions.
- Providers ship a rewriter that turns your prompt into that style before rendering.
- With it off, the model gets prompts in a style it may not have been tuned on.

**Why it matters here**
- `jobs/work.py:sent_handoff` always sends `prompt_enhancement=False`. #327 says "Prompt enhancement stays off."
- Creatify's API page: `auto` "rewrites the prompt the way boreal-h3 was evaluated; none renders it as sent."
- There is a good reason to keep it off: a rewriter could change the words inside the speech tags, or add music.
- `agents/scene_forms.py:VideoForm` is a hand-built rewriter (shot, camera, beats, small life). Whether it matches what H3 was tuned on has never been tested.

**How to apply it in this build** (Julian's call: money)
- A small A/B: 3 scenes × 2 arms (`auto` vs `none`) × 2 samples, about 12 × $0.79 ≈ $9.5. The word check and the judge read each clip, then Julian grades.
- Keep the arm on the sent list, so the grades can be split by it later.

**One real post:** CogVideoX paper, Appendix F "Caption Upsampler": "To ensure that text input
distribution during inference is as close as possible to the distribution during training … we use
a large language model to upsample the user's input during inference."
https://arxiv.org/html/2408.06072v3

---

### 8. Anti-corruption layer (a provider-neutral clip request) · good to know

**What it is**
- Describe what a clip needs in your own terms: the line, the voice words, on or off camera, the action.
- One adapter per vendor translates that into the vendor's own syntax.

**Why it matters here**
- H3's syntax lives above the gateway:
  - `agents/scene_forms.py:video_prompt` writes `<d>[English] …</d>`, "Image 1" and the speed line;
  - `gateway/types.py:BrollClipHandoff` takes a finished prompt string.
- ADR 0012 deleted HeyGen and Inworld, so one vendor now makes every picture, voice and lip.
- Its fallback needs a second model (concept 2), and Creatify's own docs disagree on the price.
- Swapping models today means editing the agent's prompt builder, its pinned tests and the fake, not one adapter.

**How to apply it in this build** (code structure is mine to propose; adding a vendor is Julian's call)
- Move the speech-tag and sound-line wording into `gateway/creatify_adapter.py`, behind handoff fields `line`, `voice_words`, `on_camera` and `after_last_word`.
- `catalog.MODEL_FOR_PURPOSE` stays the one switch.
- Do it only when a second video model is wanted.

**One real post:** Microsoft Azure Architecture Center, "Anti-Corruption Layer pattern": "Implement
a facade or adapter layer between different subsystems that don't share the same semantics … ensure
that dependencies on outside subsystems don't limit an application's design."
https://learn.microsoft.com/en-us/azure/architecture/patterns/anti-corruption-layer

---

### 9. Audio-visual sync scoring (lip-sync metrics) · good to know

**What it is**
- A small model scores how well the mouth matches the sound, frame by frame.
- The standard scores are LSE-D and LSE-C, from a SyncNet "lip-sync expert".

**Why it matters here**
- ADR 0012 has one H3 render make the lips and the voice in every talking clip.
- `jobs/clip_judge.py:check_words` tells the judge "her mouth moving is expected". The judge sees frames only (`cut_pictures`) and never hears the clip, so nothing checks the lips.
- #327 Out of Scope: "Checking lip sync or voice drift in code." The old human rubric had "lips_in_step" (`video-eval/blind.py:RULES`).
- In off-camera B-roll, a mouth that shows while the voice plays is also a sync fail.

**How to apply it in this build** (Julian's call: a new check)
- Not now.
- If round 1 shows lip fails, score Julian's graded talking clips offline with open-source SyncNet (free, runs on a CPU). Make it a check only if the score separates his passes from his fails.

**One real post:** Prajwal et al., "A Lip Sync Expert Is All You Need for Speech to Lip Generation
In The Wild" (Wav2Lip): proposes "new, rigorous evaluation benchmarks and metrics to accurately
measure lip synchronization in unconstrained videos."
https://arxiv.org/abs/2008.10010

---

## Also seen here

- #1 Idempotency keys: `gateway/creatify_adapter.py:CreatifyProvider._send` (a lost reply raises `ClipFailed`, never resent) and `jobs/work.py:_clip_asked_for` now guard every scene, talking ones included.
- #3 Timeouts and deadlines: `gateway/gateway.py:collect_clip` still polls with no deadline. Clips now run 9–15 s, so renders take longer.
- #11 Regression evals: talking scenes moved from HeyGen to H3 (#339) with no eval. `jobs/work.py:_clip_purposes` returns one purpose for both kinds.
- #17 Over-optimizing against an imperfect judge (known): `agents/scene_agent/lifecycle.py:_passes` films until the judge passes. It now covers talking clips.
- #19 Stopping conditions (no dollar cap): `agents/scene_agent/film.py:Film.run` caps a run at 6 clips. A run can now cost $4.3–12.4 at catalog price, and each owner note opens a new run.
- #20 Cost reconciliation:
  - `gateway/catalog.py:PRICE_PER_SECOND_OF_VIDEO` uses 0.4 credits/s. The Oct 3 replies agree: `credits_used: 2` for 5 s (`tests/creatify/*.json`).
  - Creatify's own pages disagree today. The H3 page's table says 0.13 credits/s at 768p ("a 5-second 768p video costs 1 credit"). Its example on the same page bills "2k, 10 seconds, 26 credits", which is the API page's 2.6/s. Every total is rounded up to half a credit.
  - Creatify says "Failed videos are refunded automatically", but `gateway/gateway.py:submit_broll_clip` records the cost at submit.
  - The reply's `credits_used` is never read (`gateway/creatify_adapter.py:CreatifyProvider.status`).
- #33 Contract tests: `gateway/fake.py:FakeModel.submit_broll` returns a tone exactly `seconds` long. `tests/creatify/*.json` are Oct 3 silent replies.
- #34 Calibrating an LLM judge: `jobs/clip_judge.py:check_words` adds `TALKING_SCENE_CONTEXT`. No graded talking clip calibrates it.
- #36 Text normalization before length estimates: `jobs/work.py:sent_handoff` calls `h3_clip_seconds(len(step.line.split()))`, and so does `agents/scene_agent/lifecycle.py:_validate_line`.
- #43 Independent evidence for checks (done right): `agents/scene_agent/film.py:Film.run` keeps `picture_photos` for the judge and never sends them to H3.
- #46 App-side rate limiter: Creatify publishes no limit (`docs/rate-limits.md`). Every scene agent films at once, and only `creatify_adapter._checked` retries a 429.
- #48 Webhooks instead of polling: Creatify's `webhook_url` is unused, and `gateway/gateway.py:collect_clip` polls every 10 s.
- #57 Native video input vs frame sampling:
  - `jobs/clip_judge.py:cut_pictures` takes 10 frames a second into at most 50 pictures, less the references. A 15 s clip packs 3 or 4 frames per picture.
  - The judge and the agent (`agents/scene_agent/media.py:_frames`) never hear the clip, though `gateway/gateway.py:watch_video` can already send a whole video with sound to Gemini.
- #63 Content-addressed keys: `jobs/work.py:_clip_asked_for` finds a clip already paid for by its exact handoff (`handoff=handoff.model_dump()`).
- #66 Expand/contract migrations (done right): `jobs/work.py:_clip_asked_for` fails a retired purpose as `ClipFailed`, never a crash.
- #9 Golden dataset: `video-eval/set.json` and `video-eval/arms.json` are the old Boreal set. They hold no H3 or talking cases.

---

## Bugs and loose ends

- **A failed or refused H3 render posts no notice.**
  - `agents/scene_agent/film.py:Film.run` → `jobs/work.py:_make_clip` raises `ClipFailed`.
  - `agents/loop.py:_run` hands it back as a tool answer and skips the log, because `ClipFailed` is in `EXPECTED_FAILURES`.
  - No Kind in `jobs/notices.py` covers it, so it never reaches `job.warnings` or Sentry. AGENTS.md: "No silent failures."
  - The try still counts against the 6: `SceneAgentRun.clips` goes up before the render.
- **Refunded renders are counted as spent.** `gateway/gateway.py:submit_broll_clip` bills at submit, and nothing takes the cost back when `collect_clip` gets `failed` (Creatify refunds it).
- **ADR 0012's fallback can't run on H3.** "Our voice first, then lip sync" needs `audio_url`, which Creatify's API page marks "boreal only". The doc should say a second model is needed.
- **Reference mode promises a frame 1 it can't fix.** "The clip starts exactly like Image 1." (`agents/scene_forms.py:video_prompt`) goes with no `image_url`, which a reference render can't take (concept 2).
- **Videos stay at Creatify for ever.** `gateway/creatify_adapter.py:CreatifyProvider.submit_broll` sends no `ttl_days`, so Creatify keeps every clip with no expiry. The app downloads at once (`gateway/gateway.py:collect_clip`), so `ttl_days: 1` would cost nothing. (Julian's call: shop data at a vendor.)
- **Provenance label, unchecked.** The H3 output link sits under `boreal_api/c2pa/` (`tests/creatify/done-from-example-pictures.json`). Whether the file carries a C2PA manifest, and whether `jobs/assembly.py:join` keeps it, hasn't been checked. See `ai-pictures.md` concept 8.
- **The prompt's length is advice only.** `agents/scene_forms.py:VideoForm` says "About 106 to 150 words in all", and nothing counts the words.
- **Stale docstrings from before H3 spoke:**
  - `gateway/gateway.py:submit_broll_clip` and `gateway/types.py:BrollClipHandoff` say "with no sound";
  - `jobs/work.py:_clip_purposes` says talking and B-roll clips "are each made by their own video model";
  - `jobs/product_videos.py:_cut` says "the clip made from it has no sound of its own";
  - the `gateway/creatify_adapter.py` module docstring says it "Makes B-roll clips".
- **Dead line:** `agents/scene_agent/__init__.py:run` sets `person_shown = step.person_shown`, and the next line overwrites it.
- Already listed in `ai-voice-words.md`, not repeated here: a silent clip passes; the speech tags aren't escaped; the voice sentence is broken ("says with A woman's voice."); voice words that ask for a slow pace.

---

## Checked by an independent reader

Checked against the code at `fd346df`; Creatify's Boreal-H3 page and its POST /api/boreal/ reference were re-read on Nov 4 (free).

- Isochrony · CORRECTED · "2.5 is the top" fixed: Creatify's 17–27 words in 10 s is 1.7–2.7 words/s, which hints H3 may fit its pace to the slot; added that a longer clip than asked is a fourth possible outcome (`creatify-problems.md` #1); noted `FakeModel.clips_short_by` exists but no test sets it. Formula, table and `_conversation`'s "The line takes N s" all hold.
- Conditioning modes · CONFIRMED · nothing changed. Reference mode sends no `image_url` (`CreatifyProvider.submit_broll`); `tests/creatify/refused-both-ways.json` is Creatify's own refusal of both together; `pieces_for` has no kind check; the API reference marks `audio_url` and `negative_prompt` "boreal only".
- Canary release · CONFIRMED · nothing changed. No recorded H3 reply with speech and no written pass bar anywhere in the repo; the H3 page does say "To add sound, send `reference_audio_urls`".
- Sampling variance and pass@k · CORRECTED, retagged good to know · the film tool's description already allows "film again as needed"; each try's prompt and picture are stored, so re-rolls can be found later. It shapes how round 1 is read, not today's build.
- Yield-adjusted cost · CORRECTED · `evals/results.py` already counts remakes and their cost per scene after a round; what is missing is a pre-run quote from a pass rate. Formula and dollar figures check out.

Cite fixes:
- `film.py:Film.run` (concept 6) → `agents/scene_agent/film.py:Film.run`.
- Every other `file:symbol` cite in this report, the "Systems in this group" table included, names a file and a symbol that exist.

Flagged, not changed: the concepts table now has a good-to-know concept (4) between must-apply-now ones; the ranking order was left as written.
