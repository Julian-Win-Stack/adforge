# Design mentor: planning and talking scenes

Area: the planner, plan edits, the person (portrait and voice), talking scenes (starting
picture, line audio, transcript, HeyGen clip), owner parts, face swaps and the picture
redraw-with-check.

Read: `backend/jobs/planning.py`, `scenes.py`, `plan_edits.py`, `voices.py`, `face_swap.py`,
`picture_check.py`, `owner_parts.py`, `made_pictures.py`, `checked_pictures.py`, and what they
call in `jobs/work.py`, `jobs/models.py`, `agents/producer.py`, `gateway/gateway.py`.
Design read: plans for HeyGen, #19 and #66; `docs/what-makes-a-good-ad.md`; `docs/lessons.md`;
`docs/limitations.md`; CONTEXT.md entries; specs #1 and #210. #327 (approved, not built) is
named in one line per concept.

Ranked by impact on this build. Each concept is tagged **must apply now** or **good to know**.
Applying any of them is a design change: it needs Julian's yes first.

---

## 1. Prompt decomposition (prompt chaining) — **must apply now**

**What it is**
- You split one big model call into a chain of small calls. Each call gets only the rules
  for its own job, and code checks the result between steps (Anthropic calls these checks
  "gates").
- Textbook example: three calls ("write", "translate", "format") instead of one prompt that
  asks for all three.

**Why it matters here**
- `planning.PLAN_INSTRUCTIONS`, sent by `work.plan`, is one call of 4,500 words. It does at
  least six jobs at once:
  - decide whether to ask or plan (five ask cases);
  - write the script (hook, format, call to action, price, name);
  - pick each scene's kind;
  - fill every B-roll field (kind, usage, result, needs, scene pictures, piece, second
    state, thin film, owner part);
  - describe the person;
  - judge the product's size and colour.
- Models follow fewer rules as a prompt gets denser, and they favour the rules that come
  first (IFScale, below). Here the B-roll rules come after the script rules.
- The repo already shows the symptom:
  - lesson 3: "the planner needs a rule for each";
  - limitations: "a prompt line saying 'keep only that part out of view' was tried once and
    didn't change it" (losing all B-roll without a photo);
  - limitations: "no rule for writing how it is held was added" (thin hand-held products).
  - Each fix adds one more sentence to the same call.
- #331 adds a second instruction set for marked ads (the arm's-length test, 3–4 scenes,
  8–10 s lines) to this same call.

**How to apply it**
- Measure first. Use the rule evals (`evals/rule_checks.py`) and the round grades to list
  which planner rules break most. Split only where the breaks cluster.
- A chain that follows the jobs:
  1. **Gaps:** ask or plan. One yes/no per ask case, each with its evidence.
  2. **Script:** lines, parts, format, name, price, overlays.
  3. **Each scene:** its kind and that kind's fields. One call per scene, with only that
     kind's rules and only the pictures that scene needs.
  4. **Person:** looks, voice, size.
- Today's validators become the gates between steps. A failed gate re-asks one small
  step, not the whole plan.
- Send the library sheets (up to 200 pictures) only to the steps that name pictures.
- Move rules word for word:
  - `tests/test_frozen_rules.py` pins them;
  - the proven B-roll rules (`docs/agents/proven-broll-rules.md`) change only with Julian's
    explicit OK;
  - moving a rule to another call changes what the planner decides, so it also needs his
    yes.
- Under #327: this carries over, and the arm's-length kind test fits step 3.

**One real post:** Anthropic, "Building effective agents" (prompt chaining):
https://www.anthropic.com/engineering/building-effective-agents
Evidence on rule density: IFScale, "How Many Instructions Can LLMs Follow at Once?":
https://arxiv.org/abs/2507.11538

---

## 2. Aggregate invariants (one rule check on every write path) — **must apply now**

**What it is**
- An aggregate is a group of rows that must stay valid together; here, the plan and its
  scenes.
- Its rules (the "invariants") live in one place, and every change goes through that place.

**Why it matters here**
- The rules for the whole plan live on `planning.Plan` validators:
  - `_opens_on_the_person`: scene 1 talks to camera (frozen rule A16);
  - `_name_said`;
  - `_second_state_after_the_first`;
  - `_a_different_action_in_each_broll_scene`.
