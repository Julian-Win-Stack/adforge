# Design mentor: checks & evals

**Area:** the clip judge, the planning checks (fact check, length fit), the fix check, and the
evals that measure them.
**Read:** ADR 0008, `docs/decision-map.md`, `docs/video-eval/`, `docs/runs/`, CONTEXT.md (Checks),
spec #1 (Planning checks, Quality checks, Judgements, Testing Decisions), spec #210 (The fixing
agent, Testing Decisions).
**Code:** `backend/jobs/clip_judge.py`, `checks.py`, `fix_check.py`, `auto_fix.py`, the fact check
in `jobs/work.py`, `backend/evals/`, `backend/video-eval/`, `backend/broll-test/should_ask.py`, and
their tests. As of `bfc4dfc` (2026-11-03).

## How this area is tested today

- Chat-flow tests, every model faked at the gateway: `tests/test_clip_judge_references.py`,
  `test_producer_clip_judge.py`, `test_producer_checks.py`. They prove what each judge is shown
  and what its ruling does. They never show whether a real judge is right (decision map, top).
- Pure-logic tests: `test_clip_judge.py` (`clip_judge.judged`), `test_fix_check_verdicts.py`,
  `test_check_answers.py`.
- Wording pins: `test_frozen_rules.py` pins every judge word and the hash
  `clip_judge.wording_version`. A pin stops drift. It doesn't measure anything.
- Eval-harness tests: `test_ai_checks.py`, `test_calibrate_picture_check.py`,
  `test_round_results.py`, `test_round_numbers.py`, `test_rule_checks.py`. They test the counting,
  on fake verdicts.
- Real-model measurement: by hand, per round, outside CI.

## Concepts, ranked by impact on this build

### 1. Regression evals (eval-gated judge and model changes): **must apply now**

- **What it is:** you keep a frozen set of graded cases and run it again whenever the judge's
  words, inputs or model change. A change ships with its new numbers next to the old ones.
