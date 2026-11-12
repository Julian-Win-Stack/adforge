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

`file-grades` completes every filed item's coverage after discovering new stage
questions, including questions introduced by later complaints in the same run.
Re-filing also picks up questions from other case files without reclassifying
complaints, changing existing labels, or turning unresolved failed taps into passes.

The [runner guide](runner.md) covers `run --to plans`, estimates, spend limits,
question handling and continuation.

## Saved product pages

`save-pages` freezes the three Firecrawl answers, the screenshot and HTML, all page
photos (including the full-size and original links the app may try), posters and
page videos. No model is called. Each page uses three Firecrawl credits. Set
`FIRECRAWL_API_KEY` and `EVAL_STORE`, then run:

```
uv run python -m evals.regression save-pages
uv run python -m evals.regression save-pages --only ridge-wallet logitech-pebble
uv run python -m evals.regression save-pages --refresh ridge-wallet
```

Already saved pages are skipped. `--refresh` alone selects only its named pages;
with `--only`, every refreshed page must be selected there too. A successful refresh
adds an immutable snapshot and replaces the manifest atomically, keeping the old
files. A dead page never gets a complete manifest. Expected unavailable photos or
players keep their recorded failure, so replay takes the same app fallback rather
than trying the live shop.

The runner installs `SavedPageSource(store.page_dir(scenario.key))` using
`jobs.page_source.use_page_source`. This is an in-process scope, with no setting or
environment switch. It serves every page download and Firecrawl answer from the
snapshot, including a fresh re-read; page-check, copier and picker model calls
still run. A missing saved file fails the read and records the developer notice
`saved_page_missing`. It never substitutes a live input or silently skips the file.

`scenarios.json` holds the approved 15 full and 15 picture scenarios. Product links
and grading identifiers come from the round 9–15 cases in PR #599 and
`docs/test-products.md` for round 4. Round 4 uses its known ad numbers (14, 7, 13),
since the full folder names are unavailable here. **Brooklinen Luxe Sateen's link
and ad folder were not present in the accessible records.** Its entry is explicitly
missing those fields; `save-pages` reports it as a dead/missing link until the
original grading record supplies them. No replacement URL was guessed.