- They run only on the planner's answer (`work.plan`).
- Plan edits check one scene at a time:
  - `plan_edits._check` validates a single `PlannedScene`;
  - `_check_the_actions` checks the actions differ again;
  - `_second_states_kept` repairs second states.
  - Nothing checks scene 1 again.
- So two edits can open the ad on a B-roll scene:
  - `plan_edits.reorder` can move a B-roll scene to first place;
  - `plan_edits.edit_scene` on scene 1 passes when it gives `shows`, `broll_kind` and
    `person_shown`.
  - `test_a_scene_that_would_break_the_plan_is_refused_with_why_and_nothing_changes` is
    refused only because the labels were missing.
- #331 adds "3 or 4 scenes when there's no target" as a `Plan` validator.
  `plan_edits.add_scene` and `remove_scene` would skip it.
- Known and accepted: the name rule applies to the first plan only (`docs/plans/66-b-roll-scenes.md`).
  The first-scene rule is not listed as a limitation.

**How to apply it**
- Write one function, such as `plan_rules(job) -> list[str]`. It reads the stored scenes and
  runs the same rules the `Plan` validators run. Share that code; don't copy it.
- Call it at the end of every write path, inside the same transaction, and raise `CantEdit`
  with the rule's own words:
  - `edit_scene`, `add_scene`, `remove_scene`, `reorder`;
  - the fact check's rewrite;
  - `work._say_it_to_camera`;
  - `owner_parts._give`;
  - the scene agent's `scene_agent/change_scene.ChangeScene`.
- Which rules refuse an edit and which repair themselves (as `_second_states_kept` does) is
  a rule decision, so it's Julian's call.
- Test each write path through the chat, e.g. "a reorder that opens on a B-roll scene is
  refused and nothing moves".
- Under #327: this grows. A16 stays (build question 2) and the scene count joins it.

**One real post:** Martin Fowler, "DDD Aggregate": https://martinfowler.com/bliki/DDD_Aggregate.html

---

## 3. Tagged union (make illegal states unrepresentable) — **must apply now**

**What it is**
- When something comes in kinds, store the kind on purpose, and give each kind only its
  own fields.
- Then a talking scene can't hold B-roll fields: there is nowhere to put them.

**Why it matters here**
- Today a scene's kind is read from a blank string: `shows == ""` means talking.
  `models.Scene.change_line` clears the B-roll fields when `shows` goes blank.
- About 84 places in `jobs/` and `agents/` read `shows` to tell the kind.
- In the planner's schema, every B-roll field is optional on one flat `ScriptScene`.
  Wrong mixes are refused only after the model has written them, and been paid:
  - `ScriptScene.check_broll_details`;
  - `PlannedScene._owner_part_is_filmed`;
  - `PlannedScene._only_broll_is_sent_pictures`.
- Nothing in the database stops a talking row that has `broll_kind` set.
- #329 adds an explicit `kind` and `why`, and holds "the only migration of the build". That
  migration is the moment to add the guard.

**How to apply it**
- In the planner's schema:
  - `TalkingScene(kind: Literal["talking"], line, part, overlay, ...)`;
  - `BrollScene(kind: Literal["broll"], why, shows, broll_kind, ...)`;
  - `scenes: list[TalkingScene | BrollScene]`.
- Use a plain union, not `Field(discriminator=...)`. I checked both with the repo's openai
  3.15 `to_strict_json_schema`:
  - the plain union becomes `anyOf`, which strict mode allows when nested;
  - the discriminator becomes `oneOf` plus `discriminator`, which is outside OpenAI's and
    Azure's supported schema subset.
