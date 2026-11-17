## Grade a regression run

Build from the lasting store's saved exports, without a database or model call:

```sh
uv run python -m evals.regression grading-page <run> --store <dir>
```

The page is `runs/<run>/grading-page/grading.html`. It uses the grading-page skill's
renderer, styles, autosaving grade controls and image lightbox.
Each scenario shows new beside its baseline, the code answers that changed, plan,
starting pictures, every clip take, model inputs, owner notes and full app records.
Baseline scenes are folded independently because their scene numbers may differ.
No feedback counts as pass; only Julian grades quality. The page lists no
questions to grade, and "no worse" is asked only when a scenario has an old run.
The plan's fail tap reads "Product used the wrong way, or makes no sense": he is
never asked to check facts or prices. Pictures show small; a tap opens the full file
(dropped page photos are kept at 512 px, so they open at that size).

When a run has saved eval-judge answers, each failed question appears beside its
picture, kept clip or finished ad. It shows what the judge saw, when and what the
fault is on, and how many asks failed. Julian can tap **not a flaw**, **real flaw,
fine to ship**, or **real flaw, don't ship**, and add a note. These are his decisions;
the page never turns a judge answer into his grade. A missing or errored answer is
shown as **Not judged**, with the saved error when available.

Pictures and clips are left off only when all their applicable judge questions
passed (or did not apply) and every question is trusted by `evals.judge.trust`.
Anything with a not-trusted question stays. Presenters, whole-ad grades, "no worse",
the page read and photo labels stay available. A run without judge answers keeps
the existing page.

"The page read" shows what the read kept on the left and what it dropped on the
right, page text and photos, grouped by the app's recorded reason. The export's
`regression.json` `page_read` holds them: the copy call's passages against the page
text it was given, and the photo picker's picks against the page's numbered
pictures, the kept photos and the skipped photos' notices. Dropped photos are
shown from the saved page (`pages/<scenario>/manifest.json` downloads), at most
512 px. A run exported before `page_read` existed shows only what was kept.

### Publish in minutes

Pictures travel in a few base64 text bundles (`up/pNN.txt`, under 19 M characters
each); the page rebuilds each one full size in the browser when it nears the screen,
and a tap still opens it full size. Videos stay their own files (`up/vNNN.mp4`),
music gets a still black picture so the host accepts it, and repeated files are kept
once. The host commits each uploaded file on its own (~0.8 s), so this turns ~900
files into ~60 for 7 ads: about 3 minutes instead of 20. The skill's "Media on the
host" section has the host rules behind it.

1. Build with `--output` inside the session's working folder: the tool batches only
   files under it. `uploads.json` names each file by its full path. Pictures are encoded on all cores and cached in
   `~/.cache/adforge-grading-pictures` (`--cache`), so a baseline's are free next time.
2. Publish `grading.html` privately with `publish.json`'s capabilities (`db`, `assets`).
3. Upload what `uploads.json` lists, yourself, in ONE message: one Artifact call per
   `single` bundle (`url`, `asset: true`, `file_path`), then one call per `batches`
   entry (`file_paths`). Don't open the bundles: they are this build's output.
4. Relink and republish the same `url`, leaving out `capabilities`:

   ```sh
   uv run python -m evals.regression grading-page <run> --output <dir> \
     --url <artifact url> --apply-uploads ~/.claude/projects/
   ```

   It reads the upload results of calls to that `url` from the session transcripts
   and names any file without one. `page-data.json` keeps the local version, so
   relinking never rebuilds.

Videos over 15 MB are re-encoded to fit the host and named in `publish.json`; the
page marks those scenarios. Builds refuse media above the host's asset limits.
A picture that fails to load says so on the page, with a reload hint.

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

Eval-judge controls use `{tap, note, at}` and append the question to the doc id
(for example `mug__s1__clip__C6`). The shared renderer uses the page's optional
`tapFields` and `tapChoices` metadata for these controls, keeping the same debounced
autosave, local drafts and visible save errors as other notes. A note can save
before its tap (`tap: null`) on the page; importing it leaves that judge fail
untapped in the run. Removing a previously imported tap also clears it from the
run's grades. Every note box saves while typing, without a button press.

The runner calls `grading_records.export_job(job, base, scenario=...)`
after the round export, where `base = store.export_dir(run, scenario) / scenario`.
It writes the model-call records and `regression.json` supplement with the scenario,
all approved plan scenes, reached picture/clip paths, an empty `notes` list (note runs fill it in) and
page snapshots. Paths in the supplement are relative to `base`; media keys in the
rich records read from the scenario's durable `media/` directory. The runner copies
that file store before marking the scenario done.

### Grade a note run

The same command builds a page for a note run (`run.json` `kind: notes`, from
`notes <run>`), with case ids in place of scenarios (`--only` takes case ids). It reads
the saved note-run layout only: each case's `case.json`, `answers.json`, the export's
`regression.json` `notes` and `note_case`, `run.json` `results`, and the run's
`judge-answers.json` as saved (the change check `N1`, item `note`, beside the clip
questions). `evals.regression.grading_notes` builds each card.

One card per case shows the owner note, Julian's grade words, the code answers, the
change check's answer and what it saw, the whole before and after ads, and the before
and after clip of every scene the app remade plus the expected scenes (marked
**not remade** when their clip did not change). The eval judge's clip questions show
only on remade clips, with the three taps above; a saved answer on a kept clip is left
off, and only its counted fails show. Two taps per case, **Problem gone?** and **Nothing new broke?** (yes or no), share
one words box: what Julian types autosaves (debounced, and on leaving the box) into both
documents, `<case>__note__gone` and `<case>__note__no_new_break`, each
`{tap, note, at}`. The box shows the newest words held by either document, so words
saved with only one tap come back and the next keystroke keeps them. A case whose
`note_new_ad` failed, or whose run result is `stopped` or `failed`, shows what happened
and takes no taps (its clips are left off). A case with no saved code answers yet keeps
its taps, says so on the page, and is named on stderr.

`--import-grades` stores both taps under those ids in the run's `grades.json`, leaving
other grades as they were. Words saved before a tap are not a grade: they stay on the
page but are not imported, as for the judge's taps.
