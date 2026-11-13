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
No feedback counts as pass; only Julian grades quality. The page lists no
questions to grade, and "no worse" is asked only when a scenario has an old run.
The plan's fail tap reads "Product used the wrong way, or makes no sense": he is
never asked to check facts or prices. Pictures show small; a tap opens the full file
(dropped page photos are kept at 512 px, so they open at that size).

"The page read" shows what the read kept on the left and what it dropped on the
right, page text and photos, grouped by the app's recorded reason. The export's
`regression.json` `page_read` holds them: the copy call's passages against the page
text it was given, and the photo picker's picks against the page's numbered
pictures, the kept photos and the skipped photos' notices. Dropped photos are
shown from the saved page (`pages/<scenario>/manifest.json` downloads), at most
512 px. A run exported before `page_read` existed shows only what was kept.

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