- In the database: put one `CheckConstraint` per kind in #329's migration
  (https://docs.djangoproject.com/en/stable/ref/models/constraints/):
  - a talking row has blank B-roll fields;
  - a B-roll row has `shows` and a `why`.
- In code: read `scene.kind`, never `bool(scene.shows)`. Change the 84 reads as each ticket
  touches them.
- Under #327: this is #329's job. The point is to land the constraint in its one migration.

**One real post:** Jane Street, "Effective ML Revisited", section "Make illegal states
unrepresentable": https://blog.janestreet.com/effective-ml-revisited/

---

## 4. Text normalization before length estimates — **must apply now**

**What it is**
- A speech engine first turns written text into the words it will say: "$24.00" becomes
  "twenty-four dollars".
- Count a line's length after that step, not on the written text.

**Why it matters here**
- `work._measure_voice` sets `words_per_second = count_words(script) / seconds`.
  `checks.count_words` is `len(text.split())`.
- So "$24.00", "94%" and "3-in-1" each count as one word, though each takes three or four
  to say.
- Every ad's call to action says the price (`PLAN_INSTRUCTIONS`). The last line is the one
  most under-counted.
- The measured speed is an average over the whole script, so its error lands on the lines
  with the most numbers.
- The repo already knows Scribe hears "sixty dollars" as one token, "$60"
  (`tests/test_assembly.py::test_a_line_ending_in_a_number_is_kept_long_enough_for_the_number_to_be_said`).
- Under #327 this gets worse:
  - the measured speed goes;
  - #329 sets a clip's length from the line's word count at 2.5 words a second, plus 1 s;
  - speech that runs past the clip's end "shows as missing words and fails" the word check;
  - each fail is a paid re-film, at about $0.08 a second.

**How to apply it**
- Write one pure function, `spoken_words(line) -> int`. It first turns numbers, prices,
  percentages, units and symbols into words, then counts.
  - Use a library such as num2words, or a small set of rules tested on real lines.
  - Use it in #329's clip-length helper, and everywhere `count_words` feeds a length.
- Test it with: prices with cents, "%", ranges ("2–3"), "24/7", "3-in-1", and "®"/"™"
  (which are said as nothing).
- When the first round's transcripts give the real speed (#327 build question 1), count
  those transcripts the same way. Otherwise the error gets baked into the new constant.
- The code is yours to write. The speed constant is Julian's (approved at 2.5).

**One real post:** NVIDIA, "Text Normalization and Inverse Text Normalization with NVIDIA
NeMo" ("$123" → "one hundred and twenty-three dollars", done before TTS):
https://developer.nvidia.com/blog/text-normalization-and-inverse-text-normalization-with-nvidia-nemo

---

## 5. Constrained decoding (put the rule in the schema) — **must apply now**

**What it is**
- With structured outputs, the provider lets the model write only what the JSON schema
  allows.
- A value from an `enum` can't be wrong. A rule that lives only in a validator can be
  broken, and you find out after you've paid.

**Why it matters here**
- Two places refuse a wrong number only after the answer comes back:
  - `planning.producer_decision_for` (`_real_photos`) refuses a plan that names a library
    picture or a moment the job doesn't have;
  - `scenes.starting_picture_choice_for` does the same for the photo number.
  - Each refusal runs the whole call again.
- The repo already does it right once: `colours.answer` makes the colour a `Literal` of the
  job's colours.
- The planning file states the cost: "a refused plan is paid for again" (the comment above
  `ScriptFormat`).
  `test_a_plan_that_breaks_the_rules_is_handed_back_keeps_nothing_and_is_still_paid_for`
  shows two paid `plan_ad` calls for one broken rule.
- The re-ask (`ANSWER_AGAIN` in `gateway.call_model`) tells the model the error, but not
  its own failed answer. It plans again from scratch and can break a different rule.

**How to apply it**
- Turn numbers into enums, as `colours.answer` already does:
  - library numbers as `Literal[tuple(numbers)]` in `ScenePicture.number`, `Need.photos`
    and `colour_photos`;
  - moments as one enum of "video:moment" ids;
  - the photo in `starting_picture_choice_for` as `Literal[tuple(photos)]`.
- Limits:
  - OpenAI allows up to 1,000 enum values in one schema;
  - Azure, used when its keys are set, supports `enum` and nested `anyOf`, but not
    `minItems`, `maxItems` or `pattern`;
  - 200 library numbers in three fields fit.
- Some rules can become shape: `first_scene: TalkingScene` plus
  `other_scenes: list[TalkingScene | BrollScene]` means the planner can't break A16.
- Keep validators for what a schema can't say (the name is said; actions differ).
- The re-ask lives in the gateway area, so this part isn't yours alone. Send back the
  failed answer, and put the error after the input, as Instructor does:
  https://python.useinstructor.com/concepts/reask_validation/
- Measure first: count the `plan_ad` model calls that failed on a rule, from their records.

**One real post:** OpenAI, Structured Outputs guide ("always generate responses that adhere
to your supplied JSON Schema", no "invalid enum value"):
https://developers.openai.com/api/docs/guides/structured-outputs

---

## 6. ASR round-trip check (WER with a shared normalizer) — **must apply now**

**What it is**
- After speech is made, transcribe it and compare the transcript with the text it was meant
  to say.
- Word error rate (WER) is the share of words that came out wrong, after both texts are
  normalized the same way.

**Why it matters here**
- #1 (tool 10) says transcribing before the clip lets "a voice mistake be caught before the
  clip is paid for".
- In code, the only comparison is `producer._heard_otherwise`: lowercase letters and digits,
  exact match.
  - On a mismatch, it allows one fresh take, but only if the producer asks for the audio
    again.
  - It doesn't stop `producer.MakeClip`, which only checks that a transcript exists.
    MakeClip's refusal text still says "a clip is only made from audio that was heard
    saying the line".
- Exact letter matching raises a false alarm on "60 dollars" against Scribe's "$60". It
  also has no middle ground between "same" and "different".
- #330 (wave 1 of #327) builds the word check: "code tidies both texts", with "'$10' and
  'ten dollars'" as one of its test cases. That tidy step is a text normalizer, and tested
  ones already exist.

**How to apply it (in #330)**
- Normalize both texts with a tested normalizer, such as OpenAI Whisper's
  `EnglishTextNormalizer`, which turns "twenty nine dollars" into "$29":
  https://github.com/openai/whisper/blob/main/whisper/normalizers/english.py
- Align the two texts word by word (jiwer or difflib), and record the WER for each clip next
  to its verdict. Julian then sees the spread before any threshold is set.
- Give the small model the aligned differences (each wrong span with its neighbours), not
  two whole texts. Speech-to-text mishears too, which is why a model settles what's left.
- Today's HeyGen path needs no change: the gap closes with #330 and #338.

**One real post:** Deepgram, "TTS playground vs API pronunciation gap" (Whisper WER as a
gate on synthesized speech; numbers and currency as a main failure; a vendor post):
https://deepgram.com/blog/tts-playground-vs-api-pronunciation-gap

---

## 7. Normalization (one row per real thing) — **good to know**

**What it is**
- Store a fact once, give it an id, and point to it.
- If it's copied into many rows, code has to find every copy and change each one the same
  way.

**Why it matters here**
- CONTEXT.md says an owner part is "one thing for the whole ad". But each scene keeps its
  own copy: `owner_part` (JSON), `owner_part_picture`, `owner_part_drawn` and
  `owner_part_approved_at`/`_by`.
- Code keeps the copies in step by copying changes out: `owner_parts._sharing`, `_give`,
  `_one_sentence`, `_holding` and `approve`.
- Copies are matched by name:
  - `reference_photos.same_thing` ignores only case and "a", "an", "the";
  - `owner_parts._shows` searches `shows` and `usage` for the name.
- The planner writes `owner_part.what` again in each scene. So "brush head" and "head of
  the brush" become two parts: two drawings, two yeses, maybe two looks.

**How to apply it**
- Add an `OwnerPart` table: job, what, looks, source, picture, approved_at, approved_by.
  `Scene.owner_part` becomes a foreign key to it.
- Have the planner declare parts once on the plan, with numbers
  (`owner_parts: [{number, what, looks}]`), and have scenes name them by number, the way
  they name library pictures. With an enum (concept 5), a scene can't name a part that
  doesn't exist.
- A yes or a correction then updates one row, and "is this scene approved?" becomes a join.
  Most of the copying code goes away.
- Migration: backfill one part per job and name, grouped with `same_thing`.
- Under #327: carries over (the scene agent still waits for the yes).

**One real post:** Microsoft, "Database normalization description":
https://learn.microsoft.com/en-us/office/troubleshoot/access/database-normalization-description

---

## 8. Identity consistency check (face-embedding similarity) — **good to know**

**What it is**
- To check that the same person appears in every picture, compare face embeddings: number
  vectors from a face model such as ArcFace.
- A cosine similarity near 1 means the same face. It's a number, not a model's answer, so
  it can't refuse, and it's cheap.

**Why it matters here**
- The person is one portrait, used as the reference for:
  - every talking starting picture (`work.make_starting_picture`);
  - the person-in-room picture (`scenes.PERSON_IN_ROOM_PROMPT`, `work.person_and_room`);
  - face swaps (`face_swap.for_clip`).
- Nothing checks that the face that comes back is the presenter's. `face_swap._check` only
  checks that the stranger's face is gone ("an identity question made the model refuse").
- Lesson 8: the clip judge ignores clothes and bodies.
- Under #327 she talks on camera in most scenes, each drawn from the person-and-room
  picture, so drift will show more. Voice drift is accepted there, and checking it is out
  of scope.

**How to apply it**
- Embed the portrait's face once. Embed the face in each new starting picture or swap, and
  record the similarity as a developer notice, with no gate.
- Set a threshold only after checking it against Julian's grades (taught concept 3), and
  only then let it decide anything.
- Embed AI-made faces only (the portrait and the pictures drawn from it), never page photos
  of real strangers: a real person's face embedding is biometric data.
- Making it a gate is a judging rule, so it needs Julian's yes.

**One real post:** AAAI-26 story-consistency benchmark, metric CIDS (ArcFace features,
similarity to the reference and across shots of one story):
https://aaai26-19745.github.io/metrics

---

## 9. Evaluator-optimizer (the check's finding shapes the next try) — **good to know**

**What it is**
- Make something, check it, and when it fails, write the next try from what the check
  found.
- The simpler version is rejection sampling: send the same request again and keep the first
  try that passes.

**Why it matters here**
- `checked_pictures.made_and_checked` and `cut_out.redraw_with_check` redraw with the same
  prompt and the same pictures. The check's `problem` only goes into the notice.
- Face swaps (`face_swap.for_clip`) and owner-part pictures (`owner_parts._redrawn`) use
  this loop.
- Drawing again fixes random faults. A fault the prompt causes comes back on every try:
  three paid draws (`MOST_REDRAWS` = 2).
- The scene agent already uses its check answers to fix B-roll pictures; these library
  steps don't.
- Lesson 2: a fix that only adds "don't" rules fails. The retry should rewrite the prompt to
  say what happens.

**How to apply it**
- Measure first: from the records of `check_part_picture` and `check_face_swap`, how often
  try 2 fails for the same reason as try 1.
- If it often does: add a small call that rewrites the picture prompt from the problem, said
  as what should happen, while code keeps its fixed lines.
- The record lookup that stops paying twice already keys on the prompt, so a worker restart
  still pays for nothing twice.
- The prompt wording is yours. Whether to add the step is Julian's call.

**One real post:** Idea2Img (GPT-4V critiques draft images and rewrites the prompt):
https://arxiv.org/abs/2310.08541

---

## 10. Human-in-the-loop approval as a structured action — **good to know**

**What it is**
- When work must wait for a person's yes, the yes is an explicit action (a button, an
  approve or reject call) that code records.
- It is not free text that an agent reads as a yes.

**Why it matters here**
- The owner-part gate (no clip until the shop owner says yes) is opened by
  `producer.ApproveOwnerPart.run`:
  - code checks only that the owner sent some message after the picture was shown;
  - `owner_parts.approve` then records it.
- Whether that message is a yes ("love it, but bluer?") is left to the producer's reading,
  guided by the tool's docstring.
- A wrong reading lets an invented part reach paid clips in every scene that holds it.
- Decided: spec #247 point 12 says the yes comes in the owner's own words.

**How to apply it**
- Smallest change: a "Yes, use this" button on the part picture's card, which code records.
  A typed yes still works through the tool.
- Or keep words, and have the tool take the owner's quoted words and check them against the
  message, as the scene agent's owner quotes do.
- Either is a flow change, so it's Julian's call.

**One real post:** OpenAI Agents SDK, "Human-in-the-loop" (`needs_approval`, the run pauses,
then `approve` or `reject`): https://openai.github.io/openai-agents-python/human_in_the_loop/

---

## 11. Semantic deduplication — **good to know**

**What it is**
- Two texts that mean the same thing in different words are semantic duplicates.
- Exact matching misses them; embeddings or a small model catch them.

**Why it matters here**
- "Each B-roll scene a different action" is checked by `planning.same_action`: it ignores
  case and "a", "an", "the", then needs an exact match. It's used in
  `Plan._a_different_action_in_each_broll_scene` and `plan_edits._check_the_actions`.
- So "a hand pours the tea" and "tea is poured from the pot" pass as different actions.
- The test for it uses two texts that differ only in case and punctuation
  (`test_a_plan_filming_the_same_action_in_two_scenes_is_refused_and_planned_again`).

**How to apply it**
- Leave it until a graded ad shows the same action twice.
- Then compare each pair of `shows` with an embedding (cosine similarity above a threshold
  you calibrate) or a one-word small-model call, and refuse the plan with the same message.
- Under #327: the one-action rule stays, so this carries over.

**One real post:** Meta AI, "SemDeDup: Data-efficient learning at web-scale through semantic
deduplication": https://arxiv.org/abs/2303.09540

---

## Also seen here

Already taught (scene-agent mentor, PR #324). Each appears again in this area:

- **1. Stopping conditions:** `checked_pictures.made_and_checked` caps redraws and refusals
  (`MOST_REDRAWS`, `MOST_REFUSALS`), and `work._fit_its_clip` caps shortenings
  (`MOST_BROLL_SHORTENINGS`). Applied.
- **2. Agent evals on a real model:** `tests/test_planning.py` (`test_the_planner_is_told_*`)
  pins the prompt's words. Whether the planner follows them is measured only outside the
  suite: `broll-test/should_ask.py`, `broll-test/line_lengths.py`. Pinned words aren't
  followed rules.
- **3. Calibrating an LLM judge:** `evals/calibrate_picture_check.py` runs
  `work.judge_starting_picture` against Julian's graded pictures. Applied.
- **4. Stable prompt prefix:** `gateway.call_model` adds `ANSWER_AGAIN` to the end of
  `instructions`, which come before the handoff and the sheets. A re-asked plan reads its
  whole input again, uncached.
- **5. Independent evidence for checks:** `face_swap._check` is shown only the photo and the
  new picture, never the prompt or the portrait. Applied.
- **6. Precision vs. recall:** `work.judge_starting_picture` fails a picture only when 2 of 3
  asks fail it (`picture_check.ASKS`, `FAILS_TO_FAIL`, also used by `fix_check`). That
  favours precision, on a check that failed 0 of 40 pictures in round 5 (lesson 6).
- **7. Explicit state machine:** `models.Scene.change_line`. `Scene.Status` holds only
  planned or finished. The fact check, the owner part's yes and staleness live in other
  fields, each reset by hand (`plan_edits._apply` clears `fact_checked`; `owner_parts._give`
  clears the yes).
- **9. App-side rate limiter:** `voices.designed_voice` designs one voice at a time per
  machine (an advisory lock), because 8 at once drew 429s. That's a limit of one at a time
  standing in for a rate limit.

---

## How this area is tested

- **No-database tests:** `tests/test_planning.py` reads plans through
  `producer_decision_for` (validators) and pins the prompt's sentences.
- **Through the chat:**
  - `test_producer_plan.py`: the plan is stored, and a broken plan is re-asked and paid for;
  - `test_producer_plan_edits.py`;
  - `test_producer_line_audio.py`;
  - `test_producer_person.py`;
  - `test_voice_account.py`;
  - `test_person_in_room.py`;
  - `test_owner_parts.py`, `test_owner_part_picture.py`, `test_producer_owner_part.py`.
- **Word-for-word pins** of approved rules: `test_frozen_rules.py`.
- **Real model, outside the suite:** `broll-test/should_ask.py` and `line_lengths.py`;
  `evals/calibrate_picture_check.py`.
- **Missing tests:**
  - an edit or reorder that leaves scene 1 talking (concept 2);
  - line lengths with numbers in them (concept 4).

## Found while reading (not concepts)

- `picture_check.py`'s docstring says it checks owner-part pictures and a fix's starting
  picture. In fact `work.judge_starting_picture` is called only by
  `evals/calibrate_picture_check.py`.
  - Owner-part pictures use `owner_parts.check_part_picture`, asked once.
  - `owner_parts.CHECK_RULE` and `check_handoff` serve only that eval path.
- Talking starting pictures and talking clips get no quality check: `work.make_clip` judges
  only when `step.shows` is set. This is known: #327 says nothing judges a talking clip.
