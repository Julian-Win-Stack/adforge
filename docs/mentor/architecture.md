# Design mentor: agent architecture, every agent and AI call as one system

**Read:** README; ADRs 0001, 0003, 0004 (history), 0005, 0010, 0011, 0012; `docs/adr/0012-legacy/decision-map.md`;
CONTEXT.md "The agents" and "Machinery"; specs #247, #272, #303, #327; `docs/mentor/summary.md` and the area
reports. **Code:** main as merged into this branch (`fd346df`), one ad end to end: `chat/api.py:_send` →
`agents/tasks.py` → `agents/loop.py` → `agents/producer.py` → `agents/scene_tools.py` → `agents/scene_agent/` →
the checks → `producer.assemble_the_ad`; then an owner note and a worker restart. Paths are under `backend/`.

Teaching only. Nothing in the app changed. Applying any concept is a design change and needs Julian's yes.

---

## Systems in this group

The checklist, one row per system. Costs are **estimates** (catalog price × calls the code makes × token sizes I
assumed), not a bill. Image inputs are counted at about 1.1k tokens each.

| System (purpose → model) | What it is asked | Model, and why | In → out | What checks its output | Eval | How it fails (notice Kind) | Rough cost per ad |
|---|---|---|---|---|---|---|---|
| **Producer** `produce` → gpt-6.1-sol | `producer.py:INSTRUCTIONS`, 27 tools (`producer.py:PRODUCER`), the ad snapshot last (`snapshot.py:ad_as_it_stands`), a `why` per call (`loop.py:WHY`) | `catalog.py:PRODUCER_MODEL`. No reason recorded | Whole chat, its tool calls, step results, system notes (`loop.py:_conversation`) → words + tool calls | Tool refusals (ADR 0001), `hard_list.py`, 100-call cap (`loop.py:settle`), pause (`loop.py:_held_by_the_pause`), nudges (`tasks.py:_nudge`) | None on a real model since #247. `evals/agent_page.py` is a page to read, not a score | Filter: 2 retries, `PRODUCER_RETRIED` (`tasks.py:_work_until_it_stops`). Crash: `PRODUCER_STOPPED` (owner) + `PRODUCER_GAVE_UP` (dev) (`loop.py:say_it_stopped`). Early stop: `PRODUCER_NUDGED` | ~11k fixed tokens a turn (10k chars of instructions + 35k chars of tool specs, measured locally). 12–20 turns a first ad: **~$0.7–1.8** |
| **Look** `look` → gpt-6.1-sol | `look.py:INSTRUCTIONS`, `look.py:LookHandoff` (scene, what is shown, question) | Producer's own model so it reads as well (ADR 0010, Julian 03:15) | ≤50 pictures (`look.py:fitted`) → sees / wrong / root cause / direction (`look.py:LookAnswer`) | Nothing. It decides nothing | None | Error → tool answer `Failed:` (`loop.py:_run`). No notice | ~$0.05–0.15 a look; 0–3 a first ad |
| **Planner** `plan_ad` → gpt-6.1-sol | `planning.py:PLAN_INSTRUCTIONS`, `work.py:plan_handoff` (page, library sheets, video moments, chat) | No reason recorded | Page, sheets, chat → plan or one question (`planning.py:producer_decision_for`) | Schema rules: 3–4 scenes with no target, first scene talking, photo numbers exist (`planning.py:producer_decision_for`, `Plan._opens_on_the_person`). Then fact check, length fit | `broll-test/should_ask.py` (8 asking cases); README's plan-only counts | Filter leaves pictures out: `LEFT_OUT_BY_THE_FILTER` (`work.py:plan`). Unusable answer → `Failed:` | 1–2 calls: ~$0.10–0.30 |
| **Fact check** `fact_check` → gpt-5.6-terra | `checks.py:FACT_CHECK_INSTRUCTIONS` via `work.py:_ask_fact_check` | No reason recorded | Page, chat (producer) **or only the run's quotes** (scene agent), lines, photos, video transcripts → verdict per line, or "unclear" | 2 rewrites, then the owner is asked (`work.py:_fact_check`). In a scene agent "unclear" refuses (`change_scene.py:_fact_checked`) | None ("not measured", README) | "Unclear" → a question for the owner. Error → `Failed:` | 1–3 calls: ~$0.05–0.45 |
| Line rewrites `rewrite_line`, `shorten_line`, `lengthen_line`, `shorten_script` → sol | `work.py:_rewrite_scene`, `_fit_for`, `_shorten`, all from `work.py:run_checks` | (own mentor: `ai-line-rewrites.md`) | Line + page → new line | Fact check again | See that report | `Failed:` to the producer | small |
| **Scene agent** `scene_agent` → gpt-6.1-sol | `scene_agent/instructions.py:INSTRUCTIONS`; intro of link, scene, library list, behaviour note, whole plan, line seconds, page words, then the shared picture, then each run's instruction and quotes (`scene_agent/__init__.py:_conversation`); 8 tools | Spec #303: "models as the prototype ran". No catalog comment | Its scene's chat, ≤50 pictures (`scene_agent/media.py:_fit`) → tool calls | Code: 6 pictures / 6 clips per run, film only a passed picture, ban words (`lifecycle.py:BANNED`), ≤15 picks, exclusions, same action, line limits, fact check. Then picture check, judge, word check | Prototype round 1 (ADR 0011: 15/15 takes, before 0012). None since | `give_up` → `SCENE_AGENT_GAVE_UP` / `SCENE_AGENT_NEEDS_CHOICE` (owner) (`scene_agent/__init__.py:finish`). Cap → `TRIES_TO_PICK` (`work.py:say_the_agent_gave_up`). 6 failed pictures → `CLOSEST_PICTURE_FILMED`. Crash → step failed, `STEP_NOT_MADE` (`tasks.py:_fail`). Shared picture → `PLACE_NOT_DRAWN` | ~5k fixed tokens a turn. Happy path 4–7 turns: ~$0.3–0.8 a scene. **No turn cap** |
| Starting picture `draw_scene_picture` → gpt-image-2.5-sunburst | `scene_agent/draw_starting_picture.py:make_picture` (form + picks + shared picture) | (own mentor: `ai-pictures.md`) | Picks → picture | Picture check | — | Filter → `Refused:` + `REFUSED_BY_THE_FILTER` | ~$0.25–0.40 a picture (spec #303 says ~$0.25) |
| **Picture check** `check_scene_picture` → sol | `scene_agent/picture_check.py:CHECK_INSTRUCTIONS`; name, colour, page, **every owner message**, photo lines | Spec #303 build choice: the agent's model | Picture + the agent's own picks → 2 answers → passed / failed / unchecked | Nothing. `QUALITY_CHECKS` off → "passed" | None (prototype only) | Unchecked + `CHECK_IT_YOURSELF` (owner, no scene number) + `REFUSED_BY_THE_FILTER` (`picture_check.py:check`) | ~$0.03–0.06 a check |
| H3 `make_broll_clip` / `collect_broll_clip` → creatify/boreal-h3 | `work.py:_make_clip`, `work.py:sent_handoff` from `scene_agent/film.py:Film.run` | (own mentor: `ai-video-model.md`) | Picture (+ piece) + prompt → clip with sound | Judge, word check | — | `ClipFailed` → `Failed:` to the agent | $0.0792/s (`catalog.py:PRICE_PER_SECOND_OF_VIDEO`) × 9–11 s: ~$0.71–0.87 a clip. Bill not checked (ADR 0012) |
| Transcript `transcribe_line` → scribe_v2 | `work.py:transcribe_clip` from `Film.run` | (own mentor: `ai-voice-words.md`) | Clip sound → words with times | — | — | `STEP_NOT_MADE` (dev); word check then unchecked | <$0.001 a clip |
| **Word check** `check_words` → gpt-6-luna | `word_check.py:QUESTION` on `Words(line, said)`, after code's `word_check.py:tidy` | Spec #327: "a small Azure text model" | Line + transcript → pass / fail / unchecked + reason (`word_check.py:Result`) | Nothing. One `WORD_CHECK` record a check (dev, not a failure) | None yet (records kept for Julian) | Unchecked → `CHECK_IT_YOURSELF` (`film.py:_check_words`) | ~$0.0002 a model check; exact matches free |
| **Clip judge** `check_clip_references`, `check_clip_product`, `check_clip_motion` → sol | `clip_judge.py:REFERENCES_QUESTION`, `PRODUCT_CHECK`, `MOTION_FROM_REFERENCES` (+ `TALKING_SCENE_CONTEXT`, + the run's instruction and quotes on a note) | Catalog: "Tested on sol; … luna matches it, at the edge" | Starting picture, picks, ≤50 frames → **only PRODUCT can fail** (#353, `clip_judge.py:judged`); MOTION only describes | Nothing. First question re-asked once if unusable | Round 6, 45 clips (README), before 0012 and #353 | Error → `not_checked` → `CHECK_IT_YOURSELF` (`clip_judge.py:_ask_to_watch`). `JUDGE_FIRST_QUESTION` (dev). Filtered seconds → `REFUSED_BY_THE_FILTER` | 3 asks × 40–60k tokens: ~$0.3–0.5 a clip |

### All 49 catalog purposes, by caller

`gateway/catalog.py:MODEL_FOR_PURPOSE`. "Live" means app code calls it today.

| Who calls it | Purposes → function | Live |
|---|---|---|
| Producer's turns | `produce` (`loop.py:run`) | yes |
| Producer's tools | `look` (`look.py:look_at`); `plan_ad` (`work.py:plan`); `fact_check` (`work.py:_ask_fact_check`); `rewrite_line` (`work.py:_rewrite_scene`); `shorten_line`, `lengthen_line` (`work.py:_fit_for`); `shorten_script` (`work.py:_shorten`); `draw_person` (`work.py:create_person`, `remake_person`); `make_music` (`work.py:create_music`); `make_picture`, `check_made_picture` (`made_pictures.py:make_picture`, `cut_out.py:check_made_picture`); `make_part_picture`, `check_part_picture` (`owner_parts.py:_redrawn`, `check_part_picture`); `edit_scene_place`, `draw_scene_room`, `make_scene_person_in_room` (`work.py:edit_scene_place`, `new_scene_place`, `presenter_in_place`, from `SetPlace`) | yes |
| Read page (producer's `read_page`, `add_to_library`) | `check_page` (`work.py:check_page`); `copy_page_text` (`work.py:copy_page_text`); `pick_photos` (`work.py:copy_and_pick`); `note_face` (`work.py:note_face`); `label_photo` (`library_items.py:label_photo`); `watch_video`, `probe_video` (`watcher.py:watch_videos`); `make_reference_photo_picture` (`work.py:_clean_picture`, `owner_answers.py:_clean_picture`) | yes |
| Scene agent, at its start | `plan_before_picture`, `make_before_picture` (`work.py:_before_picture`); `make_room_alone`, `make_person_in_room` (`work.py:person_and_room`) | yes |
| Scene agent's tools | `scene_agent` (`scene_agent/__init__.py:run`); `draw_scene_picture` (`draw_starting_picture.py:make_picture`); `check_scene_picture` (`picture_check.py:check`); `make_product_only`, `pick_cut_out_helpers`, `check_cut_out`, `recolour_picture`, `swap_face`, `check_face_swap` (via `draw_starting_picture.py:swapped`); `make_broll_clip`, `collect_broll_clip` (`work.py:_make_clip`); `transcribe_line` (`work.py:_transcribe_clip`); `check_words` (`word_check.py:check`); `check_clip_references`, `check_clip_product`, `check_clip_motion` (`clip_judge.py:_asked_against_sent`); `fact_check` again (`change_scene.py:_fact_checked`) | yes |
| **Nobody** | `fix_scene` (only `agents/models.py` help text and a migration); `scene_picture_check` (no reference anywhere); `make_starting_picture` (only `gateway/fake.py` and `broll-test/`); `check_starting_picture` (only `work.py:judge_starting_picture`, which only `evals/calibrate_picture_check.py` calls) | **no** |

### Who decides what (ADR 0001: a rule in a tool, or a suggestion in a prompt)

- **Model decides:** the producer's order of tools, its instruction and which quotes; the planner's scenes, lines,
  kind and why; the scene agent's picks, picture form, edit or redraw, line / action / kind changes
  (`change_scene.py:ChangeScene.run`), video form, which picture to film, when to give up; each check's verdict;
  the look's words.
- **Code decides:** the 6/6 caps (`film.py:Film.run`, `draw_starting_picture.py:make_picture`), the quote check
  (`owner_quotes.py:check_quotes`), the speech tags and clip length (`checks.py:h3_clip_seconds`), the pass rule
  (`lifecycle.py:_passes`), the hard list, pause, nudges, wake rules (`tasks.py:_a_result_wakes_it`), staleness,
  assembly, worker restart.
- **Prompt only, though the docs say code:** "Never assemble while a scene agent is working"
  (`producer.py:INSTRUCTIONS`). The code check in `producer.py:assemble_the_ad` looks for a running `CLIP` step,
  which the scene-agent path never creates (see Bugs).

### Rough cost of one first ad (estimate, 4 scenes)

| Part | Happy path (1 picture, 1 clip a scene) | Worst case per scene, per run |
|---|---|---|
| Producer turns | $0.7–1.8 | — |
| Scene agent turns | $1.2–3.2 | unbounded turns (~$0.1 each) |
| Shared person, room, before-picture | $1.0–1.6 | — |
| Starting pictures + checks | $1.1–1.8 | 6 × ~$0.35 ≈ $2.1 |
| H3 clips | $2.8–3.5 | 6 × up to $1.19 (15 s) ≈ $7.1 |
| Clip judge | $1.2–2.0 | 6 × ~$0.45 ≈ $2.7 |
| Plan, fact check, page, music | $0.5–1.2 | — |
| **Total** | **~$9–15** before retries; **~$12–20** with 1.5 tries a scene | **~$12–15 a scene a run**, so ~$50–60 a first ad |

- Julian's $250 cap for 15 overnight ads (spec #247) is ~$16 an ad, which fits the happy-path range.
- Spec #303's "worst case ~$5.50 per run per scene" was for ~5 s clips and leaves out agent turns and the judge.
  ADR 0012 roughly doubled clip length.
- Each owner note opens a fresh 6/6 run (`scene_tools.py:PassNote.run`), and scene-agent calls don't count toward
  the 100-call cap (`loop.py:_calls_since_the_user_spoke`). So no limit holds the cost of one owner message.
- Time is not recorded per scene. `evals/round_export.py:export_ad` keeps only the ad's start and first finish.

---

## How it is tested today

- **Chat-flow tests, outside services faked at the gateway.** The producer is scripted (`tests/conftest.py`
  `ScriptedProducer`). Scene agents are scripted too: `tests/scene_script.py:script_scene_agents` queues one draw
  and one film a scene, the picture check passes, and the fake transcript echoes the prompt's speech tags.
  Main flows: `test_end_to_end.py`, `test_h3_switch.py`, `test_scene_film.py`, `test_scene_owner_notes.py`,
  `test_scene_agent.py`, `test_carries_on.py`, `test_producer_restarts.py`, `test_traces.py`.
- **What they prove:** the tools' refusals, the caps, quote checks, no double payment on restart, what each model
  was handed (`handoffs()`), what was paid (`paid_for()`), and the notices recorded (`recorded()`).
- **What they can't see:**
  - **No two scene agents ever run at once.** `conftest.py:HeldSteps.run_held` runs held steps one at a time,
    oldest first. The re-check under the Job lock in `change_scene.py:_different_action`, notices appended from
    parallel agents, and producer turns interleaved with running agents only happen in a scripted order.
  - **Any real model's judgment at a join.** Whether the producer writes a useful instruction, picks the right
    quotes, or repeats what code already told the owner. Whether the scene agent reads the judge's words right.
  - **The word check's model.** The fake transcript always matches the line, so main flows settle it by code.
  - **States the live path never makes.** `test_versions.py:test_there_is_no_going_back_while_the_scenes_picture_or_clip_is_being_made`
    and `test_pick_from_tries.py:test_a_card_swap_while_the_scene_is_being_made_is_refused_until_its_ready`
    hand-build a running `CLIP` step. Scene agents never create one, so these tests pass while the guard can't fire.
  - **The grading export on scene-agent ads.** `test_round_results.py` builds old-style clip steps (first clip,
    then `remake` 1, 2).
  - **A note to a busy agent in the trace.** `test_traces.py` checks a step lands under the call that started it,
    not under a later note.
- **Evals (run by hand, paid):** `evals/owner_notes` runs the real chat with real text models and faked video on
  round 4 replays. Its results live in the project files, not the repo. `evals/calibrate_*` measure single checks.
  Nothing measures the producer, the scene agent, the look, the fact check or the word check model, or the whole
  system, since ADR 0012.

---

## Concepts, ranked by impact on this build

### 1. Typed handoff contracts between agents (structured briefs and results) · **must apply now**

**What it is**
- What one agent hands another, both ways, is a typed record with named fields, not prose.
- The receiver knows exactly what it got. The sender knows exactly what came back, and code can check both.
- For AdForge: what the producer gives a scene agent, and what a finished run gives back.

**Why it matters here**
- **Going in, a required field forces filler.** `scene_tools.py:StartSceneAgent` requires at least one quote.
  On a one-link first ad, the link is the owner's only words. So the producer quotes the link as "the owner's
  words" for every scene (`test_end_to_end.py:test_one_product_link_starts_all_scene_agents_together_and_finishes_with_a_talking_scene`
  passes `quote=product_page_url`). The agent reads a URL under "Owner's words (checked word for word)"
  (`scene_agent/__init__.py:_conversation`).
- **Coming back, the result is one sentence.** `scene_agent/__init__.py:finished` says passed / gave up / parked
  and a cost. It doesn't say what the agent changed (line, action, kind, through `change_scene.py:ChangeScene.run`),
  how many pictures and clips it used, or what the word check said.
  - Yet the producer is told to "Tell the owner in one line what changed and what it cost" (`producer.py:INSTRUCTIONS`).
- **The cost in it is wrong once a note reaches a busy agent.** `finished` reports `tasks.py:_cost`, which is `SceneStep.cost_usd`: every
  run of the step together.
  - A note passed to a busy agent opens a new run inside the same step (`scene_tools.py:open_note`).
  - The instructions promise "the latest run … with its cost". `SceneAgentRun` has no cost.
- **The owner is told twice, once in the agent's words.**
  - `scene_agent/__init__.py:finish` posts the agent's own reason straight to the owner ("I couldn't make scene N:
    …", `SCENE_AGENT_GAVE_UP`), with the way out.
  - The producer is also told to "explain the reason plainly and offer its way out".
  - The `GiveUp` docstring says the reason is "explaining why to the producer".
  - Code already has the right pattern for owner parts: `tasks.py:_told_it_waits` says "don't tell them again".
- **The judge's contract changed on one side only.**
  - The scene agent's fallback ladder ("if the movement keeps failing, use a simpler action",
    `scene_agent/instructions.py:INSTRUCTIONS`, ADR 0012) assumes the judge can fail motion.
  - Since #350 (merged with #353), only the PRODUCT answer can fail a clip (`clip_judge.py:judged`); MOTION only
    describes. PRODUCT still fails a product that moves or behaves unlike the real one, so the ladder can fire on
    that. A motion fault that leaves the product right (the ordered action not done, unreal movement) no longer
    fails, and `lifecycle.py:_passes` ends the run on that clip.

**How to apply it in this build**
- Give the run's end a typed record built by code (pydantic), then render the step result from it: outcome, run
  number, scene fields changed (before → after), pictures and clips used of 6/6, the clip kept, judge verdict,
  word-check verdict, the run's own cost, and what code already told the owner.
- Run cost: sum the ModelCalls charged to the run's calls since the run's `created_at` (or add a run id; see 5).
- Make `quotes` optional with an explicit "no owner words for this scene yet". (Julian's call: what the agent is
  told are the owner's words.)
- One voice per event: either code posts the give-up and the producer is told "already told", or only the
  producer tells. (Julian's call: what the owner is told.)
- Pin the judge → agent contract with a chat test: a talking clip whose motion answer says "fails" reaches the
  agent the way Julian decides. (Julian's call: what fails a clip.)

**One real post**
- Anthropic, "How we built our multi-agent research system": "Each subagent needs an objective, an output format,
  guidance on the tools and sources to use, and clear task boundaries."
  https://www.anthropic.com/engineering/multi-agent-research-system

### 2. Conflicting implicit decisions (one owner per decision) · **must apply now**

**What it is**
- Every action carries hidden choices. When parallel agents each make their own, the parts don't fit together.
- The fix: each decision has one owner. Other agents propose, and the owner re-checks the whole.

**Why it matters here**
- ADR 0004 said: "A director can't change its line. Lines belong to the whole script (the story, the length fit,
  the price), which a director can't see." Under ADRs 0011 and 0012 the scene agent can change its line, action,
  kind and B-roll labels (`change_scene.py:ChangeScene.run`, spec #303 story 50).
- What `ChangeScene.run` re-checks: line length (`lifecycle.py:_validate_line`), facts (`_fact_checked`), the same
  action as another scene (`_different_action`), first scene talking (`planning.py:check_opening_kind`).
- What it doesn't re-check:
  - the owner's target length (only `work.py:run_checks` → `_fit_length`, from the producer);
  - the story order (`checks.py:KEEPS_ITS_PLACE` goes to the rewriters only);
  - every scene turning talking (no plan check holds this rule either since #327);
  - two lines making the same claim.
- 3–4 agents run at once (`producer.py:INSTRUCTIONS`: "Start all ready scenes together"). Each rebuilds the whole
  plan every turn (`scene_agent/__init__.py:_conversation`), so it may or may not see another agent's change.
- The producer learns of a changed line only from the snapshot (`snapshot.py:ad_as_it_stands`), not from the run's
  result (concept 1).

**How to apply it in this build**
- Write one table: decision → owner (planner, producer, scene agent, code) → who may override it → what is checked
  again after. Start from "Who decides what" above.
- After a `change_scene` that changes words, run the whole-script checks code already has (the target fit) and
  refuse with the reason, as plan edits do. (Julian's call: a new refusal on the scene agent.)
- Or bring back ADR 0004's rule for lines only: the agent proposes, and the producer applies it with `edit_scene`
  and `run_planning_checks`. (Julian's call.)
- Test: two scene agents each lengthen a line past a 15 s target; the second is refused with the reason.

**One real post**
- Cognition, "Don't Build Multi-Agents": "Actions carry implicit decisions, and conflicting decisions carry bad
  results." https://cognition.com/blog/dont-build-multi-agents

### 3. Failure attribution across agents (whole-system grades with a cause per stage) · **must apply now**

**What it is**
- Grade the whole ad. When it fails, record which agent or step caused it first.
- Without a cause on each failure, a system grade can't tell you which part to fix.

**Why it matters here**
- Julian taps a cause per failed clip from `evals/results.py:CAUSES`: planner, start picture, motion prompt, video
  model.
  - Under ADR 0012 the chain is longer: producer's instruction and quotes, planner's kind, agent's photo picks,
    picture check pass, H3's voice and words, word check, judge pass.
  - None of these is a cause he can tap.
- **The kind he grades may not be the planner's.**
  - Spec #327 item 52: Julian marks "right kind / wrong kind", and the marks become the rule.
  - `evals/round_export.py:export_ad` reads `scene.kind` and `scene.kind_why` from the scene row at export time.
  - A scene agent can change both (`change_scene.py:ChangeScene.run`). A kind the agent chose is then graded as the
    planner's. The planner's own kind is kept only in the `plan_ad` ModelCall's output; the kind the agent started
    from is on its first `SceneAgentRun.scene_before`.
- `round_export.py:_first_clip_step` still reads the pre-0012 step shape, so a scene-agent scene exports only its
  first try, listed twice as remake 1 (see Bugs).
- README's numbers are from round 6, before the producer took over and before ADR 0012. There is no system number
  for the current design.
- Automatic attribution doesn't work yet: the best method finds the right agent 53.5% of the time and the right
  step 14.2% (post below). A cause tap from Julian, with the right list, is the practical way.

**How to apply it in this build**
- Bring `CAUSES` up to the current chain. (Julian's call: what he grades.) For example: kind choice, producer's
  brief, photo picks, starting picture, video prompt, H3 voice/words, H3 motion, judge passed a bad clip, assembly.
- Export per scene: the planner's kind and why (from the `plan_ad` output) next to the final kind and which run
  changed it; each run's instruction and quotes; each picture's check answers; the word-check record.
- Report the share of failed scenes by first cause, then fix the top stage first.

**One real post**
- Zhang et al., "Which Agent Causes Task Failures and When? On Automated Failure Attribution of LLM Multi-Agent
  Systems": https://arxiv.org/abs/2505.00212

### 4. Context scoping per agent (maker and checker see the same request) · **must apply now**

**What it is**
- Give each agent the smallest set of facts it needs.
- A checker gets the same request the maker got: not more, not less. If they read different requests, they
  disagree, and the maker can't satisfy the check.

**Why it matters here**
- The scene agent gets only its own scene's checked quotes (`scene_tools.py:PassNote.run`,
  `scene_agent/__init__.py:_conversation`). That is the point of ADR 0011's quotes.
- **Its picture check gets every owner message, for every scene** (`scene_agent/picture_check.py:check`), and is
  told "an owner's correction about appearance beats the page".
  - A note "make scene 2 black" reaches scene 3's picture check. Scene 3 is drawn in the planned colour.
  - The check may fail it, and each redraw is paid, up to 6. The agent gets the check's one-line answers but never
    the note behind them, so it can't tell an owner's wish for another scene from a real fault.
  - This was a build choice (spec #303: "the owner's messages, as the fact check is handed the chat today").
- **The fact check reads different evidence depending on who calls it.**
  - From the producer it gets the whole chat (`work.py:_fact_check` → `work.py:_conversation`).
  - From a scene agent it gets only the run's quotes (`change_scene.py:_fact_checked`), as spec #303 story 50
    asked ("fact checked against the page and the run's quotes").
  - So a fact the owner gave in an earlier message passes one and fails the other.
- The clip judge's fix question already gets the run's instruction and quotes (`clip_judge.py:_asked_against_sent`).
  That one is consistent.

**How to apply it in this build**
- Write a "who sees what" row per agent and check (the table above is a start).
- Give the picture check the owner's words for this scene: every run's quotes for it, not the whole chat.
  (Julian's call: what a check is told.)
- Give `change_scene`'s fact check the same evidence: the scene's quotes across runs, plus facts the producer's
  checks already passed.
- Test: a note on scene 2 only. Assert scene 3's `handoffs("check_scene_picture")` holds none of its words.

**One real post**
- Anthropic, "Effective context engineering for AI agents": "find the smallest set of high-signal tokens that
  maximize the likelihood of your desired outcome."
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

### 5. Trace context propagation across agents (one trace per unit of work) · good to know

**What it is**
- Each unit of work (an owner's message, a note) is one trace.
- Every agent turn, tool call, model call and handoff inside it hangs under it and carries its IDs.
- Then one note can be read end to end, with its cost.

**Why it matters here**
- **A note to a busy agent lands in another message's trace.**
  - A scene step's span is parented on the tool call that created the step (`tasks.py:_do_the_work` →
    `tracing.scene_step(trace_id=step.tool_call.trace_id)`).
  - A note to a busy agent opens a run inside the running step (`scene_tools.py:open_note`).
  - So that note's drawing and filming appear under the first message's `start_scene_agent`, not under the note.
- **Scene-agent turns have no turn span.** `scene_agent/__init__.py:run` calls `loop.take_a_turn` without
  `tracing.agent_turn`, which only `loop.py:_work` uses. The `of="scene 3 · try 1"` argument is never passed.
- **Scene-agent calls carry no `why`** (`scene_agent/__init__.py:AGENT_SPEC` doesn't set `asks_why`), so
  `evals/agent_page.py` shows its calls without reasons. That is by design: spec #247 point 7 gives the `why` to
  the producer only (`loop.py:Agent`).
- **ModelCalls link to a ToolCall, never to a run** (`SceneAgentRun`). Per-run cost and a per-run trace can be
  read only by time windows, not by a link.

**How to apply it in this build**
- When a note opens a run, keep the `pass_note` call's trace and span IDs on the `SceneAgentRun`. Run that run's
  turns inside a span under it.
- Wrap scene-agent turns in `tracing.agent_turn("scene_agent", of=f"scene {n} · run {r}")`.
- Add the run to what a ModelCall is charged to, so cost and trace roll up per run (also fixes concept 1's cost).
- Test in `test_traces.py`: a note to a busy agent; its draw lands under the note's trace.

**One real post**
- OpenAI Agents SDK, "Tracing": agent, generation, function and handoff spans, and a `group_id` "to link multiple
  traces from the same conversation". https://openai.github.io/openai-agents-python/tracing/

### 6. Compounding error (series reliability, pass^k) · good to know

**What it is**
- An ad passes only if every scene passes, and a scene only if every step in it goes right. Pass rates multiply.
- With p per scene and n scenes, the ad passes at p^n.

**Why it matters here**
- `producer.py:assemble_the_ad` needs every scene. The default is 3–4 scenes (`planning.py:producer_decision_for`).
- At 0.9 a scene: 3 scenes ≈ 0.73, 4 ≈ 0.66. That's arithmetic, not a measurement.
- Inside a scene the chain is kind → picks → picture → picture check → H3 → transcript → word check → judge. A wrong
  pass anywhere ships (README "Known hole"; known limitation "the fixing agent trusts the clip judge").
- More tries raise p only when the judge is right. A wrong pass ends the run (`lifecycle.py:_passes`).

**How to apply it in this build**
- From Julian's round 8 scene grades: p per scene and per stage. Predict the ad pass rate as a product and compare
  it with his whole-ad grades. A gap means scenes interact (concept 2).
- Use it to choose where money goes: a better judge, more tries, or fewer scenes. (Julian's call.)

**One real post**
- Anthropic, "Demystifying evals for AI agents": pass^k "measures the probability that all k trials succeed", and
  it falls fast as k grows. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

### 7. Fan-in barrier and tail latency (the slowest scene sets the ad's time) · good to know

**What it is**
- When work fans out to parallel parts and waits for all of them, the slowest part sets the time.
- Rare slow parts come to dominate.

**Why it matters here**
- `tasks.py:_finish` wakes the producer only when no other step of the ad is running, or on a failure
  (`tasks.py:_a_result_wakes_it`). The producer is told not to assemble while any agent works.
- One scene's run can film up to 6 clips one after another (`film.py:Film.run`). Each is an H3 render, a transcript
  and judge asks of up to 600 s each (`catalog.py:SLOW_ANSWER_SECONDS`). One hard scene holds the whole ad.
- Nothing records per-scene time (`round_export.py:export_ad` keeps the ad's start and first finish only).

**How to apply it in this build**
- Record start, end and renders per run. Per ad, see which scene finished last and why.
- Option (Julian's call): a time budget per run, after which the agent films its best picture or gives up. Or
  assemble with the slow scene's last good version and tell the owner it's still being worked on.

**One real post**
- Dean and Barroso, "The Tail at Scale": slow episodes "unimportant in moderate size systems" come "to dominate
  overall service performance at large scale". https://research.google/pubs/the-tail-at-scale/

### 8. Workflows vs agents (code the path you already know) · good to know

**What it is**
- A workflow runs fixed steps in code. An agent chooses its steps.
- Use an agent where the path can't be known in advance.

**Why it matters here**
- README: "a first ad nearly always runs the same way". `producer.py:INSTRUCTIONS` spells the order, and the tools
  refuse other orders.
- Each step of that fixed path costs a producer turn with ~11k fixed tokens, plus the conversation.
- Machinery exists because a model can stop early on a fixed path: nudges (`tasks.py:_nudge`), filter retries
  (`tasks.py:_work_until_it_stops`), the turn hold (`note_photos.hold_the_turn`).
- In the first run, producer turns were 35–43% of each ad's cost, "mostly wake-ups"
  (`docs/runs/first-run-trace-findings.md`, item 14, before ADR 0012).
- ADR 0001 rejected "agent at the edges, pipeline for the spine".

**How to apply it in this build**
- Measure first, for free: producer turns and cost share per first ad in round 8 (ModelCall `purpose="produce"`).
- If high: one code tool runs read → plan → person → checks → music → start every scene, and stops at any question.
  The producer still handles every note. (Julian's call: reverses part of ADR 0001.)

**One real post**
- Anthropic, "Building effective agents": "workflows offer predictability and consistency for well-defined tasks",
  and agents bring "higher costs, and the potential for compounding errors".
  https://www.anthropic.com/engineering/building-effective-agents

### 9. Model inventory (one record per AI use) · good to know

**What it is**
- One record per AI use: who calls it, which model and why, what checks it, which eval measures it, how it fails.
  Kept current as the code changes.

**Why it matters here**
- `catalog.py:MODEL_FOR_PURPOSE` holds 49 purposes. 4 have no live caller (table above).
- The comment above `make_starting_picture` describes two purposes that no longer exist (the note rewrite and the
  first-send prompt).
- No reason is recorded for `fact_check` on gpt-5.6-terra, `scene_agent`, `check_scene_picture` or
  `draw_scene_picture`.
- Few live purposes have an eval (table above). The catalog doesn't say which.

**How to apply it in this build**
- Make each catalog entry a record: model, reason (doc link), caller, eval, notice kinds.
- A unit test: every purpose is called by app code or marked retired with a date. A missing reason fails.
- Delete retired purposes in the next contract step, unless an old record still looks one up
  (`work.py:_clip_purposes` checks retired clip purposes).

**One real post**
- NIST AI RMF Playbook, GOVERN 1.6: "Mechanisms are in place to inventory AI systems", with documentation and
  links to source code per system. https://airc.nist.gov/airmf-resources/playbook/govern/

---

## Also seen here

- #3 Timeouts, deadlines and retry budgets: `agents/scene_agent/__init__.py:run` (no deadline on a run);
  `jobs/work.py:person_and_room` (every scene agent waits on the ad's lock while the shared pictures are drawn).
- #12 Append-only, immutable records: `agents/scene_agent/lifecycle.py:_put_back` (a give-up overwrites the scene
  row from its snapshot; `ChangeScene.run` edits it in place).
- #15 Aggregate invariants: `agents/scene_agent/change_scene.py:ChangeScene.run` (the target length isn't
  re-checked when a scene agent writes the plan).
- #17 Over-optimizing against an imperfect judge (known): `agents/scene_agent/lifecycle.py:_passes` (the run ends
  on the first clip the product check doesn't fail).
- #19 Stopping conditions (known: limitations "There is no limit on its turns"): `agents/scene_agent/__init__.py:run`
  (no turn cap; a turn with no tool call gets `media.py:REMINDER` and loops); `agents/loop.py:_calls_since_the_user_spoke`
  (scene-agent calls don't count toward the 100); `agents/scene_tools.py:PassNote.run` (each note opens fresh 6/6 caps).
- #20 Cost reconciliation: `gateway/catalog.py:video_cost_usd` (0.4 credits/s in the catalog, 0.2 seen in Creatify's
  sessions, ADR 0012); `agents/voice_changes.py:consent` quotes the owner from it, one try a scene.
- #24 Explicit state machine: `agents/producer.py:assemble_the_ad` ("a scene is busy" is guessed from step kinds, so
  the new `SCENE_AGENT` kind slipped past it).
- #25 Context budget: `agents/scene_agent/__init__.py:_conversation` (a scene's chat lasts the ad's life with no fold).
- #35 Agent evals on a real model: `tests/scene_script.py:script_scene_agents` (scene agents and the producer are
  scripted everywhere; no real-model eval since ADR 0012).
- #42 Leases instead of "one worker, always": `agents/tasks.py:another_session_is_busy` (while one ad's agents film,
  every other shop owner gets "Another ad is being made right now").
- #43 Independent evidence for checks (known, ADR 0011): `agents/scene_agent/draw_starting_picture.py:make_picture`
  (the picture check sees exactly the agent's own picks).
- #44 Stable prompt prefix: `agents/scene_agent/__init__.py:_conversation` (every scene's live row sits in the intro,
  so another agent's edit changes the prefix; it also stops `gateway/gateway.py:_answered_before` matching a paid
  turn after a restart, so it is paid again).
- #45 Atomic counters: `agents/scene_agent/film.py:Film.run` (`clips=window.clips + 1`) and
  `agents/scene_agent/draw_starting_picture.py:make_picture` (`pictures=window.pictures + 1`).
- #66 Expand/contract migrations: `jobs/work.py:go_back` (the contract left busy guards keyed to retired step kinds);
  `gateway/catalog.py:MODEL_FOR_PURPOSE` (retired purposes kept).
- #68 Durable execution: `agents/tasks.py:carry_on_after_the_worker_starts` (scene-agent steps now resume from
  checkpoints; other running kinds are still failed).
- #79 Rows, not a growing JSON array: `jobs/word_check.py:check` (one `WORD_CHECK` per clip, from every agent at
  once, appended to `job.warnings` under the Job lock by `jobs/notices.py:post_notice`).

---

## Bugs and loose ends

Found while reading; not concepts. Read from the code. I ran nothing paid and no tests.

- **Busy-scene guards look for a step kind the scene agent never creates.** These refuse while a `CLIP` or
  `STARTING_PICTURE` step is running. Scene-agent try steps are created already `FINISHED`
  (`scene_agent/lifecycle.py:_try_step`), and the agent's own step is `SCENE_AGENT`. So during a fix, nothing stops:
  - assembling the ad: `agents/producer.py:assemble_the_ad`;
  - the producer's version swaps and kept tries: `_current_clip_in_use`, `jobs/work.py:_try_to_use`,
    `keep_the_clip_before_it_failed`, `go_back`;
  - the owner's card swap (`jobs/card_versions.py:use` → `work.go_back`).

  `producer.py:_no_clip_being_made` was updated to include `SCENE_AGENT`; these weren't. The tests that cover the
  guards hand-build a running `CLIP` step.
- **The round export reads the pre-0012 shape** (`evals/round_export.py:_first_clip_step`, `export_ad`, `_notes`):
  - Each try is its own `CLIP` step, so the first try is exported alone.
  - Its `remake` is 1 (`film.py:Film.run` sets `remake=row.number`), so it shows twice as remake 1, and no `first.mp4` is written.
  - `kept` is always that first try.
  - `gave_up` is never found: the `AgentFix` hangs on the `SCENE_AGENT` step, not the try step.
  - `notes.json` is empty: `pass_note` never sets `owner_note`.

  This feeds Julian's grading page.
- **Give-ups are told twice.** Once by code's owner notice, in the agent's words (`scene_agent/__init__.py:finish`),
  and once more by the producer, as `producer.py:INSTRUCTIONS` tells it to.
- **The run's cost is the step's cost.** `scene_agent/__init__.py:finished` uses `SceneStep.cost_usd`, which sums
  every run of a busy step.
- **First ads quote the link as the owner's words** (`scene_tools.py:StartSceneAgent`, `quotes` `min_length=1`).
- **The talking fallback ladder can't trigger from a motion fault that leaves the product right** since #350
  (merged with #353; `clip_judge.py:judged`, `scene_agent/instructions.py:INSTRUCTIONS`). PRODUCT can still fail a
  product that moves unlike the real one. ADR 0012, README ("impossible motion") and CONTEXT ("product/motion
  clip judge") still describe a motion judge that fails clips.
- **The picture check's owner notice has no scene number** ("The starting picture could not be checked",
  `scene_agent/picture_check.py:check`). The others pass `scene=`.
- **Unexpected tool errors reach Sentry only.** `loop.py:_run` logs them; no notice Kind lands in `job.warnings`,
  so `scripts/ad-runs/ad_warnings.py` doesn't list them.
- **Docs and code disagree:**
  - AGENTS.md says a starting worker "marks every running step dead". `tasks.py:carry_on_after_the_worker_starts`
    now resumes scene-agent steps, and its own docstring still says each running step is failed.
  - README: "The producer has 30 tools". `producer.py:PRODUCER` has 27.
  - `settings.py`: "the B-roll starting picture check, which nothing switches off". `picture_check.py:check` returns
    "passed" when `QUALITY_CHECKS` is off (spec #303 decision 6). The heartbeat comment above it is cut off from its
    setting.
- **Catalog:** `fix_scene`, `scene_picture_check`, `make_starting_picture` and `check_starting_picture` have no
  live caller (`gateway/catalog.py:MODEL_FOR_PURPOSE`). Stale comments sit above `make_starting_picture`.
- **Dead code in the scene agent:**
  - `scene_agent/__init__.py:_conversation` sets `seconds` from the audio, then overwrites it.
  - `scene_agent/__init__.py:run` sets `person_shown` twice.
  - `tracing.agent_turn`'s `of` is never passed.

---

## Checked by an independent reader

Read against the code at `fd346df`; nothing run, nothing paid.

- 1 Typed handoff contracts · CORRECTED · Quote, one-line result, step-wide cost, double give-up and `_told_it_waits` all hold (the producer also never sees the give-up notice: `loop.py:_conversation` leaves notices out). The judge bullet was too strong: PRODUCT still fails a product that moves unlike the real one, so the ladder can fire on that; only motion faults that leave the product right no longer fail. The change is #350, merged with #353. The cost heading now names a note to a busy agent. Stays must apply now.
- 2 Conflicting implicit decisions · CORRECTED · Every re-check and gap holds (`ChangeScene.run`, `_fit_length` only in `run_checks`, `KEEPS_ITS_PLACE` only in the rewrite prompts). "Every scene turning talking" now says no plan check holds that rule either since #327. Stays must apply now.
- 3 Failure attribution · CORRECTED · `CAUSES`, the row-read kind and the old step shape hold. The planner's kind isn't only in `plan_ad`: the kind the agent started from is on its first `SceneAgentRun.scene_before`. "Likely export wrong" is now certain: first try only, listed twice as remake 1. Stays must apply now: the next graded round (#327 item 15) reads this export.
- 4 Context scoping · CORRECTED · The picture check does read every owner message (`picture_check.py:check`), and the fact check does read only the run's quotes. Two fixes: the agent is handed the check's answers, just not the note, and the run's-quotes scope is what spec #303 story 50 asked for. Stays must apply now.
- 5 Trace propagation · CORRECTED · Every fact holds (trace parent, no `agent_turn`, no `why`, no run link). Retagged good to know: it changes no ad, owner message or charge, and the owner-facing cost is concept 1's. The missing `why` is by design (spec #247 point 7). Per-run cost can be read by time windows.

Cite fixes:
- Planner row: `check_opening_kind` guards edits, not the planner's output. The planner's first-scene rule is `planning.py:Plan._opens_on_the_person`. Fixed.
- Every other `file:symbol` cite in the concepts and the table names a file that exists and a symbol in it.

Not changed, for the author (Bugs section):
- The round export bullet says the first try shows "as 'first' and 'remake-1'". It shows twice as remake 1, because `Film.run` sets `remake=1` on try 1, and no `first.mp4` is written.
- The ladder bullet has the same overstatement as concept 1.
