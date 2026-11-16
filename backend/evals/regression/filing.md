## File grades into cases

```
uv run python -m evals.regression file-grades <run> [grades.json] --store <dir>
uv run python -m evals.regression move-case <case-id> <question-id> --store <dir>
```

`file-grades` reads the run's saved `grades.json`, or imports the optional file
there first. Every reached page, plan, kept starting picture, kept clip, finished
ad and completed owner note becomes cases for the questions at its stage that
were named from Julian's own complaints; no Claude-written question is offered. A missing tap passes; an exported grades file is required before that
rule is applied. Words are filed even without a Fail tap. A fail without words
stays unfiled, prints which item needs words, and exits 1. No-worse taps, photo
labels, other-take taps and whole-run notes remain in the grades archive.

Each complaint uses the cheap text model to select his earlier questions or name
one new question from his words; cases retain Julian's words and the saved output
files. Every filed item is recorded in `filed-grades.json`, even before any of his
questions exists at its stage, so a question named later gets that item's pass,
in this run and in the store's earlier filed runs: a later complaint then shows
as broke against a baseline he did not complain about. The command
prints the filing for correction. `filed-grades.json` saves each item's filing
decision before appending, so an interrupted command resumes missing lines and
re-filing a run adds no duplicates or model calls. Existing cases and
`clip_product.jsonl` are never rewritten by filing.

`move-case` is an explicit correction: the source question becomes pass, the
destination becomes fail with the original words, and its id and half follow
the destination question. An existing destination pass is replaced, so both
files remain valid; unrelated lines are kept verbatim. Corrections stay within
the item's stage and cannot target code questions or `clip_product`. Judge
questions are also valid destinations, including corrections between judge questions.

### Judge questions

When `judge-answers.json` exists, filing also saves one case per judged item and
judge question in `P1.jsonl`, `P4.jsonl`, `C5.jsonl`, and so on. An explicit
three-tap answer supplies the label: **not a flaw** means pass; either **real
flaw** means fail, including a flaw that is fine to ship. The case's source keeps
the stored `tap`; its reason names that tap and retains the optional note. Each
case also keeps the judge's counted verdict, prompt-version id, and all its asks.
Product facts, spoken line, what the scene shows, and page-photo labels accompany
the item's media followed by the page photos. App verdicts and prompts do not.

The same cheap complaint-filing call independently selects matching fixed judge
questions as well as Julian's own questions. A complaint against a judge pass
therefore becomes a failed case with his exact words. A shown item with no
complaint becomes a pass. An item left off the page because all its questions
passed and are trusted gets no implicit human label. Explicit grades still win;
presenters and whole-ad grading remain Julian's to watch.

An untapped judge fail stays unfiled, is named, and makes `file-grades` exit 1.
`promote` refuses any untapped or unfiled judge fail, even on a first baseline.
The ordinary fail-without-words guard remains in place. Judge questions' filing
decisions are saved in `filed-judge-grades.json` before appending the cases, while
complaint classification remains in `filed-grades.json`; re-filing asks no model
again and adds no duplicate lines. `move-case` updates both journals so a
correction stays corrected after another filing. A run with no judge answers
uses the original filing flow.

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