- **Why it matters here:**
  - Every ruling stores its wording hash and models (`clip_judge.py:wording_version`,
    `_sent_ruling_facts`). The pin test forces a new hash on any change
    (`test_frozen_rules.py:test_everything_the_clip_judge_is_told_is_the_version_its_rulings_name`).
    Nothing measures the new hash.
  - The clip judge's only graded numbers come from the old SIDES + MOTION judge (decision map,
    clip judge row: "when it checked sides and motion").
  - Since then the judge has changed three times:
    - PRODUCT replaced SIDES (#228).
    - A first "do the references show enough" question was added (`_asked_against_sent`, #290,
      item 11).
    - Scene clips are judged against new inputs (`check_words`, `SCENE_REFERENCES_FRAME`, #309).
  - So the judge running now (`3c870a5e5b`) has no graded number at all. The scene agent stops
    filming when this judge passes a clip (CONTEXT: Auto-fix).
  - The same happened with a model: the rewrites the fact check sends back moved to gpt-6.1-sol
    "with no comparison test" (`test_frozen_rules.py:test_each_job_that_was_on_gpt_5_6_sol_runs_on_gpt_6_1_sol`).
- **How to apply it:**
  - Keep one result file per judge version: `evals/judge_results/<wording>-<model>.json`, with
    caught, missed, wrong fails and not checked, scored on the golden set (concept 2).
  - Extend the pin test so a new hash fails CI until its result file exists. CI stays free. The
    paid run is done by hand, with Julian's OK and its cost stated first.
  - To price a re-run, sum `ModelCall.cost_usd` over round 6's `check_clip_*` calls (a free query).
  - Cheaper options:
    - When only the counting changes, replay the stored answers for $0, as ADR 0008 did.
    - Otherwise, re-run only the clips whose inputs the change touches.
- **One real post:** Hamel Husain, "Your AI Product Needs Evals". Rechat runs hundreds of
  tests in CI and tracks the results over time: https://hamel.dev/blog/posts/evals/

### 2. Golden dataset: **must apply now**

- **What it is:** one owned, versioned set of inputs with trusted labels. Anyone with a checkout
  of the repo can run against it.
- **Why it matters here:**
  - The clip judge's answer key lives outside the repo:
    - `evals/results.py` (docstring) scores clips "following evals/clip-judge-v2/score.py
      (project files)".
    - ADR 0008's analysis sits in `runs/gemini-judge-test/`, which is also outside the repo.
  - `evals/calibrate_picture_check.py:load_cases` reads 62 cases whose pictures are under
    `/mnt/project-files/`. None of the 62 exist in this checkout.
  - The 44 blind-graded video-eval clips sit in a Docker volume that the README calls "not a safe
    home" (`docs/video-eval/README.md`).
  - The set drifts away from what production makes:
    - On the clips it was proved on, the judge wrongly failed 2 of 29 good clips
      (`test_frozen_rules.py`, A22 comment).
    - On round 6's new clips it wrongly failed 9 (decision map).
    - A set that isn't refreshed each round makes the judge look better than it is.
- **How to apply it:**
  - Keep one manifest in the repo (`evals/golden/clips.json`) with, per clip:
    - its id,
    - its sent list, or its starting picture and photos,
    - Julian's grade and note,
    - its round and product.
  - Keep the media in one backed-up store, named by content hash and pointed to from the
    manifest.
  - Each graded round adds its clips under a new set version.
  - Keep a frozen held-out slice that is never used while writing judge words.
  - Split by product, so no scene has tries on both sides of the split.
- **One real post:** Langfuse docs, Datasets. Every item change makes a new dataset version, and
  each experiment pins a version. AdForge already sends traces to Langfuse:
  https://langfuse.com/docs/evaluation/experiments/datasets

### 3. Groundedness eval for the fact check: **must apply now**

- **What it is:** you measure a "does the source support this claim" checker on lines whose right
  answer you already know, including lines with errors planted on purpose.
- **Why it matters here:**
  - The fact check is the only guard against false claims (`jobs/work.py:_checked`,
    `_ask_fact_check`, `checks.py:FACT_CHECK_INSTRUCTIONS`). The decision map says: "Not measured."
  - The runs have already recorded misses that were never scored:
    - It passed "available for $4,999" on a page that only takes quotes
      (`docs/runs/first-run-findings/24-life-fitness-treadmill.md`).
    - It passed claims from a cross-sell block for another product
      (`docs/runs/second-run-findings/04-mack-weldon-sweatshirt.md`).
    - It passed the brand-wide "no artificial colors or flavors"
      (`docs/runs/second-run-findings/03-vital-proteins-collagen.md`).
  - The harm is the worst kind for a shop owner: a false claim in a paid ad.
- **How to apply it:**
  - Take 40 to 60 lines from saved rounds, each with its page text, and have Julian label each
    ok or wrong.
  - Add planted errors the way `evals/break_passes.py:broken` builds its broken copies: change a
    number, add a claim the page doesn't make, move in a cross-sell sentence, drop a qualifier.
    You know the right label because you made the error.
  - Score two errors separately: wrong lines it misses (false claims shipped) and good lines it
    flags (needless rewrites).
  - It's text only, on gpt-5.6-terra at $2 / $12 per M tokens: roughly $1 to $3 for 50 cases.
    Check that figure first against the `cost_usd` of past `fact_check` calls.
- **One real post:** InfoQ on DoorDash's support bot. It uses a binary hallucination metric and
  an LLM judge calibrated to human grades. Its test cases come from failures seen in use, and
  changes must pass before they deploy:
  https://www.infoq.com/news/2026/03/doordash-llm-chatbot-simulator/

### 4. Blind annotation (no anchoring on the judge): **must apply now**

- **What it is:** whoever writes the answer key doesn't see the judge's verdict first. A grader
  shown a model's answer drifts toward it, and the model then looks better than it is.
- **Why it matters here:**
  - The round grading page shows each clip "with the judge's verdict and what it saw wrong in
    view (never hidden)" (`evals/grading_page.py`, module docstring and `_verdict`). A test pins
    this: `tests/test_round_results.py:test_the_page_grades_the_first_and_the_kept_clip_with_the_judge_in_view`.
  - Re-grades only move one way. Where Julian's note says the judge was right, his pass becomes a
    fail (`regrades.json`, applied in `evals/results.py:load_run`). Nothing re-checks the clips
    the judge passed.
  - These are the numbers behind ADR 0008 and the decision map's clip-judge row.
  - The video eval did this right: random names, with the key kept apart (`video-eval/blind.py:main`).
- **How to apply it:**
  - Hide the judge's chip and the fault it found until Julian's tap is saved, then show them.
  - Re-check grades in both directions or not at all: review a random sample of the clips where
    Julian and the judge agreed, not only the disagreements where the judge was right.
- **One real post:** Berzak et al. (2016), "Anchoring and Agreement in Syntactic Annotations".
  Annotators who edited a tool's output showed "a clear anchoring effect", and the tool's
  measured performance came out too high: https://arxiv.org/abs/1605.04481

### 5. Over-optimizing against an imperfect judge (Goodhart's law): **must apply now**

- **What it is:** a loop that keeps trying until a judge says "pass" ends up selecting for the
  judge's blind spots. The more tries it takes, the more likely the final pass is a false pass.
- **Why it matters here:**
  - The scene agent films up to 6 clips per run and stops at the judge's first pass (CONTEXT:
    Auto-fix; the ruling comes from `clip_judge.py:judge_clip`).
  - The old auto-fix kept the try with the fewest failed answers
    (`jobs/auto_fix.py:clip_to_keep`): it picked the best of n by the judge's score.
  - Known limitation: "The fixing agent trusts the clip judge" (`docs/limitations.md`).
  - Round 6, with the fixing agent from before scene agents (decision map): 4 fixes ended on a
    clip Julian failed. Of the 7 that ended on a clip he passed, 4 started from a first clip that
    was already fine and that the judge had wrongly failed.
  - An illustration (not measured): if the judge misses a share q of bad clips, the chance that
    a bad clip slips through within k bad tries is 1 − (1 − q)^k. At q = 0.3 that is 30% after 1
    try, 66% after 3 and 88% after 6.
- **How to apply it:**
  - Measure the judge on the clips it actually stops on: how often a "pass after at least one
    fail" is right, kept apart from first clips. `evals/results.py:report` already has a
    "kept remakes" section. Report that line every round, with its interval (concept 6).
  - Note how many tries each passed fix took. How often a pass is right, by try number, shows
    the effect directly.
  - This is measurement only. Changing the loop is a design change and needs Julian's yes.
- **One real post:** Gao, Schulman and Hilton (OpenAI), "Scaling Laws for Reward Model
  Overoptimization". When they pushed a policy hard against a stand-in judge (reinforcement
  learning or best-of-n), the true score fell past a point, "in accordance with Goodhart's law":
  https://arxiv.org/abs/2210.10760

