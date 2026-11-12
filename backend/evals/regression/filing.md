## File grades into cases

```
uv run python -m evals.regression file-grades <run> [grades.json] --store <dir>
uv run python -m evals.regression move-case <case-id> <question-id> --store <dir>
```

`file-grades` reads the run's saved `grades.json`, or imports the optional file
there first. Every reached page, plan, kept starting picture, kept clip, finished
ad and completed owner note becomes cases for its stage's Julian-answered
questions. A missing tap passes; an exported grades file is required before that
rule is applied. Words are filed even without a Fail tap. A fail without words
stays unfiled, prints which item needs words, and exits 1. No-worse taps, photo
labels, other-take taps and whole-run notes remain in the grades archive.

Each complaint uses the cheap text model to select questions or name one new
question; cases retain Julian's words and the saved output files. The command
prints the filing for correction. `filed-grades.json` saves each item's filing
decision before appending, so an interrupted command resumes missing lines and
re-filing a run adds no duplicates or model calls. Existing cases and
`clip_product.jsonl` are never rewritten by filing.

`move-case` is an explicit correction: the source question becomes pass, the
destination becomes fail with the original words, and its id and half follow
the destination question. An existing destination pass is replaced, so both
files remain valid; unrelated lines are kept verbatim. Corrections stay within
the item's stage and cannot target code questions or `clip_product`.

Media references use `project-files:` by default: relative to `PROJECT_FILES`
when inside it, otherwise an absolute local path. On Azure choose
`--media-place azure`, or `--media-place mac` for a Mac archive. `--cases-dir`
selects a different case folder for either command.

The item reader uses `export/<scenario>/regression.json` when present, and the
existing round-export `results.json`, scene inputs and kept-take files otherwise.
Legacy owner notes come from `notes.json`: one note can use the original and
latest ads; several notes need the saved intermediate before/after paths. A
missing boundary is named explicitly rather than using the whole run's ads for
each note. Missing named outputs fail visibly; unreached stages produce no cases.
