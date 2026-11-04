# Design mentor: line rewrites (rewrite, shorten, lengthen)

Design read: `CONTEXT.md` (Planning check, Fact check, Scene, Clip, User-supplied fact, Quote), spec #1
"Planning checks", spec #210, spec #327, ADR 0012, `docs/limitations.md`. Code read at `fd346df`: the
functions below, `jobs/checks.py`, `gateway/gateway.py:call_model`, `agents/producer.py:RunPlanningChecks`,
`jobs/plan_edits.py`, and the tests listed. Backend paths are under `backend/`.

Teaching only. Applying any concept is a design change and needs Julian's yes.

---

## Systems in this group

Costs are **estimates**, not a bill: catalog prices (`gateway/catalog.py:PRICE_PER_MILLION_TOKENS`) times
calls the code allows. Token counts are guesses from instruction sizes measured locally and a typical
page (about 3k tokens). Every one of these calls is followed by a paid fact check
(`gpt-5.6-terra`, about $0.02–0.04) of what it wrote.

| System | What it is asked | Model and why | Input → output | What checks the output | Eval | How it fails | Rough cost per ad (estimate) |
|---|---|---|---|---|---|---|---|
| **rewrite_line** | Rewrite one scene that failed the fact check: line, action, B-roll details. An inline prompt in `jobs/work.py:_rewrite_scene` (about 620 tokens with the one-action rule), not `jobs/checks.py:REWRITE_INSTRUCTIONS`. Called from `jobs/work.py:_fact_check` | `gpt-6.1-sol` (`gateway/catalog.py:MODEL_FOR_PURPOSE`). No reason in the catalog. `tests/test_frozen_rules.py:MOVED_TO_6_1_SOL`: "every gpt-5.6-sol job moves to gpt-6.1-sol, with no comparison test" | `jobs/checks.py:RewriteHandoff` (page, conversation, colour, library counts, colour photos, whole script, scene, every past problem) plus every library sheet as images → `jobs/checks.py:rewritten_scene_for` (a `ScriptScene`) | Answer shape: kind kept, action never null, B-roll has no face, needed photos exist (`rewritten_scene_for`). Then the fact check again (`_fact_check` loop). Then the length fit | None | A failed call fails `run_planning_checks` (`agents/loop.py:_run`, FAILED to the producer). Shape broken twice: `ANSWER_BROKE_A_RULE` (developer). Sheets are sent without `library_sheets.past_the_filter`, so one refused sheet fails the call. Posts `PLANNER_NOT_SHOWN` and `NO_PHOTO_FOR_A_PICTURE` (developer). After 2 rewrites: `Asking(about="line")` (`_about_line`) | About 9–17k tokens in (with sheets), 1–3k out: **$0.03–0.06 a call**. 0 calls when every line passes; up to 2 per failing scene per round |
| **shorten_script** | Trim lines or drop scenes to fit the owner's target length. `jobs/checks.py:SHORTEN_INSTRUCTIONS`. Called from `jobs/work.py:_fit_length` → `_shorten` | Same: `gpt-6.1-sol`, same reason | `jobs/checks.py:ShortenHandoff` (page, conversation, colour, target, `most_words`, script) → `jobs/checks.py:shortened_script_for` (lines, each with its source scene) | Shape: scenes exist, in order, each once, first scene kept. Changed lines are fact checked again (`_shorten` keeps `fact_checked` only for identical text). `fits_target` is measured again | None | As above. After 2 since the owner last spoke (`_shortened_since_the_user_spoke`): `Asking(about="length")` | About 5k in, 1–3k out: **$0.02–0.04 a call**. Only with a target and the owner's "shorten"; up to 2 per owner reply |
| **shorten_line** | Rewrite one line to at most 35 words. `jobs/checks.py:SHORTEN_LINE_INSTRUCTIONS`. Called from `jobs/work.py:_fit_for` → `_shorten_line` → `_shorter_line` | Same: `gpt-6.1-sol`, same reason | `jobs/checks.py:ShortenLineHandoff` (page, conversation, colour, script, scene, `most_words`) → `jobs/checks.py:RewrittenLine` | Shape: only "not empty". Word count is checked one loop later in `_fit_for`, after `_new_line` has cleared `fact_checked` and a fact check was paid | None | As above. After 3 per scene number in the job's whole life: `Asking(about="line_length")` (`_ask_for_a_shorter_line`) | About 4–5k in, 0.5–2k out: **$0.01–0.03 a call**. Up to 3 per scene number |
| **lengthen_line** | Rewrite one line to at least 10 words. `jobs/checks.py:LENGTHEN_LINE_INSTRUCTIONS`. Called from `jobs/work.py:_fit_for` → `_lengthen_line` | Same: `gpt-6.1-sol`, same reason | `jobs/checks.py:LengthenLineHandoff` (… `fewest_words`) → `RewrittenLine` | Same as shorten_line | None | As above. After 2 per scene number: `LineTooShort` → `Refused` to the producer (`agents/producer.py:RunPlanningChecks.run`), with no notice | Same as shorten_line. Up to 2 per scene number |

