## Code answers

`uv run python -m evals.regression answer <run> [--only <scenario> ...]` restores
each selected scenario's dump into a disposable database and rewrites only its
`answers.json`. It needs the configured `DATABASE_URL` account to create databases,
and `pg_dump` / `pg_restore` on `PATH`. Stored dumps, media and run records stay
unchanged. Restored databases are migrated for the current app and removed after
the answer child exits. No model is called.

The runner calls `answers.answer_job(job, *, scenario, run, to="full", kind="full",
photo_labels=None, notes=(), model_call_ids=None, cost_usd=None)`. `photo_labels`
is that scenario's hash-to-label mapping. Pending photo items include their byte
hash as `photo-<sha256>` and an optional `file` key for the grading page.
`NoteComparison(before, after, sent_at)` names the finished ads around a note.
Pass the **actual newly charged ModelCall ids**, including new work on note copies,
or an explicitly tracked `cost_usd`; replayed and copied rows must be excluded.
Without either, the standalone initial-run helper sums this job's calls.

Alongside each `db.dump`, the runner saves `scenario.json`:

```json
{
  "job_id": "the original scenario job UUID",
  "session_id": "the original chat UUID (optional)",
  "kind": "full",
  "model_call_ids": [1, 2, 3],
  "notes": [{"before_ad": 20, "after_ad": 35, "sent_at": "2026-11-12T00:00:00+00:00"}]
}
```

Shared database helpers are `database.isolated_database(source=None,
connection_url=None)` (context manager yielding a fresh database URL),
`dump_database(url, into)` (atomic custom-format dump),
`url_for_database(name, connection_url=None)` and `child_environment(url,
media_dir)`. Only the generated `reg_eval_<uuid>` database is dropped. A running
Django process is never pointed at another database.
