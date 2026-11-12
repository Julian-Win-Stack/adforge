## Compare and promote a run

```
uv run python -m evals.regression intend ridge-wallet picture_possible "Explain the intended change"
uv run python -m evals.regression report after-change
uv run python -m evals.regression promote after-change
```

`report` prints the comparison and saves `runs/<run>/report.md`. It compares each
question across all its items, so changing the number of scenes cannot hide a
regression. Questions not reached or waiting for photo labels are listed apart.
The report includes Julian's words, his no-worse tap, first-try rates, clips per
scene, and costs. Before `grades.json` exists, it reports code answers only.

An intention must predate the run's `started_at`. Its first eligible report marks
it `used_by: <run>` in `intended.json`; repeated reports of that run keep it, and
later runs do not inherit it. Declare intentions before starting paid work.

`promote` refuses ungraded or unfinished runs and protects existing baselines when
the ship rule fails. Run `file-grades` first: every reached page, plan, picture,
kept clip, ad and note must be filed, with a case for every question named from
Julian's complaints at its stage, including his implicit passes. Saving taps alone never replaces those cases.
Complete, graded scenarios without a baseline can still
become their first baseline in a failing run; the command reports that partial
promotion and exits 1. An explicit failed tap must first be filed into a failed
case with the same words, even for a first baseline. All old runs stay in place.