What the money depends on: these four calls cost cents. The **H3 seconds** they choose cost more. A
line sets its clip length (`jobs/checks.py:h3_clip_seconds`), at $0.0792 a second
(`gateway/catalog.py:PRICE_PER_SECOND_OF_VIDEO`), for every try.

---

## How it is tested today

- `tests/test_producer_checks.py` (39 tests): a failed line is rewritten and checked again. After 2
  rewrites the owner is asked. Shortening keeps parts, overlays, B-roll labels and second states; a
  broken shortened script changes nothing; a too-long line gets 3 shortenings and then a question.
- `tests/test_h3_planning.py` (9): kinds and their reasons survive rewrites and shortening; 2
  lengthenings end in a refusal; 3 shortenings end in a question; the target budget is 42 words for 15 s.
- `tests/test_producer_broll_line_length.py`, `tests/test_producer_broll_long_line.py` (5 + 5): both
  scene kinds use the 4–14 s band at 2.5 words a second; a restart doesn't pay for a shortening twice.
- `tests/test_producer_restarts.py`: a rewrite stopped by the worker is redone without checking again.
- `tests/test_frozen_rules.py:test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol`: the model pin.

What they cannot see:
- Every rewriter answer is scripted (`fake_model.respond`). No test sees whether a real model hits the
  word budget, keeps the price or call to action, or changes the line's meaning.
- Instruction tests check only the one-action text and "keep the hook and the call to action". Nothing
  checks that a rewriter is told the producer's messages are not facts (ADR 0002).
- No test has an owner's own line under 10 words. I ran one in a scratch copy (not committed): the
  owner's "Holds 350 ml of hot tea." was replaced by the fake lengthener's line, paid as `lengthen_line`,
  and the checks passed with no notice.
- No test compares the target fit with how long the clips actually play.
- No test reorders or removes a scene after its tries are counted, or shortens after clips are made.

---

## Concepts, ranked by impact on this build

### 1. Single source of truth for a derived value (one length model) — **must apply now**

- **What it is:** each fact gets one function, used everywhere. Here the fact is "how long the ad plays".
- **Why it matters here:**
  - `jobs/work.py:_fit_length` measures speech only: `jobs/checks.py:script_seconds`, words ÷ 2.5.
  - Clips play whole at `jobs/checks.py:h3_clip_seconds`: speech rounded up, plus 1 s, at least 5 s.
    `jobs/work.py:music_seconds` already adds up those.
  - ADR 0012 changed how long a clip is, but the target check still measures speech alone.
  - I ran the pure functions locally:
    - A 15 s target with 3 lines of 12 words: the check says 14.4 s and fits. The clips play 18 s.
    - A 30 s target with 4 lines of 20 words: the check says 32 s and fits. The clips play 36 s.
  - shorten_script's budget (`jobs/checks.py:most_words`) uses the same model, so a "fitted" script
    still runs over. The owner's question ("Your script runs about …") quotes the wrong number.
