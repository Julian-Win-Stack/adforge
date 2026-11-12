# Run the fixed scenarios

From `backend/`, choose the lasting store with `--store <dir>` (before or after
the subcommand) or `EVAL_STORE`. Save the scenarios' pages first with `save-pages`.
The runner uses those frozen page and media files; it never reads the live shop.

```sh
uv run python -m evals.regression run --to plans --name after-change
uv run python -m evals.regression run --to plans --only ridge-wallet --name ridge-plan --yes
```

The estimate is printed before any paid work. Type `yes` to start, or pass `--yes`.
It uses the most recent same-stage real costs when available, otherwise catalogue
prices and typical text-call counts (about $20–30 for all thirty plans). Every
scenario runs the real chat in its own fresh, migrated Postgres database, with
quality checks on. All paid services are real; this command installs no fake.
`--parallel N` controls concurrent scenarios (default six).

Plans mode runs the app's real planning checks before any presenter, owner-part
picture or scene agent is made. Ordinary check failures are handed back to the
real producer to repair. The planner's shown questions take the app's approved
defaults immediately through its normal timeout path. A question without a
default uses that scenario's saved answer; otherwise the scenario stops and
names the question. It never invents an owner reply.

At 1.5 times the estimate, no new scenario or paid attempt starts. In-flight work
may finish; collecting an already-paid clip is allowed so its output is not lost.
Actual ModelCall identities and costs are recorded in the run's shared ledger,
including billed failures, so copied rows and reused database sequence numbers
cannot change the spend. Text calls and picture attempts are paced across all
children; fal music uses at most two concurrent requests.

Each scenario saves its custom Postgres dump, every local media file, the
round-export layout and grading records for all reached page/plan outputs,
answers, database/session/call identities and its result. Successful scenarios
get the `done` marker last, only after the export is saved. An export failure is
recorded with a developer notice and leaves the scenario unfinished.
A settled page checkpoint also keeps its original database
and media before later planning changes them, for later-stage replay.

Run the same name again to skip completed scenarios and start unfinished ones
in fresh databases. Their earlier spend remains in the ledger. A saved name
keeps its original app commit, selection and terminal stage; use a new name for
a changed app version or a different stage. Missing pages, unanswered questions,
crashes and spend-limit stops are saved and reported, and do not prevent the
other scenarios from finishing.

The machine needs the API credentials, Postgres with permission to create
isolated eval databases, `pg_dump`/`pg_restore`, and ffmpeg. The deployed site
and a Celery worker are not used. The runner's process-only page source,
execution boundary and paid-attempt observer have no setting or environment
variable that activates them in the app.
