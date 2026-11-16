# Eval judge prototype (spec #644)

Throwaway prototype on branch `eval-judge-prototype`: never merged. Does a judge that asks one
question per call match Julian's grades? A good result is rebuilt from #644 with `implement`.

## Run (from `backend/`)

```
uv run python -m evals.judge_prototype --split score [--questions P1,C5] [--limit N] [--fake]
uv run python -m evals.judge_prototype --split score --score
uv run python -m evals.judge_prototype --counts --results THIS.jsonl --last LAST.jsonl
```

- Default questions: the spec's 11 (Nov 16): pictures P1, P4; clips C5-C10, C13 hand, C14
  action works; ad A11. P2 (nothing impossible in the picture) is gone. A12 (presenter, Gemini)
  runs only when named.
- A fail is asked 2 more times and counts only when 2 of the 3 asks fail (`REASKS`); the row
  keeps every ask in `asks`. A fail from an older results file asked once is asked the 2 more
  times on the next run over that file (cheap way to bring a last run up to date).
- `--counts`: flaws per question, last run then this run ("clip: hand: 3 clips, then 1"), with
  this run's failed items. Only the current judge ids count: when a question changed or is new,
  run it on the last run's cases and results file first so the two counts compare.

- Cases: `/mnt/project-files/eval-store/judge-cases/cases.jsonl` (`--cases`), written by the
  data thread. Format in `cases.py`. Ad-level lines also carry `talking_clips` and
  `talking_lines` (for A12) and `ad_stills` (for A11).
- Results: one JSON line per answer in
  `/mnt/project-files/eval-store/judge-cases/results/judge-results.jsonl` (`--results`):
  item, question, judge id, what it sees (`sees`, `when`, `on_what`), verdict
  (pass/fail/doesnt_apply, or null with `error` for a refused/failed call), tokens (cached
  and cache-write too), cost. Resumes: an item already answered by the same judge id is skipped.
- `--fake`: the gateway's fake model, free; prints what each call is shown.
- Never tune on `--split score` (baseline-2).

## What each call gets

- Shared instructions `questions/_judge.md` (answer shape, Rules A and B), then the scene's
  line and what it is shown, then the pictures, then the question `questions/<id>.md` last.
- Pictures P1, P2, P4 (P3 removed: a human check, see `backend/evals/human-only-checks.md` on main): page photos (at most 10) + the starting picture. Azure gpt-6.1-sol.
- Clips C5-C10: page photos + every frame at 10 fps, joined side by side past 50 pictures
  (`jobs.clip_judge.cut_pictures`). One clip's questions run one after another.
- C13 (hand, answered on `HandForm`: one row per stretch of the same grip) and C14 (action
  works, its 4 steps): every frame at 5 fps and NO page photos, as tested on regression-3
  (`eval-store/runs/regression-3/judge-deep-dive/scripts/run_e3.py`).
- P1, C5, A11 carry the app clip judge's hidden-part rule word for word
  (`jobs/clip_judge.py:HIDDEN_PART_RULE` on main; Julian, Nov 16).
- Ad A11: page photos + one still per scene. A12: each talking clip as video with sound at
  5 fps on Gemini (watcher path); the ad fails when any talking clip fails.
- Judge id = question id + 8-char hash of shared instructions + question + answer shape + fps
  (+ the inputs, for questions shown something other than 10 fps with photos). Editing
  `_judge.md` changes every judge id; re-asking fails did not change any.

## Scoring

Per question and per item (fails when any chosen question fails), against Julian's label:
counts, recall, precision, Cohen's kappa, trust bar (recall >= 0.8, precision >= 0.7, kappa >=
0.7; temporary). A question's label on an item: fail if a case line names it; pass if he passed
the item or failed it only for other questions; unknown (item level only) if a fail of his has
no question. "doesnt_apply" on his fail counts as a miss; on his pass it isn't counted.

## Findings so far (Nov 13)

- Azure prompt cache only hits an identical whole prompt: a different question at the end
  gets 0 cached tokens (tested). So the 6 clip calls are billed uncached: about $0.075 a call
  for a 7 s clip with 8 page photos (37k input tokens), $0.018 a picture call. Re-running an
  unchanged question on the same item does hit the cache.
