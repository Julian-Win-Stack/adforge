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

### fact_check

A case is one scene of a script. `inputs.handoff` is the whole fact check handoff as the app sent it (a `jobs.checks.FactCheckHandoff`), `inputs.scene` is the scene the case is about, and `inputs.files` are the product photos the app showed, in their order. Today every case is planted: one scene of a real script changed into a made-up claim, the rest of the script left as the planner wrote it. So until Julian grades real lines the check failed, "both pass" and "false alarm" stay empty, and a check that failed everything would look perfect. The command therefore also prints how many of the unchanged scenes in the same scripts it failed: if that number jumps after a prompt change, the check has grown too strict. A planted case's `kind` is `regression` when the check caught it on its first run and `capability` when it missed.

The photo labels are approximate: the app labels a photo by its position on the page ("Photo 4", plus where in the video a still was cut), and the saved inputs don't keep those labels, so the command numbers the photos 1, 2, 3 in order.

Re-ask today's check on every case, appending its verdict (free when text runs on Azure; reads photos from `PROJECT_FILES`, default `/mnt/project-files`):

```
uv run python -m evals.fact_check [--ids <id> ...]
uv run python -m evals.boxes fact_check
```

Scenes of one script are checked in one call, and photos are shown only when a scene has a "shows", as the app does. Each verdict's `judge` is a hash of the prompt, so `--judge` counts one prompt's verdicts apart from another's.

## Older files in this folder

The old rule evals (Oct 9-11) were deleted on Julian's call (Nov 5); git history keeps them. What is left from before this home:

- `tool_calls.py`, `round_export.py`, `agent_page.py`: live, used by the grading-page skill.
- `grading_page.py`, `grading_page.html`, `grading_page.js`, `results.py`: the old round 7 grading page. Kept only because `agent_page.py` imports `grading_page`, which imports `results`. `results.py` can't read rounds 8 and later; don't build on either.
- `picture_check_cases.json`: 62 starting pictures from rounds 2-3 (old pipeline). 30 are Julian's grades (10 wrong, 20 right); the other 32 are only "no complaint", not his grade. 6 of the wrong ones and the 20 right ones fit today's picture check question 1 (same product); the 4 toilet ones fit question 2 (physics) at best, his call.
- `owner_notes/`: borderline; its tests pass, it replays round 4's old-pipeline ads.
