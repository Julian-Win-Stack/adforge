# Run the fixed scenarios

From `backend/`, choose the lasting store with `--store <dir>` (before or after
the subcommand) or `EVAL_STORE`. Save the scenarios' pages first with `save-pages`.
The runner uses those frozen page and media files; it never reads the live shop.

```sh
uv run python -m evals.regression run --to plans --name after-change
uv run python -m evals.regression run --to plans --only ridge-wallet --name ridge-plan --yes
uv run python -m evals.regression run --to pictures --name picture-check
uv run python -m evals.regression run --to full --name full-check
```

The estimate is printed before any paid work. Type `yes` to start, or pass `--yes`.
It uses the most recent same-stage real costs when available, otherwise catalogue
prices and typical service counts (about $10 for all fifteen plans, $25–30
through pictures, or $150 for the full set). Full runs also show estimated
Creatify seconds and credits; each result and the total show the actual video
seconds made. Every scenario runs the real chat in its own fresh, migrated Postgres database, with
quality checks on. All paid services are real; this command installs no fake.
`--parallel N` controls concurrent scenarios (default six).

Plans mode runs the app's real planning checks before any presenter, owner-part
picture or scene agent is made. Ordinary check failures are handed back to the
real producer to repair. The planner's shown questions take the app's approved
defaults immediately through its normal timeout path. A question without a
default uses that scenario's saved answer; otherwise the scenario stops and
names the question. It never invents an owner reply.
Every result records `owner_questions`: each asking owner notice and each producer
message containing a question, with its text. The run summary prints their total,
including stopped or failed scenarios. Replayed prefixes are not counted again. The runner never invents an answer
to a producer or scene question.

Pictures mode runs the real picture checks and their redraws, then each scene
stops at the checked picture it would film, before video
submission. Full mode makes a finished ad for every scenario. Scene steps of a scenario
run concurrently with separate database connections and their normal retry caps.
Invented owner parts are drawn and checked without waiting for owner approval.

Fixing a finished ad from an owner note is tested by note runs, not by `run`:
see "Note runs" in the [README](README.md).

When a paid service refuses us for who we are (out of money, such as fal's
"Exhausted balance"; no key; a wrong key), the run stops at once: the refused
scenario stops before its agent hears of it, every other running scenario stops
at its next paid attempt, each is saved where it was, and no new scenario starts.
A `STOP:` line names the scenario and the refusal. Top up or fix the key, then
run the same name again to resume.

At 1.5 times the estimate, no new scenario or paid attempt starts. In-flight work
may finish; collecting an already-paid clip is allowed so its output is not lost.
Actual ModelCall identities and costs are recorded in the run's shared ledger,
including billed failures, so copied rows and reused database sequence numbers
cannot change the spend. Text calls and picture attempts are paced across all
children; fal music uses at most two concurrent requests.

Each scenario saves its custom Postgres dump, every local media file, the
round-export layout and grading records for every reached stage,
answers, database/session/call identities and its result. Successful scenarios
get the `done` marker last, only after the export is saved. An export failure is
recorded with a developer notice and leaves the scenario unfinished.
A settled page checkpoint also keeps its original database
and media before later planning changes them, for later-stage replay.
Picture/full runs additionally keep a `checkpoints/plan` dump after actual shared
person/room setup and before scene pictures, including any music already made.
Music or other shared outputs the producer makes later remain in the final dump.
Its `prefix-state.json`
holds the concrete job/library rows and existing produced IDs so later replay
can distinguish earlier outputs from scene-generated caches.

`film-selections.json` records each actual pre-film selection, including job and
scene identity, the selected picture/end pair, tool arguments and complete scene,
step, fix and window snapshots. `kept-pictures.json` selects the records matching
the original finished ad's actual clips, or the terminal selected pictures of a
picture run. Note-copy scenes never overwrite those original selections. An
intentional picture stop leaves its pending Film checkpoint resumable in the
isolated saved database, without an owner failure or a pretend finished clip.

Run the same name again to skip completed scenarios and resume unfinished ones
from their newest save: where the last attempt ended (saved even when it stopped
or failed), else the last checked picture before filming (`checkpoints/latest`,
saved each time a scene reaches filming), else the plan or page checkpoint. The
restored chat carries on: unfinished tools run again, running scene steps are
handed out again, and the producer is woken. Unlike the app after a worker
restart, no step is failed, so the graded ad gets no false failure. A scenario with no save starts in a fresh database. A run started from a
baseline resumes only from its own saves, never the baseline's copied
checkpoints. To start a scenario over instead, use a new name. Earlier spend
remains in the ledger. A saved name
keeps its original app commit, selection and terminal stage; use a new name for
a changed app version or a different stage. Missing pages, unanswered questions,
crashes and spend-limit stops are saved and reported, and do not prevent the
other scenarios from finishing.

The machine needs the API credentials, Postgres with permission to create
isolated eval databases (and `max_connections` well above the default 100: at
`--parallel 15`, 3 of 30 scenarios failed with "too many clients"), `pg_dump`/`pg_restore`, and ffmpeg. The deployed site
and a Celery worker are not used. The runner's process-only page source,
execution boundary and paid-attempt observer have no setting or environment
variable that activates them in the app.


## Start from a baseline

```sh
uv run python -m evals.regression run --to plans --from plan --only ridge-wallet --name new-plan
uv run python -m evals.regression run --to pictures --from pictures --only ridge-wallet --name new-pictures
uv run python -m evals.regression run --to full --from clips --only ridge-wallet --name new-clips
```

Each selected scenario uses its own promoted entry in `baseline.json`:

| `--from` | Saved outputs kept | Work run for real |
| --- | --- | --- |
| `plan` | Page text, photos, library, videos and picker answers | Planning and every requested later stage |
| `pictures` | Page, validated plan, presenter, room and music | Scene agents from their start, pictures and requested later stages |
| `clips` | Those outputs and the pictures actually kept for the original ad | New scene-agent turns, film forms, move/clip checks and clips |

An ad's kept picture may be a redraw after a failed clip. Replay follows the
saved ad cuts and pre-film selections, including edited ancestors, end pictures,
scene fields and scene-agent windows. A picture-only baseline can supply the
terminal picture selections for `--from clips`. The agent may redraw after a
new real check fails; later-stage checks and decisions are never substituted.
Shared music made after the plan checkpoint is retained too.

The child restores the baseline dump and media into disposable storage and trims
execution and paid-call caches for the stages being replaced. The source dump,
media and original chat stay unchanged. Ledger identities and the cost override
count only new calls, including failed attempts.

A scenario without a baseline stops with `no baseline; run it from the start
first`. Missing checkpoints, kept-picture manifests or required
saved outputs stop before new paid work. A full-ad suffix also requires saved music;
a picture baseline with no music can target pictures, but needs a full baseline
before it can supply an ad suffix. `pictures` cannot target `plans`;
`clips` requires `--to full`. A key that is no longer in the
catalogue (such as a dropped picture-only scenario named by an older run) is
refused before payment.

The estimate includes only the requested suffix, using recent actual costs from
the same `--from`/`--to` combination when available. The saved run records its
`from` and pinned `baselines` mapping; each scenario's `replayed_from` records the
baseline run and stage. Continuing that name uses the same source even if a
baseline is promoted meanwhile. A new source or stage needs a new run name.

After running, follow the [complete workflow](README.md#workflow): answer, grade,
import, answer again to apply photo labels, file grades, report and promote.
