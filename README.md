# adforge

A chat app that turns a product page into a short video ad: a presenter talks, B-roll clips show the product at work, and the shop owner fixes any scene by writing a note.

## Where AI decides, and what checks it

Every model call goes through `backend/gateway/gateway.py` under a `purpose`; `backend/gateway/catalog.py` says which model each purpose uses. This map is documentation of what is checked today. It is not how evals are picked: those come from graded failures.

How a decision can be checked:

- **Test**: the suite in `backend/tests/`. Every test runs on a scripted fake model (`gateway/fake.py`), so tests prove the plumbing and code guards, never the quality of a real answer.
- **Eval**: a real model scored against Julian's grades, run by hand.
- **Judge**: another model checks the output inside the app.
- **Guard**: code refuses or caps a bad answer.

| Decision (purpose) | Where | If it decides badly | Checked by |
|---|---|---|---|
| Which page text is this product's (`copy_page_text`) | `jobs/work.py` `copy_page_text` | The ad states another product's facts | Guard: sentences not found on the page are dropped (`page_text.match_back`) |
| Is the page one product (`check_page`) | `work.py` `check_page` | A category page goes ahead, or a good page is refused | **Nothing** |
| Which photos are the product, which show a face (`pick_photos`, `note_face`) | `work.py` `pick_photos`, `note_face` | A look-alike's photos or a stranger's face in the ad | Guard: unknown photo numbers dropped; a failed face call counts as a face. `broll-test/face_notes.py` reads the picker's face note on saved runs |
| The script: scenes, lines, what B-roll shows, or a question to the owner (`plan_ad`) | `work.py` `plan`, `jobs/planning.py` | False claims, scenes that don't sell, needless questions | Eval: 208 rule cases (`evals/rule_cases.json`, `rule_checks.py`, `ai_checks.py`), `broll-test/should_ask.py` (ask or not, 8 cases). Judge: fact check. Guard: first scene talks, product named, photo numbers exist |
| Is each line true to the page (`fact_check`) | `work.py` `_ask_fact_check`, `jobs/checks.py` | False claims ship, or true lines get rewritten worse | **Nothing checks the checker.** Guard: after 2 rewrites the owner is asked |
| Rewrite, shorten, lengthen lines (`rewrite_line`, `shorten_line`, `lengthen_line`, `shorten_script`) | `work.py` | Facts lost, padding, a selling scene dropped | Judge: fact check re-runs. Guard: try limits, scene order kept, length re-measured |
| Presenter, voice, speech, word timings, music (`draw_person`, `design_voice`, `speak_line`, `transcribe_line`, `make_music`) | `work.py` | Odd presenter, bad delivery, cuts mid-word, wrong music | **Nothing** on quality. Guard: gender prompt prefix, "instrumental only" |
| Talking-scene start picture (`choose_starting_picture` + `make_starting_picture`) | `work.py` `make_starting_picture` | Presenter holds the wrong product or colour | **No judge.** Guard: only the job's colour photos can be chosen |
| B-roll picture and motion prompts (`choose_broll_picture`) | `work.py` `_make_broll_picture`, rules in `jobs/scenes.py` | Impossible objects, or a clip that does nothing that sells | Eval: the 208 rule cases. Judge: picture judge, then clip judge. Locked rules pinned in `tests/test_frozen_rules.py` |
| Shared "before" picture (`plan_before_picture`, `make_before_picture`) | `work.py` `_before_picture` | Every B-roll scene inherits a wrong setting | **Nothing** |
| Picture judge: is the start picture possible (`check_starting_picture`) | `work.py` `judge_starting_picture`, `jobs/picture_check.py` | A paid clip of an impossible object, or needless redraws | Eval: 62 graded pictures (`evals/calibrate_picture_check.py`): 6 of 10 wrong caught (toilets never), 0 of 52 good failed. Guard: 2 redraws, then stop |
| Video clips (HeyGen talking, Boreal B-roll) | `work.py` `_ask_for_clip` | Bad lips, impossible motion, wrong action | Judge: clip judge on B-roll only. Eval: `video-eval/blind.py` (human grading) |
| Clip judge: sides and motion (`check_clip_sides`, `check_clip_motion`) | `jobs/clip_judge.py` | A bad clip ships, or a good one is remade at the owner's cost | Eval outside the repo (110 graded clips, `/mnt/project-files/evals/clip-judge-v2`). Wording pinned in `test_frozen_rules.py` |
| Remake prompt after a judge fail (`remake_clip_prompt`) | `work.py` `_remake_clip`, `jobs/auto_fix.py` | The remake repeats the fault | Judge: clip judge re-checks. Guard: 2 remakes; which clip is kept is code |
| Clip from the owner's note (`rewrite_clip_prompt`) | `work.py` `_rewrite_motion_prompt` | The clip ignores the note | Judge: clip judge asks if the owner's change was made |
| Fix check on a new start picture (`check_fix`) | `work.py` `check_fix`, `jobs/fix_check.py` | Owner told "fixed" when it isn't | **No eval.** Guard: 2 of 3 answers must agree |
| Producer agent: next tool, how to read a note into a fix (`produce`, `restate_fix`, `change_line`) | `agents/producer.py` | Wrong scene or step redone, money spent, complaint not fixed | Eval: `tests/eval_producer_fixes.py` (11 cases, opt-in, picks the step only). Guard: no paid redo before the owner answers the restatement; line must match the quote |

Biggest blind spots today: the fact check has no eval; talking-scene pictures and the shared before picture have no judge; the clip judge's eval lives outside the repo; and whether the owner's answer to a restated fix means yes is the producer model's call: code only checks that they answered (`agents/producer.py` `_yes_to`).
