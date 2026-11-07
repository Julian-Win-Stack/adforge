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
| `label` | `pass` or `fail`: Julian's only, never Claude's guess. |
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
| `clip_product` | Is the product in the B-roll clip the real one (as in the reference photos), and does it behave like the real one? | The clip | The clip judge's product check: `jobs/clip_judge.py:check_clip_product` |

`clip_product` cases come from rounds already run (9, 11, 12), written by `python -m evals.clip_product` (how to run it is in its docstring). A clip the judge passed in an ad Julian watched is a pass by his rule "no feedback = pass"; clips the judge failed or was unsure about, and his own clip fails, need his answer first. His labels and the page he gave them on: `project-files:runs/clip-product-eval/`.

## Older files in this folder

The old rule evals (Oct 9-11) were deleted on Julian's call (Nov 5); git history keeps them. What is left from before this home:

- `tool_calls.py`, `round_export.py`, `agent_page.py`: live, used by the grading-page skill.
- `grading_page.py`, `grading_page.html`, `grading_page.js`, `results.py`: the old round 7 grading page. Kept only because `agent_page.py` imports `grading_page`, which imports `results`. `results.py` can't read rounds 8 and later; don't build on either.
- `picture_check_cases.json`: 62 starting pictures from rounds 2-3 (old pipeline). 30 are Julian's grades (10 wrong, 20 right); the other 32 are only "no complaint", not his grade. 6 of the wrong ones and the 20 right ones fit today's picture check question 1 (same product); the 4 toilet ones fit question 2 (physics) at best, his call.
- `owner_notes/`: borderline; its tests pass, it replays round 4's old-pipeline ads.
