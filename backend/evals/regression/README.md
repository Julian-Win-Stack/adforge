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

## Grade a regression run

Build from the lasting store's saved exports, without a database or model call:

```sh
uv run python -m evals.regression grading-page <run> --store <dir>
```

The page is `runs/<run>/grading-page/grading.html`. It uses the grading-page skill's
renderer, styles, autosaving grade controls, image lightbox and media batch builder.
Each scenario shows new beside its baseline, the code answers that changed, plan,
starting pictures, every clip take, model inputs, owner notes and full app records.
Baseline scenes are folded independently because their scene numbers may differ.
No feedback counts as pass; only Julian grades quality.

`publish.json` describes the private Artifact's required `db` and `assets`
capabilities. Publish the HTML privately, upload `abatches.json`'s asset batches,
and use `aname.json` to map returned URLs to the media keys. Rebuild with
`--media-map <urls.json>` and republish the same Artifact. Each media-map value is
its uploaded URL; the keys match `media-map.json`. `--output <dir>` selects another
build folder. Media copies keep full-resolution pictures; videos over 15 MB are
re-encoded to fit the host and named in `publish.json`. The page marks scenarios
with re-encoded videos. Builds refuse media above the host's total asset limits.

Taps save to the Artifact's `grades` collection as `{grade, note, at}`. Export that
collection, or use **Download grades** on **Start here**, then file it immediately:

```sh
uv run python -m evals.regression grading-page <run> --import-grades <grades.json>
```

An exported directory of `grades/<doc id>.json` is accepted too. This writes the
run's `grades.json`, `grades.md` and shared photo labels, retaining Julian's exact
words. The canonical controls use the spec's document IDs. Earlier picture
versions, end pictures and earlier clip takes use `<scenario>__s<N>__take<K>`,
with distinct numeric archive numbers within a scene. Those and `round__notes`
remain in the grade archive, while case filing uses the kept picture and clip's
canonical `__s<N>__picture` and `__s<N>__clip`.

The runner calls `grading_records.export_job(job, base, scenario=..., notes=...)`
after the round export, where `base = store.export_dir(run, scenario) / scenario`.
It writes the model-call records and `regression.json` supplement with the scenario,
all approved plan scenes, reached picture/clip paths, notes' before/after files and
page snapshots. Paths in the supplement are relative to `base`; media keys in the
rich records read from the scenario's durable `media/` directory. The runner copies
that file store before marking the scenario done.