- **How to apply it:**
  - Write one `ad_seconds(lines)` that adds up `h3_clip_seconds` per line.
  - Use it in `_fit_length`, in the owner's question, and in `music_seconds`' guess for clips not yet made.
  - Give shorten_script a word budget per scene derived from it.
  - Whether the target counts played seconds, and whether the 2 s allowance stays: (Julian's call).
- **One real post:** Dave Thomas and Andy Hunt on DRY, "every piece of system knowledge should have one
  authoritative, unambiguous representation":
  https://www.artima.com/articles/orthogonality-and-the-dry-principle

### 2. Length-controlled generation (models can't count words) — **must apply now**

- **What it is:**
  - Models miss word targets: they read tokens, not words, and can't count their own output well.
  - The usual fix: state the target, count in code, ask again with the miss named, or ask for several
    lines.
- **Why it matters here:**
  - `jobs/work.py:_shorter_line` and `_lengthen_line` pass `most_words` / `fewest_words`.
    `jobs/checks.py:RewrittenLine` checks only that the line isn't empty.
  - A miss is found one loop later in `jobs/work.py:_fit_for`, after `_new_line` has cleared
    `fact_checked` and the missed line was paid for and fact checked. It also uses up one of the
    scene's lifetime tries (3 to shorten, 2 to lengthen).
  - rewrite_line is given no length at all (`jobs/checks.py:RewriteHandoff`). The prompt that told it
    "about the same length" is dead (concept 4).
  - The budget given is the hard limit, not the aim. ADR 0012 aims lines at 8–10 s (20–25 words);
    shorten_line is told "at most 35" (`most_words_in_a_broll_line`), which is 14 s.
  - A 35-word line films a 15 s clip; a 25-word line films 11 s. When the answer lands at the limit,
    that is 4 s × $0.0792 = up to $0.32 more per try, with up to 6 tries per scene.
- **How to apply it:**
  - Check `count_words(line)` against the band in the answer's validator. The gateway's existing re-ask
    (`gateway/gateway.py:call_model`, `ANSWER_AGAIN`) then names the miss before any fact check is paid.
  - Send the line's current word count and an aim (20–25 words) next to the hard band (10–35).
  - Measure how often each rewriter lands in the band (concept 6).
  - Aiming at the middle instead of the limit changes clip length and money: (Julian's call).
- **One real post:** LIFEBench, 26 models on length instructions: most "deteriorate sharply beyond a
  certain threshold" and often come out too short: https://arxiv.org/abs/2505.16234

### 3. Evaluator-optimizer loop (every writer sees every check) — **must apply now**

- **What it is:**
  - One model writes, checkers judge, and the writer tries again.
  - The loop settles only when the writer knows every rule the checkers apply. Otherwise one fix
    undoes another.
- **Why it matters here:**
  - There are three checks (facts, the 4–14 s line band, the script target) and four writers. The
    three length writers are told the fact rule plus one length bound each; rewrite_line is told the
    fact rule and no length at all:
    - `RewriteHandoff` has no word band.
    - `ShortenHandoff` has a script budget but no 10-word minimum per line.
    - lengthen_line adds words without knowing the target.
  - `_fit_length` fits each line first, then the target. So shorten_script can trim a line to 8 words.
    lengthen_line pads it again, the script is over target, and it is shortened again. After 2 rounds the
    owner is asked; after 2 lengthenings, `LineTooShort` refuses the whole check.
  - `_new_line` sets `fact_problems = []`, so the 2-rewrite cap starts over after every length fix. The
    stop rule isn't per line.
- **How to apply it:**
  - Write one "line rules" block and give it to all four rewriters:
    - facts come only from the page and the owner, never from the producer's messages;
    - 10–35 words, aiming at 20–25;
    - keep the price;
    - keep the scene's place in the script.
  - The three length prompts already carry the facts, price and place rules
    (`SHORTEN_LINE_INSTRUCTIONS`, `LENGTHEN_LINE_INSTRUCTIONS`, `SHORTEN_INSTRUCTIONS`). What they
    lack is each other's bounds. The rewrite prompt lacks all three (concept 4).
  - Put the numbers in every handoff, and give shorten_script per-line bounds.
  - Count repairs per line across all repair kinds. Changing the caps: (Julian's call).
- **One real post:** Anthropic, "Building effective agents", evaluator-optimizer: "particularly effective
  when we have clear evaluation criteria": https://www.anthropic.com/engineering/building-effective-agents

### 4. Prompt management (one source per prompt, pinned by tests) — **must apply now**

- **What it is:** prompts are code. Each purpose has one definition, with a version and with tests that
  pin the rules that matter.
- **Why it matters here:**
  - `jobs/checks.py:REWRITE_INSTRUCTIONS` (about 1,770 tokens) has been unused since c635889. The live
    prompt is an inline string in `jobs/work.py:_rewrite_scene`.
  - The inline prompt lost these rules:
    - the producer's messages are not facts (ADR 0002, spec #1). It still says claims must come
      from "the page, the shop owner's words, or for looks only the product photos";
    - keep about the same length;
    - keep the price;
    - most of `KEEPS_ITS_PLACE`: it keeps "Keep its place in the whole script", but not "follows
      on" and "never says again what another scene's line already says";
    - what each handoff field means;
    - the rules for B-roll details (`BROLL_DETAILS_INSTRUCTIONS`; only the one-action rule stays).
  - The fact check catches some of what the rewriter now gets wrong, at a cost of one paid round each.
    It can't catch a dropped price: a line with no price claims nothing false, and no code checks
    that one line still says the price.
  - Three prompts still describe the retired voice. `SHORTEN_LINE_INSTRUCTIONS`,
    `LENGTHEN_LINE_INSTRUCTIONS` and `SHORTEN_INSTRUCTIONS` say "the speed the chosen voice speaks";
    the speed is now fixed.
  - `LENGTHEN_LINE_INSTRUCTIONS` says the scene "shows something else while the person's voice says
    its line". Talking lines are lengthened too: `tests/test_producer_broll_line_length.py` runs both
    kinds.
  - The producer's tool text (`agents/producer.py:RunPlanningChecks`) says "a line the person says to
    camera … after 2 shortenings". The code does it for any scene, after 3.
- **How to apply it:**
  - Move the live prompt into `checks.py` as the only `REWRITE_INSTRUCTIONS`, and delete the dead one.
  - Add one test per rewriter that pins the ADR 0002 rule, as `tests/test_frozen_rules.py` does for the
    producer.
  - Build the tool text's numbers from the constants.
  - The gateway already stores each call's system prompt by digest (`gateway/gateway.py:_system_prompt`),
    so the versions are recorded today.
  - Putting approved rules back is wording, but #331 chose the short text, so check with Julian first.
- **One real post:** Langfuse prompt management (already in the stack): versions, labels, and linking
  prompts to traces "to analyze performance by prompt version":
  https://langfuse.com/docs/prompt-management/overview

### 5. Stable identifiers (key by ID, not position) — **must apply now**

- **What it is:**
  - Track a thing by its own ID, never by where it sits.
  - Julian knows this from React: an index used as a key breaks when items move.
- **Why it matters here:**
  - `jobs/work.py:_fit_for` counts tries by `handoff__scene=scene.number`.
  - Numbers move with `add_scene`, `remove_scene`, `reorder_scenes` (`jobs/plan_edits.py:renumber`, which
    already moves chat files by `pk`) and with `_shorten`. After a reorder, a scene inherits another
    scene's used-up tries.
  - `jobs/work.py:_shorten` puts the answer onto rows by position. If scene 2 is dropped, row 2 takes
    scene 3's line and fields, but keeps row 2's reference photos (a foreign key), steps and produced items.
  - The rows left over at the end are hard-deleted with `scene.delete()`. That cascades to their steps
    and produced items (`jobs/models.py`, `ProducedItem.scene`, CASCADE).
  - `jobs/plan_edits.py:remove_scene` instead soft-deletes and keeps what was paid for.
  - This path is reached when the checks run after scenes are made. `EditScene` tells the producer to
    run them after a changed line.
- **How to apply it:**
  - Keep the try counts on the `Scene` row, or key them by `scene.pk`.
  - Make `_shorten` drop and change scenes through `plan_edits.remove_scene` / `edit_scene`.
  - Changing what counts as a try: (Julian's call).
- **One real post:** React docs, "Index as a key often leads to subtle and confusing bugs … Keys must not
  change": https://react.dev/learn/rendering-lists

### 6. Code-based graders (an offline eval for the rewriters) — **good to know**

Retagged: a missing eval doesn't hurt the build today. The harms it would measure are concepts 2–4,
and the recorded cases stay in the database until someone runs it.

- **What it is:** eval checks that a program scores, such as counts, whether a string is present, and
  structure. Fast, cheap, the same every run.
- **Why it matters here:**
  - Nothing measures these four. `evals/` has nothing for them, and the legacy decision map lists only
    "Judge: fact check re-runs".
  - Most of the ways they fail can be checked by code:
    - the line lands in the word band;
    - the page's price string is still there;
    - the first and last scenes are kept;
    - unchanged lines are byte-identical;
    - the fact check passes afterwards.
  - Each recorded `ModelCall` holds the handoff and the output (`gateway/gateway.py:_recorded`), so the
    cases already exist in past runs.
- **How to apply it:**
  - Write an eval module that loads recorded handoffs from the app's `ModelCall` rows (round exports
    keep only each ad's cost totals, `evals/round_export.py:_costs`), asks each rewriter again,
    scores with code, and reports hit rates with intervals.
  - Each re-ask is one text call (about $0.01–0.06): (Julian's call).
- **One real post:** Anthropic, "Demystifying evals for AI agents": code-based graders are "Fast, Cheap,
  Objective, Reproducible", but "brittle to valid variations":
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

### 7. Minimal edits (ask for the changes, not the whole thing) — **good to know**

- **What it is:** ask the model only for what changes and keep the rest in code. Asking it to copy
  everything back costs tokens and lets small drift in.
- **Why it matters here:**
  - `jobs/work.py:_shorten` asks for the whole script back, and unchanged lines must come back exactly.
  - `jobs/models.py:Scene.change_line` treats any difference as a new line: status PLANNED, so its clip
    is made again (limitation "A new line's clip is made again").
  - `_shorten` doesn't normalise whitespace before comparing; `_new_line` does.
- **How to apply it:**
  - Change the answer shape to `drop: [scene]` and `trim: [{scene, line}]`. Code keeps every other line
    as it was.
  - The validator then checks that the first and last scenes are kept.
- **One real post:** aider's edit formats: "whole" is "slow and costly because the LLM has to return the
  entire file even if just a few lines are edited": https://aider.chat/docs/more/edit-formats.html

### 8. Best-of-N with a code verifier (rejection sampling) — **good to know**

- **What it is:** ask for several candidates at once, then let code keep the first one that passes.
- **Why it matters here:** `jobs/work.py:_fit_for` tries one line at a time. Each miss costs one paid
  call, one paid fact check and one lifetime try.
- **How to apply it:**
  - Ask for 3 candidate lines in one call.
  - Code drops the ones outside the band or without the price.
  - Fact check the rest in one call (`FactCheck` already takes many lines) and keep the first that passes.
  - This changes the number of calls per ad: (Julian's call).
- **One real post:** "Large Language Monkeys": where an automatic verifier exists, more samples "directly
  translate into improved performance": https://arxiv.org/abs/2407.21787

### 9. Model routing (right-size the model per purpose) — **good to know**

- **What it is:** send each task to the cheapest model that passes its eval, not one model for everything.
- **Why it matters here:**
  - All four run on `gpt-6.1-sol` ($2 / $10 per million tokens) with no comparison test
    (`tests/test_frozen_rules.py:MOVED_TO_6_1_SOL`).
  - `gpt-6-luna` costs $0.10 / $0.50 (`gateway/catalog.py:PRICE_PER_MILLION_TOKENS`). Cutting a line to
    35 words is a small text task.
  - Text spend here is cents and H3 seconds cost far more, so this comes last.
- **How to apply it:** only after concept 6 exists. Run shorten_line and lengthen_line on luna against
  the same cases, and switch if the hit rate holds: (Julian's call).
- **One real post:** FrugalGPT, LLM cascades: "up to 98% cost reduction" at matched quality:
  https://arxiv.org/abs/2305.05176

---

## Also seen here

- #1 Idempotency keys: `jobs/work.py:_rewrite_scene`, `_lengthen_line` (no `pay_once`). A restart between
  the paid answer and the save pays again, and `_fit_for` counts the lost try.
- #8 Groundedness eval for the fact check: `jobs/work.py:_ask_fact_check` is the only judge of every
  rewrite, and it is not measured.
- #9 Golden dataset: `evals/` holds no rewrite cases.
- #15 Aggregate invariants: `jobs/checks.py:shortened_script_for` checks only that the first scene is
  kept. Keeping the last scene and "one line must still say the price" are in the prompt only.
- #19 Stopping conditions: `jobs/work.py:_new_line` resets `fact_problems`, so the rewrite cap starts over
  after every length fix.
- #23 Data provenance: `jobs/work.py:_fit_for`. Who wrote a line is only inferred (`_chosen_by_the_user`,
  now unused), so an owner's line is lengthened by a model.
- #24 Explicit state machine: `jobs/work.py:_chosen_by_the_user` reads "the owner chose it" from
  `fact_checked` plus a failure that was never rewritten.
- #36 Text normalization before length estimates: `jobs/checks.py:count_words` counts "$24.00" as one word
  though it is spoken as several. `jobs/work.py:_shorten` compares lines without normalising them.
- #37 Constrained decoding: `jobs/checks.py:RewrittenLine` doesn't hold the word budget that code knows.
- #43 Independent evidence for checks (done right): the rewriter (`gpt-6.1-sol`) and the fact check
  (`gpt-5.6-terra`) are different models (`gateway/catalog.py:MODEL_FOR_PURPOSE`).
- #58 Feedback-driven retries (done right): `jobs/work.py:_rewrite_scene` hands back every past problem,
  oldest first.
- #62 Human-in-the-loop approval as a structured action: `agents/producer.py:RunPlanningChecks.run`
  stores the owner's "own" or "keep" only as `fact_checked=True`, and the length fit then rewrites it.

---

## Bugs and loose ends

- **An owner's own line under 10 words is rewritten by a model, with no notice.**
  - `jobs/work.py:_fit_for` lost its `_chosen_by_the_user` check in c635889.
    `agents/producer.py:RunPlanningChecks.run` refuses only lines over 14 s.
  - This breaks CONTEXT's "User-supplied fact … used as written".
  - Reproduced in a scratch test, not committed.
- **The target fit measures speech, not the clips that play.** See concept 1. It shows on every ad with
  a target, and the owner is told the wrong length.
- **`_shorten` hard-deletes dropped scenes and moves content between rows by position.** Paid steps and
  produced items cascade away. Reference photos stay at the old position. `plan_edits.remove_scene` does
  this correctly.
- **Try caps are keyed by scene number for the job's whole life** (`_fit_for`). They move with
  renumbering. A new line on a scene whose tries are used up gets none.
- **The shortening validator is looser than its prompt.** `SHORTEN_INSTRUCTIONS` says never drop the
  last scene and keep the price; `shortened_script_for` checks neither.
- **The rewrite skips the safety-filter fallback the planner uses.** `_rewrite_scene` sends sheets
  without `library_sheets.past_the_filter`, which `jobs/work.py:plan` uses.
- **The `LineTooShort` refusal doesn't say "10 words".**
  - It tells the producer "shorter than 4 seconds … Revise the line", and the next check allows no
    lengthening.
  - Spec #1's "if still short, it is kept" became a refusal.
  - The test name says "for the owner to revise", but the message is for the producer.
- **The producer is told the wrong caps.** `RunPlanningChecks` says "after 2 rewrites … a line the
  person says to camera … after 2 shortenings". The code does it for any scene, after 3.
- **Dead code:**
  - `jobs/checks.py:REWRITE_INSTRUCTIONS`.
  - `jobs/work.py:_shortenings`, `_showed_something` and `_chosen_by_the_user`.
  - The `pay_once` parameter of `_shorter_line`.
  - The `if rewrite.shows is None:` branch in `_rewrite_scene`; the validator now forbids a null action.
  - `notices.LINE_TO_CAMERA`.
- **Stale comments and docs:**
  - `_rewrite_scene`: "A scene where the person talks is rewritten on text alone". Talking scenes now
    have an action, so they are sent the sheets.
  - The `_fit_length` docstring: "or a B-roll scene made a talking one".
  - The comments on `MOST_REWRITES` and `MOST_LENGTHENINGS` in `checks.py`: "kept as it is", "said to camera".
  - The `Scene.shortened_from` help text: "because their audio was too long".
  - `docs/limitations.md` "A new line's clip is made again": "or a B-roll line is said to camera".

---

## Checked by an independent reader

Checked against the code at `fd346df`, re-running the length sums by hand from `script_seconds` and `h3_clip_seconds`.

- Single source of truth for a derived value · CONFIRMED · nothing changed. 36 words → 14.4 s "fits" a 15 s target while 3 × 6 s clips play 18 s; 80 words → 32 s fits 30 s while 4 × 9 s play 36 s; `most_words` and the owner's question use the same speech-only sum.
- Length-controlled generation · CORRECTED · "+$0.32 a try" became "up to $0.32 more per try when the answer lands at the limit". The rest holds: `RewrittenLine` checks only "not empty", and `run_checks` pays a fact check before `_fit_for` sees the miss.
- Evaluator-optimizer loop · CORRECTED · "each writer is told only one rule" was wrong: the three length prompts already carry the fact, price and place rules plus one length bound each; only rewrite_line lacks them. The trim-pad-trim loop and `_new_line` resetting `fact_problems` hold.
- Prompt management · CORRECTED · the inline rewrite prompt still says "Keep its place in the whole script" and "supported by the page, the shop owner's words"; what it lost is the producer-message rule, the price rule, the length rule, most of `KEEPS_ITS_PLACE` and `BROLL_DETAILS_INSTRUCTIONS`. Added why the price loss hurts today: no code checks that a line still says the price.
- Stable identifiers · CONFIRMED · nothing changed. `_fit_for` counts by `handoff__scene=scene.number`; `_shorten` zips answers onto rows by position and calls `scene.delete()`, which cascades through `SceneStep.scene` and `ProducedItem.scene`; `remove_scene` sets `removed_at`.
- Code-based graders · CORRECTED, retagged good to know · round exports hold only cost totals, so the cases come from `ModelCall` rows, not exports. A missing eval measures harm rather than causing it.

Cite fixes: none needed. Every `file:symbol` cite in this report, the "Systems in this group" table included, names a file and a symbol that exist.
