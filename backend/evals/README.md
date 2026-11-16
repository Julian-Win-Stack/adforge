# Evals

Evals answer two things: does a check in the app agree with Julian's grades, and what does the agent still fail. This folder is their one home.

## Rules (Julian, Nov 5, issue #384)

Evals are built so Julian can keep extending the project and make the agent better, not to hand anyone a number. So every eval:

- **Saves every grade** in one format with its inputs, so the labeled set grows every round.
- **Lives in the repo** and runs with one command, not as a one-off thread script.
- **Tests what the owner sees**, never prompt rules, so it survives the next redesign.
- **Tests the failure, not the rule, at the earliest step where it shows.** A case asks "did the failure that rule A prevents come back?", never "did the AI follow rule A?". A made-up claim is tested on the script; a side flip only shows in the clip, so it's tested there.
- **Comes in two kinds:** regression (what works keeps working, run on every change) and capability (hard cases the agent fails today: the to-do list).

## Cases

One file per question: `cases/<question>.jsonl`, one case per line. A question is one yes/no a check answers, for example `fact_check` ("does the plan claim something the product page doesn't back?"). A case is one thing Julian graded:

```json
{
  "id": "r9-mudbuster-clip-3936",
  "kind": "capability",
  "half": "score",
  "inputs": {
    "handoff": {"...": "what the check was given, as the app sent it (the ModelCall handoff)"},
    "files": ["project-files:runs/round-9/<path to the clip>"]
  },
  "label": "fail",
  "reason": "<Julian's exact words>",
  "source": {"round": "9", "ad": "<ad folder>", "graded_at": "<UTC time>", "from": "<his grade file>"},
  "verdicts": [
    {"verdict": "pass", "reason": "<the check's own reason>", "judge": "<system prompt hash or ModelCall id>", "model": "<model>", "at": "<UTC time>"}
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | Unique in its file. Suggested shape `r<round>-<ad>-<what>-<pk>`. |
| `kind` | `capability` (the agent's output failed) or `regression` (it passed, must keep passing). Set from his label when added; a capability case moves to regression by hand once a re-run of the agent passes. |
| `half` | `tune` or `score`, fixed by the id: `evals.cases.half_for(id)`. Never picked by hand. Tune a check's prompt on `tune` only; score it on `score`. |
| `inputs.handoff` | What the check was given, as the app sent it. |
| `inputs.files` | Pictures and clips stay out of git (big, and the repo is public). Each path starts with where it lives: `project-files:`, `azure:` (the box's media) or `mac:` (Julian's archive). |
| `label` | `pass` or `fail`: Julian's only, never Claude's guess. One exception: a **planted** case (`source.from` is `"planted"`), a mistake written into a real output on purpose to test that a check catches it, is labelled `fail` by how it was made, and its `reason` says what was planted and why the inputs don't back it. When Julian rules on a planted case, his label and words replace those and `source.from` names his grade file. |
| `reason` | His exact words. Empty is allowed for a pass only. |
| `source` | `round`, `ad`, `graded_at`, `from` (his grade file). |
| `verdicts` | What the check said (`pass`, `fail` or `unsure`), newest last. Today's checks save their inputs and answer for every call in `gateway.ModelCall` (`handoff`, `images`, `output`, `system_prompt`), so verdicts can be filled from past runs without paying again. |

**Adding a case:** append one line to the question's file, with `half` set to `half_for(id)`, then run the command below. It refuses the whole file if any case is malformed (missing or unknown field, label not pass/fail, a fail without his words, wrong half, bad file prefix, repeated id, not UTF-8) and names the file and line.

## The command: 4 boxes

From `backend/`, free (reads the case files only, calls no model):

```
uv run python -m evals.boxes [<question> ...] [--judge <id>]
```

No question means every file in `cases/`. Each case is counted by its newest verdict, or the newest from `--judge`. Per question, per half (tune, score):

| Box | Means |
|---|---|
| both pass | Check and Julian agree it's fine. |
| both fail | Check caught what Julian failed. |
| judge fails his pass (false alarm) | A needless redo: money. |
| judge passes his fail (miss) | A bad result reaches the owner. |
| judge unsure | Counted on its own, never folded into pass or fail. |
| no verdict yet | The check has no verdict on this case (or none from `--judge`). |

Then every false alarm, miss and unsure case: id, his label and words, the check's verdict and reason. Then how many cases are capability and regression. Counts, not percentages.

## Questions

| id | The question | Earliest step where the failure shows | App check that answers it today |
|---|---|---|---|
| `fact_check` | Does a scene's line, or what it shows, claim something the product page, the shop owner or the product photos don't back? | The script, before any picture or clip | `jobs/work.py:_ask_fact_check` (prompt `jobs/checks.py:FACT_CHECK_INSTRUCTIONS`) |
| `clip_product` | Is the product in the B-roll clip the real one (as in the reference photos), and does it behave like the real one? | The clip | The clip judge's product check (`PRODUCT_CHECK`, call purpose `check_clip_product`): `jobs/clip_judge.py:judge_clip` |
| `presenter_real_person` | Does the presenter feel like a real person (natural voice with expression, natural face)? | Finished ad | None |
| `hook_stops_scroll` | By 2 s can you name the product type and the problem or payoff, and does something move early? | Finished ad | None |
| `emotional_after` | Does at least one shot show how life feels after using it? | Finished ad | None |
| `broll_real_use` | Does this B-roll scene show the product really used for what she says, and look good? | Clip | None |
| `realistic_grip` | Does she hold the product in a realistic way in this scene? | Clip | None |
| `clip_real_product` | Is the product in this clip the real one: same shape, parts, colours and printed words as the page photos? | Clip | None (the clip judge's product check is the app's own) |
| `clip_possible` | Does nothing in this clip bend, float, pass through something, appear from nowhere or move in a way that can't happen? | Clip | None |
| `clip_substance_real` | Does the foam, cream, liquid or powder look and behave like the real product? | Clip | None |
| `clip_camera_natural` | Does the camera move naturally (in and stays in, never in and back out)? | Clip | None |
| `picture_looks_natural` | Do the presenter and the room look like one real photo, not a person pasted onto a background? | Starting picture | None |
| `plan_real_use` | Is the product used the way the page shows it is used? | Plan | None |
| `consistent_across_scenes` | Is the product, and what it's used with, the same in every scene? | Finished ad | None |
| `viewer_knows_product` | Can a viewer tell what kind of product this is, and is it sold as what it is? | Plan | None |

Some faults no model can judge yet: a person pasted onto the background (`picture_looks_natural`), hair passing through a brush, the wrong hand for the side of the body, and the presenter. Julian checks these by eye, and the eval judge leaves them out: [human-only-checks.md](human-only-checks.md).

### fact_check

A case is one scene of a script. `inputs.handoff` is the whole fact check handoff as the app sent it (a `jobs.checks.FactCheckHandoff`), `inputs.scene` is the scene the case is about, and `inputs.files` are the product photos the app showed, in their order. Today every case is planted: one scene of a real script changed into a made-up claim, the rest of the script left as the planner wrote it. So until Julian grades real lines the check failed, "both pass" and "false alarm" stay empty, and a check that failed everything would look perfect. The command therefore also prints how many of the unchanged scenes in the same scripts it failed: if that number jumps after a prompt change, the check has grown too strict. A planted case's `kind` is `regression` when the check caught it on its first run and `capability` when it missed.

The photo labels are approximate: the app labels a photo by its position on the page ("Photo 4", plus where in the video a still was cut), and the saved inputs don't keep those labels, so the command numbers the photos 1, 2, 3 in order.

Re-ask today's check on every case, appending its verdict (text runs on Azure, which Julian pays for, so keep runs reasonable; reads photos from `PROJECT_FILES`, default `/mnt/project-files`):

```
uv run python -m evals.fact_check [--ids <id> ...]
uv run python -m evals.boxes fact_check
```

Scenes of one script are checked in one call, and photos are shown only when a scene has a "shows", as the app does. Each verdict's `judge` is a hash of the prompt, so `--judge` counts one prompt's verdicts apart from another's.

### clip_product

A case is one B-roll clip. `clip_product` cases come from rounds already run (9, 11, 12), written by `python -m evals.clip_product` (how to run it is in its docstring). A clip the judge passed in an ad Julian watched, that he said nothing about, is a pass by his rule "no feedback = pass"; clips he said anything about (a tap, words, or a note naming the scene) and clips the judge failed or was unsure about need his answer first. His labels and the page he gave them on: `project-files:runs/clip-product-eval/`.

### Past grades, rounds 9-15 (clip_real_product, clip_possible, clip_substance_real, clip_camera_natural, picture_looks_natural, plan_real_use, consistent_across_scenes, viewer_knows_product)

Filed from Julian's grades on rounds 9 and 11-15 (spec #598, step 3): his clip taps and words, and his whole-ad notes, matched to the clip, scene or ad they name on each round's grading page. A fail's `reason` is his words verbatim, copied from the note it came from, so one note can be the reason for more than one case. Passes are his own clip passes, plus, by his rule "no feedback = pass", every clip in a finished ad he graded whose scene nothing he said touches (its `reason` says so). Scenes he said anything about that fits no question, and tap-only fails with no words, are left out. `clip_substance_real` passes are only scenes that show a foam, cream, liquid or powder. Soft questions (`clip_camera_natural`, `picture_looks_natural`, `viewer_knows_product`) hold only his complaints so far: passes need his labels. Clips and pictures are on the Azure box (`azure:media/`). The script that wrote them, with the table of which note went to which question: `project-files:runs/past-grades-into-cases/build.py`.

### Success goals (presenter_real_person, hook_stops_scroll, emotional_after, broll_real_use, realistic_grip)

These five come from Julian's success goals (quality-eval step 2, Nov 7 2026). He ticked yes or no for every finished ad from rounds 11 and 12 and for 2 Creatify Ad Agent ads he liked, on a labeling page with no old grades or judge verdicts shown. A tick has no words of its own, so a fail's `reason` says which tick he gave, plus his note when he wrote one. Scenes where nobody holds the product have no grip case. Videos stay out of git: round ads under `azure:media/`, Creatify ads under `project-files:runs/creatify-agent-test/`.

The labels are lopsided: every round 11 and 12 ad fails the three whole-ad questions, only the 2 Creatify ads pass them, and grip has 1 fail in 80. Add more good ads before tuning a check on those.

## Code tests

Two checks done by counting over every finished ad, with no model and no grade of Julian's (his OK, Nov 8), so a change that quietly drops something shows up without him watching every ad. Run them where the ads were made (the Azure box for a test round), free:

```
uv run python -m evals.code_tests <chat session id> ...
uv run python -m evals.code_tests --ads <ads.json>
```

- **Demo floor:** at least one demo scene: a B-roll scene, or a talking scene in the style "talking while doing it". A floor only, no ratio.
- **The ending:** the end card's product photo plays at least 1.5 s at the end of the video, and the brand or the product name is heard at least once (each clip's transcript).

It reads each ad as shipped (its newest finished version, the scenes it plays, the video measured with ffprobe), prints one row per ad with why it passed or failed, and exits 1 when any ad fails. Nothing is saved as a case: there is no label to agree with.

## Older files in this folder

The old rule evals (Oct 9-11) were deleted on Julian's call (Nov 5); git history keeps them. What is left from before this home:

- `tool_calls.py`, `round_export.py`, `agent_page.py`: live, used by the grading-page skill.
- `grading_page.py`, `grading_page.html`, `grading_page.js`, `results.py`: the old round 7 grading page. Kept only because `agent_page.py` imports `grading_page`, which imports `results`. `results.py` can't read rounds 8 and later; don't build on either.
- `picture_check_cases.json`: 62 starting pictures from rounds 2-3 (old pipeline). 30 are Julian's grades (10 wrong, 20 right); the other 32 are only "no complaint", not his grade. 6 of the wrong ones and the 20 right ones fit today's picture check question 1 (same product); the 4 toilet ones fit question 2 (physics) at best, his call.
- `owner_notes/`: borderline; its tests pass, it replays round 4's old-pipeline ads.

## Eval judge shared contracts (#644, #675)

The eval judge is a third answerer, beside code and Julian. It runs outside the app,
never remakes an output, and fails every real flaw its questions cover. This ticket
registers its inputs and lasting formats; it does not call a model or change grading.

`evals.regression.questions.judge_questions(stage=None)` lists the fixed catalogue;
`all_questions(cases_dir=...)` includes it with `answered_by="judge"`. Each `Question`
has `id`, `stage`, `words`, `unit`, `frames_per_second` and `page_photos`:

| id | stage | question | unit | inputs |
|---|---|---|---|---|
| P1 | pictures | Real product | pictures | Starting picture and page photos |
| P4 | pictures | Staged like real life | pictures | Starting picture and page photos |
| C5 | clips | Real product, same size the whole clip | clips | 10 frames/s and page photos |
| C6 | clips | Nothing impossible | clips | 10 frames/s and page photos |
| C7 | clips | Used the right way, on the right target | clips | 10 frames/s and page photos |
| C8 | clips | Cream or liquid behaves like the real thing | clips | 10 frames/s and page photos |
| C9 | clips | Camera moves naturally | clips | 10 frames/s and page photos |
| C10 | clips | Staged like real life | clips | 10 frames/s and page photos |
| C13 | clips | The holding hand is a real hand | clips | 5 frames/s, no page photos |
| C14 | clips | The action really works and its result stays | clips | 5 frames/s, no page photos |
| A11 | ad | Same product in every scene | ads | One still per scene and page photos |

The scene's spoken line accompanies pictures and clips. Product-comparison questions
also receive the product name, page text and photos' labels; C13/C14 retain their
separately tested inputs. P2, P3 and A12 are absent. The 11 question files and shared
`_judge.md` live in `evals/judge/questions/`, copied byte for byte from
`eval-judge-prototype` at `026971e23cc61d4a62b48f9ebe7c813b9aea4d49` (PR #671).
`evals.judge.prompts.question_text(id)` and `shared_instructions()` read them afresh;
changing wording requires only a text edit. They contain the approved strict-path
rules and allowances, not the app clip judge's lenient policy.

### Run answers

`Store.read_judge_answers(run)` / `write_judge_answers(run, mapping)` read/write
`runs/<run>/judge-answers.json`, atomically. The nesting is **scenario → question id
→ item → answer**, where items are `s1`, `s2`, … for pictures/clips and `ad` for A11:

```json
{
  "ridge-wallet": {
    "P4": {
      "s1": {
        "judge": "P4:prompt-hash",
        "verdict": "fail",
        "asks": [
          {
            "verdict": "fail",
            "sees": "The bill is in the card slot",
            "when": "the picture",
            "on_what": "product",
            "model": "model-name",
            "at": "2026-11-16T00:00:00Z",
            "input_tokens": 100,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 20,
            "cost_usd": 0.01
          }
        ]
      }
    }
  }
}
```

The counted `verdict` is `pass`, `fail` or `doesnt_apply`. A failed call has `error`
(text) and no verdict, or `verdict: null`. A partial re-ask also has no counted
verdict until its remaining asks finish: every successful ask can be saved before
starting the next. `asks` retains all asks in order; the asking ticket counts a
fail only at 2 of 3. `on_what` is one of `product`, `hand`, `target`, `presenter`,
`background`, `nothing` (C13 uses `hand`). Every ask keeps its own model, time,
token counts and cost. The run's judge cost is the sum of its saved asks' costs.

Optional forms on an ask (or `null` when absent):

- C13 `rows`: one object per stretch of the same grip, with text `seconds`,
  `wrist_side`, `knuckles_side`, `fingertips_point`, `thumb`, and boolean `possible`.
- P1/C5 `photo_sides`: objects with text `photo`, `side`, `parts`.
- P1/C5 `shown_sides`: objects with text `side`, `when`, `parts`, `same_side_photo`,
  `differences`.

`evals.judge.records.JudgeAsk`, `JudgeAnswer`, `HandRow`, `PhotoSide`, `ShownSide`
and `validate_answers` define/validate these JSON shapes. Unknown fields/questions,
wrong item names/types, invalid verdicts and malformed forms raise; a missing
answer file returns `{}`. No error is silently turned into a pass.

### Julian's three taps

Taps live beside the existing grades in `runs/<run>/grades.json`. Their doc ids
append the question to the existing scheme:

- `ridge-wallet__s1__picture__P1`
- `ridge-wallet__s1__clip__C5`
- `ridge-wallet__ad__A11`

A tapped document is `{"tap":"real_flaw_ship","note":"small but real","at":"..."}`.
The exact stored tap values and displayed words are:

| stored value | displayed words | case label |
|---|---|---|
| `not_a_flaw` | not a flaw | pass |
| `real_flaw_ship` | real flaw, fine to ship | fail |
| `real_flaw_dont_ship` | real flaw, don't ship | fail |

An untapped fail has no grade document. `note` is text (empty is allowed), `at` is
text. `evals.judge.records.tap_doc_id(scenario, stage, item, question)` builds an id;
`parse_tap_doc_id(id)` returns `(scenario, stage, item, question)` or `None` for an
unrelated id. `TAP_WORDS` maps values to wording. `Store.read_judge_taps(run)` selects
and validates these documents; `write_judge_taps(run, mapping)` merges them into
existing grades without replacing Julian's other grades. Missing files read `{}`;
malformed judge documents raise. Existing import/filing ignore the new doc ids
until their own tickets opt into them.

### Judge cases and asks

Each judge question has its own existing-format file `cases/P1.jsonl`, …,
`cases/A11.jsonl`. `inputs.handoff` follows
`evals.judge.records.JudgeCaseHandoff`: `stage`, `item`, `line`, `shows`,
`product_name`, `product_text`, `page_photo_labels` (text list), and `question`
(the catalogue's plain description). `inputs.files` lists the item's media first,
then the page photos in label order; each path retains its location prefix.
The handoff never includes app verdicts, reasons or prompts.

`source` retains the existing `round`, `ad`, `graded_at`, `from` fields; a tap adds
`source.tap` with its stored value. Both real-flaw taps mean label `fail`, whether
or not the ad ships. A complaint filed under that question also means fail and
keeps Julian's exact words. `not_a_flaw`, or no complaint on an item Julian was
shown, means pass. Items hidden because only trusted judge questions passed them
have no human label and create no case. A tap-only fail whose question is unknown
belongs in `judge_picture_item.jsonl` or `judge_clip_item.jsonl`
(`records.ITEM_QUESTIONS`), with the same handoff except **no `question`**: it is
not registered as a per-question case and counts only at item level. Its reason
states that he failed it without a written note; no fault category is guessed.

A case verdict may add `asks`, a list of objects with exactly `verdict` (one of the
case verdict values) and `reason` (text). For example:

```json
{"verdict":"fail","reason":"The strap crosses the leg","judge":"C6:hash","model":"model-name","at":"...","asks":[{"verdict":"fail","reason":"The strap crosses the leg"},{"verdict":"pass","reason":"It stays clear"},{"verdict":"fail","reason":"It passes through"}]}
```

Rich observations/forms/cost remain in the run answer; case asks retain each
verdict and reason. `evals.cases.Verdict.asks` is `None` for an older verdict (or JSON `null`) or a
tuple of `Ask(verdict, reason)` when supplied. Malformed asks refuse the whole
case file with its filename and line. Case verdicts now also accept
`doesnt_apply`; unknown values remain errors. `evals.boxes` counts doesn't-apply
on its own line, never as pass/fail or a disagreement.

### Trust seam

`evals.judge.trust.trusted_questions(*, cases_dir=FOLDER, judge=None)` is the one
answer for the grading page and report. It currently returns `frozenset()`;
#677 supplies scoring at that seam. No question has earned trust yet. These new
formats alone do not change `answer`, `grading-page`, `file-grades`, `report` or
`promote` on a run with no judge answers.

`evals.judge.inputs.JudgeInput` is the shared media adapter: `stage`, `item`,
`files` (the item's media paths), `line`, `shows`, `product_name`, `product_text`,
`page_photos`, and `page_photo_labels`. `inputs_for_run_item(store, run, scenario,
graded_item)` reads these fields from the saved export; it never forwards the app's
checks or model handoffs. The labels always have one entry per original page photo,
including an empty label when no description was saved. `case_handoff()` makes the
case's allowed context; `case_files()` gives item media first, then page photos, so
the label count also tells a case reader where the page photos begin. At the ad stage,
item media are the kept scene clips in scene order, from which A11 takes one still
each; saved cases can hold pre-cut stills instead.

### Ask the regression eval judge (#676)

Run only when Julian asks, from `backend/`, on a finished saved run:

```
uv run python -m evals.regression --store /path/to/eval-store judge regression-4
uv run python -m evals.regression --store /path/to/eval-store judge baseline-2 --questions P1 P4 A11
```

This first command version covers starting pictures (P1, P4) and finished-ad product
consistency (A11). It never runs after a regression run on its own and never remakes
anything. It uses the configured Azure text/vision account and the app clip judge's
model; a missing Azure account is an error, never an OpenAI fallback. A few items run
at once, but each item's questions run one after another. A plan-only run has no media
to judge.

Each picture call sees its starting picture, product name and page text, the scene's
spoken line and what it shows, and every kept page photo labelled with its saved note.
Photos above ten are joined side by side into at most ten images. A11 gets one still
from each kept scene clip, in scene order, and the page photos. The question follows
the images; the app's prompts, verdicts and reasons are never forwarded.

A pass or `doesnt_apply` is asked once. A fail is asked twice more and counts as fail
only with at least two fails among its three asks. P1 fills the product form first.
Every answer is saved before the next ask. Re-running resumes the same question version,
including the missing asks of a partially answered fail. Changing shared instructions,
question text, answer/handoff schema, model or input construction policy gives a new
judge id. The id is shared across items: it hashes how inputs are constructed, not
individual item contents, so `--judge` scores one version across cases.

Tokens, cache counts, time and cost are saved with every ask. A billed refusal or broken
reply is kept in the answer's optional `failed_asks` list (error, model, at, tokens and
cost, with no verdict). `history` holds previous question versions so their spend is
kept when wording changes. `evals.judge.records.answer_cost(answer)` includes successful
asks, failed asks and history. Unreadable media and failed/refused calls post the
`EVAL_JUDGE_UNAVAILABLE` developer notice, keep the item unjudged, name it in output and
exit 1. An explicit resumed command can try an unjudged item again.

The command prints its incremental cost and the total judge cost for the saved run.
The #644 estimate for a baseline-sized run (15 ads, 57 clips, 115 pictures) was about
$22 with cache hits or $50 without, plus about $11 for hand/action questions and $1–2
for re-asking fails; pictures are about $0.05 cached / $0.11 uncached each. This version
runs only the picture and finished-ad portion. Cached input is costed at the spec's
assumed 90% discount, which has not yet been checked against Azure's price page.
Paid runs need Julian's OK; all code tests fake the model or its HTTP service.

The reusable seam is `evals.judge.ask.ask_question(question, JudgeInput,
previous=JudgeAnswer|None, save=callback)`. `show(inputs, question)` prepares reusable
images; `judge_id(question)` identifies the prompt/policy version. The run command and
saved-case command use these same inputs, answer forms, re-asks, resume and cost records.
