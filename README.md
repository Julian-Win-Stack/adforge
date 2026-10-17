# adforge

A chat app that turns a product page into a short video ad: a presenter talks, B-roll clips show the product at work, and the shop owner fixes any scene by writing a note.

## Where AI decides, and what checks it

Every model call the app makes goes through `backend/gateway/gateway.py` under a `purpose`; `backend/gateway/catalog.py` says which model each purpose uses. This map documents what is checked today. It is not how evals are picked: those come from graded failures. Paths below are under `backend/`. Counts are as of 2026-10-15 (main 59cdca5).

How a decision can be checked:

- **Test**: the suite in `tests/`. Every test there runs on a scripted fake model (`gateway/fake.py`), so tests prove the wiring and code guards, never the quality of a real answer.
- **Eval**: a real model's answers scored against Julian's (the project owner's) grades, run by hand.
- **Judge**: another model checks the output inside the app.
- **Guard**: code refuses or caps a bad answer. **Prompt only** means the rule is just text in the prompt.

Harm if a decision is quietly wrong: **broken ad** (the owner sees a bad ad), **paid remake** (the owner pays for a useless remake), **unseen** (nothing visible).

| Decision (purpose) | Runs per ad | Harm if wrong | Checked today |
|---|---|---|---|
| Which page text is this product's (`copy_page_text`, `jobs/work.py`) | 1 | Broken ad: another product's facts | Guard: drops sentences not on the page (`page_text.match_back`), not another product's text on the same page. One-off eval: `docs/scraping-test/copy-test` (repo root) |
| Is the page one product (`check_page`) | 1 | Broken ad, or a good page refused | **Nothing** |
| Which photos are the product, which show a face (`pick_photos`, `note_face`) | 1, plus 1 per photo the picker never saw | Broken ad: a look-alike or a stranger's face | Guard: unknown photo numbers dropped; a failed face call counts as a face. One-off eval: `docs/scraping-test/ref-photo-test` (repo root). `broll-test/face_notes.py` reruns the picker for a person to check (no score). `note_face` unchecked |
| The script: scenes, lines, what B-roll shows, or a question (`plan_ad`, `jobs/planning.py`) | 1, again after each answered question | Broken ad: false claims, scenes that don't sell | Eval: `broll-test/should_ask.py` (asks when it should, 8 cases). Judge: fact check. Guard: first scene talks, product named, photo numbers exist. Rule judges (below) grade saved runs |
| Is each line true to the page (`fact_check`, `jobs/checks.py`) | 1 per check round, again after each rewrite or shortening | Broken ad: false claims ship, or true lines get worse | **Nothing checks the checker.** Guard: after 2 failed rewrites the owner is asked; in production a shortened B-roll line that fails is said to camera |
| Rewrite, shorten, lengthen lines (`rewrite_line`, `shorten_line`, `lengthen_line`, `shorten_script`) | 0 to 3 per scene | Broken ad: facts lost, padding, a selling scene dropped | Judge: fact check re-runs. Guard: try limits; a B-roll line still too long after 3 shortenings becomes a talking scene; scene order kept; length re-measured |
| Presenter, voice, speed, speech, word timings, music (`draw_person`, `design_voice`, `measure_voice`, `speak_line`, `transcribe_line`, `make_music`) | 1 each; speech and timings 1 per scene | Broken ad: odd presenter, bad delivery, cuts mid-word, wrong music | **Nothing** on quality. Prompt only: gender first, "instrumental only" |
| Talking-scene starting picture (`choose_starting_picture`, `make_starting_picture`) | 1 per talking scene | Broken ad: presenter holds the wrong product or colour | **No judge** on the first draw; after an owner's picture fix, the fix check. Guard: only the job's colour photos can be chosen |
| B-roll picture and motion prompts (`choose_broll_picture`, `jobs/scenes.py`) | 1 per B-roll scene, up to 2 redos | Paid remake, or broken ad: impossible objects, a clip that sells nothing | Judge: picture judge, then clip judge. Locked rules pinned in `tests/test_frozen_rules.py`. Rule judges (below) grade saved runs |
| Shared before-picture (`plan_before_picture`, `make_before_picture`) | 1 | Broken ad: every B-roll scene inherits a wrong setting | **No judge.** Wording pinned in `tests/test_frozen_rules.py`; A19 rule cases (do the scenes match) |
| Picture judge: is the starting picture possible (`check_starting_picture`, `jobs/picture_check.py`) | 3 asks per B-roll picture drawn | Paid remake: a clip of an impossible object, or needless redraws | Eval: `evals/calibrate_picture_check.py` on 62 in-repo cases: 6 of 10 wrong caught (toilets never); none failed of 20 graded good and 32 ungraded. Guard: 2 redraws, then stop |
| Video clips (`make_talking_clip` HeyGen, `make_broll_clip` Boreal) | 1 per scene | Broken ad: bad lips, impossible motion, wrong action | Judge: clip judge, B-roll only. Eval: `video-eval/blind.py` (human grading). Guard: a B-roll clip is sent its drawn starting picture as Image 1, told to start like it, never a shop photo (reference mode; rule check A7) |
| Clip judge: sides and motion (`check_clip_sides`, `check_clip_motion`, `jobs/clip_judge.py`) | 2 checks x 3 asks per B-roll clip | Broken ad if it misses; paid remake if it fails a good clip | Eval outside the repo (`/mnt/project-files/evals/clip-judge-v2`), only on clips the wording was tuned on: SIDES on 110, MOTION on 45 (2 of 29 good wrongly failed, 6 of 12 bad caught). Never-seen clips are first scored in round 5. Wording pinned in `tests/test_frozen_rules.py` |
| Auto-fix prompt after a judge fail (`remake_clip_prompt`, `jobs/auto_fix.py`) | 0 to 2 per B-roll clip | Paid remake that repeats the fault | Judge: clip judge re-checks. Guard: 2 remakes; code keeps the clip with the fewest failed answers, newest on a tie (`clip_to_keep`) |
| Clip for a fix, from the owner's note (`rewrite_clip_prompt`) | 1 per clip fix | Paid remake that ignores the note | Judge: clip judge asks if the owner's change was made (no eval of that question) |
| Fix check on a new starting picture (`check_fix`, `jobs/fix_check.py`) | 3 asks per picture drawn for a fix | Paid remake: a picture that ignores the fix passes and its clip is paid for | **No eval.** Guard: 2 of 3 answers decide; a fail is drawn again, told why, up to 2 times before the owner is asked; the app never tells the owner a fix worked (`docs/limitations.md`) |
| Producer agent: next tool while the ad is first made; answers what isn't a note after (`produce`, `agents/producer.py`) | Every owner message before the first clip; after it, those that aren't a note | Broken ad: a step skipped or made wrong; a wrong answer about a fix | **No eval.** Guard: tools refuse out of order; once a scene has a clip its making tools refuse, and the producer has no fix tool: it reads the system notes code writes (`agents/routing.py`) |
| Decider agent: sorts each owner message (note, yes, no, go ahead, other), looks, plans each scene's fix (`decide_fixes`, `agents/decider.py`) | Every owner message once the ad has a clip | Paid remake: wrong scene or step, a change the owner never asked for, a yes read wrong | Eval: `evals/decider` (32 replayed notes, 55 rule cases), Julian's grades pending. Guard: no decision on a scene it hasn't looked at; code adds rule can'ts and estimates, shows the plan, and runs nothing before a yes (a tap, or a typed yes to a plan shown before it) (`agents/run_plan.py`) |

The rule judges: `evals/ai_checks.py` is scored against 208 graded cases in `evals/rule_cases.json` (20 plan-level, the rest B-roll scenes), and `evals/run_rule_checks.py` runs free code checks on a saved run. Re-running the cases tests the judges, not a new planner or prompt.

Biggest blind spots today: the fact check has no eval; talking-scene starting pictures and the before-picture have no judge; the clip judge has never been scored on clips it wasn't tuned on; and whether a typed answer to a fix plan means yes is the decider model's call: code reads a tapped yes itself, and a typed one only for a plan shown before it (`agents/routing.py`).