### 6. Confidence intervals on eval scores: **must apply now**

- **What it is:** a range of true rates that a count is consistent with. Small sets give wide
  ranges. To compare two judges on the same cases, look at the cases where they disagree (a
  paired comparison), not at two totals.
- **Why it matters here** (95% Wilson intervals, computed for this report):
  - ADR 0008: 29 of 43 matches gives 53% to 80%; 28 of 43 gives 50% to 78%. On only 8 clips
    could a single ask differ from the vote, too few to show any but a large gap. Dropping the
    vote still stands, because it rested on cost.
  - Start-picture judge: it caught 6 of 10 wrong pictures (31% to 83%) and failed 0 of 52 good
    ones (0% to 7%) (`evals/calibrate_picture_check.py`, docstring).
  - Video eval B-roll mark: 5 of 6 passed (44% to 97%). Those 6 clips are 3 scenes run twice,
    so they count for closer to 3 samples than 6 (clustering, `docs/video-eval/README.md`).
  - Rule evals: 10 of 18 rules have no failing case in their held-out half
    (`evals/rule_cases.json`), so "kept 5/5 passes" says nothing about catching fails.
    `tests/test_ai_checks.py` says each held-out half has "some of its fails"; the data doesn't.
  - `evals/results.py` prints counts only (its docstring: "Counts, not percentages"), and so
    does `evals/ai_checks.py:Score.line`.
- **How to apply it:**
  - Print a Wilson interval next to every count in `results.py:_judge_lines` and in
    `Score.line`. It's a pure function, so it can be unit-tested.
  - When comparing judge A with judge B on the same clips, report the clips where they disagree
    and which one was right on each. McNemar's test is that count.
  - Before a paid round, decide the smallest difference worth acting on, then work out how many
    clips you need to detect it (a power analysis, as in the post below).
  - Group clips by scene or product when you compute intervals (clustered errors).
