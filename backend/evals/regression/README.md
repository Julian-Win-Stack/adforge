# Regression eval

Implementation of [spec #598](https://github.com/Julian-Win-Stack/adforge/issues/598).
Run from `backend/`: `uv run python -m evals.regression`. This lists the subcommands;
their implementations are added by the spec's remaining tickets.

`--store <dir>` is accepted before or after the subcommand. Without it the command
uses `EVAL_STORE`; a lasting store must be explicitly chosen. Media and database
dumps belong in the store, outside git.

## Shared interfaces

- `questions.all_questions()`, `for_stage(stage)`, `julian_questions(stage=None)`,
  `code_questions(stage=None)` return `Question(id, stage, answered_by, words)`.
  Each accepts `cases_dir=...` for a different case folder. Unknown question files
  join the list when their first case has `inputs.handoff.stage` and `.question`;
  older case files without those fields are left out. Static questions never get
  replaced by case metadata. `presenter_real_person.case_file` points to its
  existing case file.
- `cases.case_id(run, scenario, question, item)`, `kind_for(label)` and
  `make_case(...)` use the existing eval case format and `half_for` split.
- `store.Store(root=None)` provides named paths and read/write helpers for run
  metadata, answers, grades, baselines, intentions and photo labels. Missing files
  read as empty; malformed files raise an error. TypedDicts in `store` document
  the JSON shapes. Call `mark_done` only after the scenario's dump, media, export
  and answers have been saved. A scenario without `done` is unfinished.
- `store.write_json(path, value)` and `atomic_write_text(path, text)` flush and
  atomically replace files. The dump/media/export paths let their existing
  exporters write directly to the scenario folder before the final marker.

To register a subcommand, edit its single line in `__main__.COMMANDS` from `None`
to `"evals.regression.<module>"` or `"evals.regression.<module>:<function>"`. Its
handler is `main(argv: Sequence[str], *, store: Store) -> int` (or the named
function). It parses only its own arguments; `--store` is already removed. Imports
are lazy, so listing commands never initializes the app or calls a paid service.
