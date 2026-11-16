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

## The eval judge's flaw counts

A run with saved judge answers adds one line per judge question, using that
question's unit, for example `C13 The holding hand is a real hand: 3 of 40 clips,
then 1 of 38 clips`. The first count combines each scenario's kept baseline in
`baseline.json`; the second is this run. The run just before this one is never
used unless it is that scenario's baseline. Pictures and clips do not make their
finished ad fail: Julian's whole-ad grade stays his own.

Each question names the scenario and scene whose flaw is **gone**, **new** or
**still** there, with `judge` or `Julian` beside it. A saved tap or a complaint
filed under that judge question overrides the model. Both real-flaw taps count a
flaw; `not a flaw` removes it. Doesn't-apply answers count as neither pass nor
fail. Failed or incomplete model calls are listed as **not judged**, and judge
fails without Julian's tap are listed separately.

A missing baseline answer names its run and prints `python -m evals.regression
judge <baseline>` to fill it. An older question version also needs the baseline
asked again; those incomparable answers do not claim a flaw is new or fixed.
A question not asked on this run, or an item the judge could not judge, cannot
silently count as a fix.

Trusted questions' larger or smaller flaw counts contribute to the report's
`broke` or `got fixed` totals. Not-trusted questions remain visible and marked
`not trusted yet`, outside those totals. These counts never fail the ship rule
on their own. Of the judge's three taps, only **real flaw, don't ship** fails it;
**real flaw, fine to ship** stays a flaw in the count but permits shipping.
Judge cases are excluded from the existing all-or-nothing question comparison,
so filing them cannot accidentally fail the ship rule that way. Existing code
questions, Julian's other grades, and his **new is no worse** tap still apply.

The section shows the judge's cost for this run beside the other run costs. It
includes every saved ask, billed refusal, and older prompt version retained
when a question was asked again. A run with no judge answers keeps its original
report.
