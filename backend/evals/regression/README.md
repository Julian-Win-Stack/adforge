# Regression eval

Implementation of [spec #598](https://github.com/Julian-Win-Stack/adforge/issues/598).
Run from `backend/`: `uv run python -m evals.regression` lists the commands for
saved-page runs, owner-note runs, code answers, grading, case filing, comparison
and promotion.

`--store <dir>` is accepted before or after the subcommand. Without it the command
uses `EVAL_STORE`; a lasting store must be explicitly chosen. Media and database
dumps belong in the store, outside git.

## Shared interfaces

- `questions.all_questions()`, `for_stage(stage)`, `julian_questions(stage=None)`,
  `code_questions(stage=None)`, `judge_questions(stage=None)` return `Question(id, stage, answered_by, words)`.
  Each accepts `cases_dir=...` for a different case folder. The static list holds
  code-answered questions, the 11 judge questions of #644 and `note_questions()`'s change
  check `N1` of #695. Julian answers only questions named from his own
  complaints: a case file joins the list when its first case has
  `inputs.handoff.stage` and `.question`, which filing writes when it starts one.
  Older case files without those fields are left out, and case metadata never
  replaces a static question.
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
questions, including questions introduced by later complaints in the same run and,
through their `filed-grades.json`, the store's earlier filed runs.
Re-filing also picks up questions from other case files without reclassifying
complaints, changing existing labels, or turning unresolved failed taps into passes.

The [runner guide](runner.md) covers plans, pictures, full ads, `--from` replay,
estimates, spend limits, owner questions and continuation.

## Workflow

Choose `EVAL_STORE` outside git. These commands describe the workflow; saving pages
uses Firecrawl credits and `run` prints a paid-work estimate before starting.
Declare an intended change with `intend` before starting the run when applicable.

```sh
uv run python -m evals.regression save-pages --only ridge-wallet
uv run python -m evals.regression run --to full --only ridge-wallet --name after-change
uv run python -m evals.regression answer after-change
uv run python -m evals.regression grading-page after-change
# A big run passes the 1 GiB page media limit: build it in parts instead.
uv run python -m evals.regression grading-page after-change --only ridge-wallet,furbo-360 --output pages/full
# Grade the page, then download/export its grades.
uv run python -m evals.regression grading-page after-change --import-grades grades.json
uv run python -m evals.regression answer after-change
uv run python -m evals.regression file-grades after-change
uv run python -m evals.regression report after-change
uv run python -m evals.regression promote after-change
```

`run`, `notes` and the judge commands first check every case (keys, database, saved
pages and sources) and start nothing on any problem. A case that stops for a setup
reason mid-run prints `STOP: <case>: <reason>`, appends it to `runs/<run>/STOP`, and
no new case starts.

The plan card (spec #768): each run's first message is the scenario's link plus
"Make it a person talking ad.", so all 3 plans are person talking and the baseline
compares like with like. The run answers the card itself, as a shop owner pressing
"Use the agent's picks" would, through the chat's own answer, and prints
`plan card: <the 3 plans>; answered: ...`. `run --plan 2` picks the second plan
instead. A producer that stops after the page with no card stops the case with a
`no_plan_card` developer notice and a `STOP:` line.

Importing grades also updates shared photo labels. The second, free `answer`
applies those labels to answers that initially said `needs_label`, before filing,
comparison and promotion. Only Julian grades pictures and ads. A first complete,
graded and filed run can establish a scenario's baseline; subsequent runs may use
`--from` to pay only for a changed stage and its suffix.

Details: [code answers](answers.md), [grading and import](grading.md),
[case filing](filing.md), and [reports, intentions and promotion](reports.md).

## Note runs

A note run tests fixing a finished ad from an owner note (spec #695). Each **note
case** in `note_cases.json` is one finished ad frozen in a saved run plus one note
written the way a shop owner writes: what looks wrong and roughly where, never how
to fix it. Fields: `id`, `scenario` (a catalogue scenario), `source_run` (the saved
run the ad comes from, never "the current baseline", so promotion can't swap the ad),
`scenes` (scene numbers in that ad the problem is in, from Julian's grade), `note`
(the exact text sent), `grade` (Julian's words) and `graded_from`. One note per
problem; Julian OKs every note's wording before it goes in. A malformed file is
refused, naming the entry.

```sh
uv run python -m evals.regression notes <run name> [--only <case id> ...] [--parallel N] [--yes]
```

It prints the estimate (each case's most recent actual cost from an earlier note
run, else $4 a case) and waits for `yes`; no new paid work starts at 1.5 times the
estimate. Each case runs in its own process: the source run's saved scenario (dump
and media) is restored into a fresh, migrated database, the chat is copied with
`session_copy`, the note is sent through the in-process chat API, and the app works
until the producer settles, with the scenario's saved page, quality checks on and
real paid services. The source folder is never changed. A source that is missing,
unfinished, has no finished ad or a busy chat stops before paid work, with the
reason. If the app asks the owner something and makes no new ad, the case stops
with the question's text; nothing answers for the app. The same name again skips
finished cases; a saved run started at another app commit, or with cases it
didn't select, is refused.

Saved under `runs/<run>/` (case id in a scenario's place):

- `run.json`: `kind: "notes"`, `name`, `app_commit`, `cases`, `estimate_usd`,
  `started_at`, `finished_at`, `spend_usd`, and `results` per case
  (`status` done/stopped/failed, `why`, `cost_usd`, `minutes`).
- `<case>/case.json`: the catalogue entry plus `source` (`run`, `scenario`,
  `session_id`, `ad`) and `copy_session_id`; `db.dump`, `media/`, `result.json`,
  and `done` last.
- `<case>/answers.json`: `answers` with three code answers, plus `cost_usd` (this
  case's new calls only, from the ledger) and `minutes` (note sent to new finished ad):
  - `note_new_ad`: a newer finished ad was made after the note; else fail with the
    app's last message.
  - `note_right_scenes`: one item per expected scene, passing when the app made an
    owner fix for that scene and its clip changed.
  - `note_untouched_same`: every scene with no owner fix kept exactly the same clip
    (a new voice or presenter touches every scene).
- `<case>/export/<case>/regression.json`: the copy's export, with `notes` (the note
  and `note-1/before.mp4` / `note-1/after.mp4`, `after` missing when no new ad) and
  `note_case` (`id`, `expected_scenes`, and every scene of the before ad in order
  with `before_clip`, `after_clip` or null, and `changed`).

Where to run: a case needs its source run folder in the store. baseline-2's
dumps are in the project's eval store and regression-3's on the Azure box, so run
where both are present. Copy each source scenario folder whole: a source needs
`done`, `answers.json` and `db.dump` (`Store.scenario_done`).

Note runs (#695: one owner note per saved failed ad, fixed by the latest app) use
the same `file-grades` and `report` commands: see the note-run sections of
[case filing](filing.md#note-runs) and [reports](reports.md#note-runs).

## Judging a note run (change check, #697)

Run only when Julian asks, after a note run (`kind: notes`) has finished its cases:

```sh
uv run python -m evals.regression judge-notes <run name> [--only <case id> ...]
```

It asks two things per finished case and saves them in the run's `judge-answers.json`
(case id → question id → item), resuming where it stopped:

- **Change check, `N1`** ("The change the owner asked for was made"), item `note`,
  verdict `made` or `not_made`. Inputs: the owner's note, the product name, the labelled
  page photos, and every scene of the first ad and of the new ad as frames,
  in scene order, each picture labelled with its ad, scene and seconds. It never gets the
  expected scenes, which scenes changed, the app's verdicts, reasons or prompts. It writes
  what the owner is unhappy about, which first-ad scenes show it and whether the new ad
  still does, then answers. A `not_made` is asked twice more and counts only at 2 of 3.
  Wording approved by Julian Nov 17, 2026: `evals/judge/questions/N1.md` and `_note.md`.
- **Eval judge on remade clips**: `C5`-`C10`, `C13`, `C14` on the new clip of each scene
  whose `changed` is true (item `s<N>`), with the judge's own inputs and re-asks.
  Unchanged scenes are not judged.

A case with no new ad is skipped; an unfinished case is skipped. Both checks use the
eval judge's Azure account; a missing account, unreadable media or a failed call posts
`EVAL_JUDGE_UNAVAILABLE`, saves the error and exits 1 naming the case.

**Frame rate.** 2 frames a second. A 35-second ad pair is 70 s, so 140 frames. Azure takes
at most 50 pictures; the page photos use up to 10, which leaves at least 40. Consecutive
frames of one scene are joined side by side (`jobs.clip_judge.joined`) until every scene
fits, never skipped: a 35-second pair shows 3-4 frames per picture.

**Cost per case** (an estimate from the eval judge's clip asks, not yet a real N1 call;
the command prints the real cost): one change-check ask is about 40-50k input tokens on gpt-6.2-sol, about $0.10 (the eval judge's 43-50-picture
clip asks cost $0.07-0.08). So about $0.10 when `made`, about $0.30 when `not_made`
(asked 3 times), plus about $0.50-0.85 per remade clip for the eight clip questions.

## How much to run

Julian's call (Nov 16, 2026):

- Run all 15 scenarios every time. Failures land on different products from run
  to run (baseline-2's 7 failed ads and regression-3's 3 had no product in
  common), so no smaller set holds "the hard ones", and fewer products often
  can't show whether a fix worked.
- Stop at the stage the change reaches: `--to plans` for a planning-only change
  (about $10), `--to pictures` for a picture change (about $25–30), and
  `--to full` for clips, video or anything later (about $150 plus the judge).

## Saved product pages

`save-pages` freezes the three Firecrawl answers, the screenshot and HTML, all page
photos (including the full-size and original links the app may try), posters and
page videos. No model is called. Each page uses three Firecrawl credits. Set
`FIRECRAWL_API_KEY` and `EVAL_STORE`, and migrate the database `DATABASE_URL` points at
(`uv run python manage.py migrate`): Firecrawl's shared pacing is kept there, so
`save-pages` refuses to start on an unmigrated database. Then run:

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

**Checked by Julian.** On Nov 12, 2026 Julian checked the page read of all 30
baseline-2 scenarios (the photos and text the app kept from each saved page) and
found every one correct. Treat these saved pages, and what the app read from them,
as the truth about the product: when an eval judge disagrees with them, the judge
is wrong, not the page. A page saved or refreshed after that date is unchecked
until he checks it.

The runner installs `SavedPageSource(store.page_dir(scenario.key))` using
`jobs.page_source.use_page_source`. This is an in-process scope, with no setting or
environment switch. It serves every page download and Firecrawl answer from the
snapshot, including a fresh re-read; page-check, copier and picker model calls
still run. A missing saved file fails the read and records the developer notice
`saved_page_missing`. It never substitutes a live input or silently skips the file.

`scenarios.json` holds the approved 15 full-ad scenarios. The 15 picture-only
scenarios were dropped on Julian's call (Nov 15); older saved runs still hold
them, and grading pages for those runs still show them. Product links
and grading identifiers come from the round 9–15 cases in PR #599 and
`docs/test-products.md` for round 4. Round 4 uses its known ad numbers (14, 7, 13),
since the full folder names are unavailable here. Brooklinen Luxe Sateen's link
comes from round 15's product list (project files `runs/round-15/products.md`,
"Final links"); its ad folder is still unknown.