- **One real post:** Anthropic, "A statistical approach to model evaluations". It recommends
  reporting standard errors, clustering them, comparing models by paired differences, and using
  power analysis:
  https://www.anthropic.com/research/statistical-approach-to-model-evals

### 7. Cost-sensitive decision threshold (operating point): **good to know**

- **What it is:** you set a checker's bar from what each kind of error costs, rather than "fail
  only when sure". This goes past taught concept 6 (precision vs recall) by putting a price on
  each kind of error.
- **Why it matters here:**
  - The bar lives in the judge's words: MOTION and PRODUCT fail "only when you are sure"
    (`clip_judge.py:MOTION_CHECK`, `PRODUCT_CHECK`). ADR 0008: "The judge's errors are its bar."
  - The vote, on round 6's 43 clips: 9 wrong fails and 5 missed fails (ADR 0008).
  - The two errors cost different things:
    - A wrong fail costs at least one more clip: $0.40 to $1.19 for 5 to 15 s at $0.0792/s
      (`gateway/catalog.py:PRICE_PER_SECOND_OF_VIDEO`), plus the judge's calls.
    - A missed fail shows a shop owner a broken clip.
  - "The clip judge being too strict" is parked (spec #303, Undecided 5). This is the tool for
    deciding it.
- **How to apply it:**
  - Julian puts a dollar figure on shipping one broken clip. Then, for each judge version, the
    expected cost per 43 clips is wrong fails × the cost of a wrong fail + misses × the cost of
    a miss.
  - A judge that only says pass or fail has no knob to turn. If it rated each fault minor, clear
    or severe, you could choose the level that fails a clip and try each level on the golden
    set. That changes the judge's words, so it's Julian's call.
- **One real post:** Google ML Crash Course, "Thresholds and the confusion matrix". A default
  threshold is "not a good idea if the cost of one type of wrong classification is greater than
  the other":
  https://developers.google.com/machine-learning/crash-course/classification/thresholding

### 8. Selective prediction (abstention): **good to know**

- **What it is:** a judge can say "can't tell" and hand the case to a person. You then measure
  two things: coverage (the share of cases it decides) and accuracy on the cases it decides.
- **Why it matters here:**
  - The clip judge already abstains. It rules "unsure" when the references never show the
    product or the safety filter refused some seconds, and "not_checked" when it got no answer
    (`clip_judge.py:judged`). `_say_the_judge_is_unsure` then asks the shop owner to watch the
    clip.
  - The round numbers don't count it. `evals/results.py:_judge_lines` counts pass, fail and
    not_checked, so an "unsure" ruling ends up under "Graded but never judged".
  - Each unsure clip hands the check to the shop owner, and nothing counts how often that
    happens. This gap in `_judge_lines` is small to fix.
- **How to apply it:**
  - Add "unsure" to `_judge_lines`. Report coverage (decided / graded), accuracy on the decided
    clips, and Julian's grades on the unsure ones (were they bad?).
  - A target such as "unsure on at most 1 clip in 10" can then be checked every round.
- **One real post:** "Trust or Escalate: LLM Judges with Provable Guarantees for Human
  Agreement". The judge decides when to trust itself; it reached over 80% agreement with humans
  while deciding almost 80% of cases: https://arxiv.org/abs/2407.18370

### 9. Intra-rater reliability (the human ceiling): **good to know**

- **What it is:** how often one grader gives the same item the same grade twice. Cohen's kappa
  is that agreement with chance agreement taken out. No judge can agree with Julian more than
  Julian agrees with himself.
- **Why it matters here:**
  - Julian is the only grader (AGENTS.md), and his bar moves as he grades. `regrades.json` exists
    for exactly that (`evals/results.py:load_run`).
  - The round page asks only "First clip: good?", with no written rule
    (`evals/grading_page.py:_scene_card`).
  - The video eval had written rules and its labels still slipped (`docs/video-eval/README.md`):
    - A B1 clip whose note says the whole chair spins, wheels included, was graded a motion pass.
    - Blank answers were read as passes.
  - ADR 0008 reports raw matches, which don't allow for how many clips pass anyway (the base
    rate).
- **How to apply it:**
  - Each round, mix 15 to 20 earlier clips, picked at random, back into the page, graded blind.
    Report how often Julian agrees with his earlier grade, and the kappa.
  - Write the pass rule for round grading once (the video eval's three B-roll rules are a start)
    and show it on the page.
- **One real post:** Shankar et al., "Who Validates the Validators?". They name "criteria drift":
  "users need criteria to grade outputs, but grading outputs helps users define criteria":
  https://arxiv.org/abs/2404.12272

### 10. Position bias in pairwise judges: **good to know**

- **What it is:** a judge comparing two items leans toward one of them because of where it sits.
  The fix is to ask twice with the order swapped and count a verdict only when both answers
  agree.
- **Why it matters here:**
  - The fix check is a pairwise judge, and it always sees the old clip's frames first and the new
    clip's second (`clip_judge.py:_frames_shown`, `OLD_FRAMES_LABEL`, `NEW_FRAMES_LABEL`).
  - Its question rides inside the MOTION ask (`MotionFixVerdict`). One answer settles it
    (`fix_check.py:settle_all`), and "not fixed" fails the try (CONTEXT: Fix check).
  - It was last measured on round 4's taps (`evals/results.py:Scene.fix_check_right`). Known
    limitation: "Nothing says a fix worked" (`docs/limitations.md`).
- **How to apply it:**
  - On the golden set's fix clips, ask the fix question once in each order and count how often
    the verdict flips. If it rarely flips, keep one order. If it often flips, ask both orders,
    but only where that was measured.
  - Then measure whether a separate fix-check call does better than one bundled into MOTION.
- **One real post:** Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena". It
  names position bias; its §3.4 calls the judge twice with the two answers swapped and counts a
  win only if both orders agree: https://arxiv.org/abs/2306.05685

### 11. Native video input vs frame sampling for video judges: **good to know**

- **What it is:** a video judge either watches the file itself (the model samples frames at a
  rate you can set) or gets still frames you cut. The frame rate and resolution decide which
  motion it can see.
- **Why it matters here:**
  - The clip judge gets stills cut by ffmpeg: 10 a second for PRODUCT and 5 for MOTION
    (`clip_judge.py:cut_pictures`, `_every_frame`).
  - When they don't fit in 50 pictures, frames are joined side by side (`_kept`, `MOST_PICTURES`),
    and each picture is then shrunk to 2048 px (`gateway/gateway.py:MAX_IMAGE_SIDE`). A 15 s clip
    at 10 a second beside 10 references gives 4 frames per picture.
  - The frame rates were tuned: the comment on `PRODUCT_FRAMES_PER_SECOND` says 5 stills missed
    every phone flip.
  - For the watcher, ADR 0009 found Gemini watching the whole video beat GPT on frames.
  - ADR 0008 cites a `gemini-judge-test` folder that isn't in the repo, so this checkout can't
    show whether a judge that watches the whole clip was tried.
- **How to apply it:**
  - Once the golden set exists, run one arm that watches the clip whole (at a set fps) against
    today's stills, on the same clips, and compare them paired (concept 6). It's a paid run:
    Julian's go, with the cost stated first.
- **One real post:** Google, Gemini API docs, "Video understanding". The default is 1 frame a
  second, "fast action sequences might lose detail", and `fps` can be set:
  https://aistudio.google.com/docs/video-understanding

## Also seen here

- **1. Stopping conditions:** `jobs/checks.py:MOST_REWRITES`: after 2 failed fact-check rewrites
  the owner is asked (`jobs/work.py:_about_line`).
- **2. Agent evals on a real model:** `evals/owner_notes/__main__.py` replays round 4 with its
  videos faked; `broll-test/should_ask.py` tests whether the planner asks or plans, on 8 cases × 3
  runs.
- **3. Calibrating an LLM judge against Julian's grades:** `evals/calibrate_ai_checks.py:main`
  (tune vs held out), `evals/calibrate_picture_check.py:report`, `evals/results.py:_judge_lines`.
- **5. Independent evidence for checks:** for scene clips, the real-product photos the judge
  compares against are the scene agent's own picks (`clip_judge.py:sent_references`, which reads
  `sent.picture_photos`).
- **6. Precision vs recall:** `clip_judge.py:MOTION_CHECK` says "Decide fail only when you are
  sure". Concept 7 takes it further.
- **9. App-side rate limiting:** the eval scripts cap concurrency with a thread pool
  (`evals/calibrate_ai_checks.py:main`, `AT_ONCE` = 8), not a token bucket.
