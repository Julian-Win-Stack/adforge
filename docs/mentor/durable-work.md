# Design mentor: durable work, worker & deploy

**Area:** background work and its recovery: `backend/agents/tasks.py`, the step code in
`backend/jobs/work.py`, `jobs/job_locks.py`, `stale.py`, `threads.py`, `owner_answers.py` (re-runs),
`backend/adforge/celery.py`, `settings.py`, `monitoring.py`, `deploy/`, and their tests.
**Design read:** ADR 0003, `deploy/README.md`, `docs/azure.md`, CONTEXT.md (Job, Scene step,
Checkpoint, Stale, Pause, Interrupt), spec #1 (Reliability, One ad at a time, Stack, Job handling,
What is stored), #247 Pause, #272 Part 3.
**Versions in use** (`backend/uv.lock`): Celery 5.6.3, kombu 5.6.2, Django 6.1.1, psycopg 3.3.5,
Redis 7 (image), Postgres 16, Python 3.14.

## Ranked

| # | Concept | Tag |
|---|---|---|
| 1 | Idempotent consumer (at-least-once delivery) | **must apply now** |
| 2 | Transactional outbox | **must apply now** |
| 3 | Graceful shutdown and draining | **must apply now** |
| 4 | Timeouts and deadlines | **must apply now** |
| 5 | Dead man's switch (monitoring scheduled jobs) | **must apply now** |
| 6 | Backups you have restored (3-2-1, RPO/RTO) | **must apply now** |
| 7 | Integration tests against a real broker | good to know |
| 8 | Expand/contract migrations | good to know |
| 9 | Bulkheads (queue isolation) | good to know |
| 10 | Immutable, pinned build artifacts | good to know |
| 11 | Input fingerprints (cache keys) for staleness | good to know |
| 12 | Durable execution | good to know |

Items marked **(Julian's call)** change what the app tells the shop owner, a threshold, or money.
Those need his yes on a proposal first. The rest is code structure.

---

## 1. Idempotent consumer (at-least-once delivery) · **must apply now**

**What it is**
- A queue can hand you the same message twice. Celery with Redis does this.
- An idempotent consumer makes "run twice" end the same as "run once".
- The usual tool is an atomic claim: one SQL statement that only succeeds for the first taker.

**Why it matters here**
- `agents/tasks.py:run_scene_step` claims a step with
  `filter(pk=step_id, status=RUNNING).update(taken_at=now, seen_at=now)`.
  That also succeeds for a step another process already took and is still running.
- Second copies do arrive:
  - `settings.py` sets `CELERY_TASK_ACKS_LATE = True` and no `visibility_timeout`. Redis
    hands out again any task not acked within 1 hour (Celery docs).
  - A scene agent's whole run is one task (`agents/scene_agent/__init__.py:run` loops until the
    agent ends: up to 6 pictures and 6 clips, each clip waited for "however long it takes").
    A run past 1 hour gets a second copy of the same agent running beside it.
  - `tasks.py:carry_on_after_the_worker_starts` sends every running scene-agent step again.
    Its own docstring says "Celery may hand it out again too". When Celery does, both copies
    claim the step.
- Result: two agents on one scene, both paying. This is the problem the spec set out to prevent
  (spec #1 story 55; `docs/creatify-problems.md` #7 "The same scene was produced twice").
- The producer is guarded the right way: `tasks.py:wake_producer` checks and sets
  `producer_running` under `select_for_update`. The step claim isn't guarded like that.

**How to apply it**
- Claim only an untaken step: add `taken_at__isnull=True` to the `update()` in `run_scene_step`.
  Every legitimate re-send already clears `taken_at` first
  (`carry_on_after_the_worker_starts`, `owner_answers.py:_run_again`, `owner_answers.py:run_again`),
  so nothing else changes.
- Set `CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": ...}` above the longest real step.
  The redeliveries then become rare; the claim makes them harmless.
- Later, with leases (taught concept 8): store a claim token at claim time and have `_finish` and
  `_fail` check it (a fencing token).
- Test: two threads run `run_scene_step` on the same step behind a `threading.Barrier`
  (the pattern in `tests/test_video_watcher.py`). Assert one run paid.
- No change the owner sees.

**One real post**
- Celery docs, Redis broker, "Visibility timeout": "If a task isn't acknowledged within the
  Visibility Timeout the task will be redelivered to another worker", default 1 hour.
  <https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html>

---

## 2. Transactional outbox · **must apply now**

**What it is**
- You must save a row in Postgres and also send a message to Redis. Those are two systems, and
  no single commit covers both.
- The outbox fix: the row itself records "to be sent". A relay keeps sending until a worker
  takes it. Delivery becomes at least once, which is why concept 1 comes first.

**Why it matters here**
- `agents/producer.py:_start_step` commits the `SceneStep` row, then calls
  `transaction.on_commit(run_scene_step.delay)`. There is no outer transaction, so the send runs
  right away, after the commit.
- If the send fails (Redis restarting: Celery retries for under a second, then raises) or the
  process dies first, the step stays `running`, with `taken_at` empty, forever:
  - `tasks.py:fail_dead_steps` only looks at steps with `taken_at` set.
  - The tool answers "failed", but the chat already said "Making scene N's ..."
    (`step_signals.say_started` in the same transaction).
  - `jobs/models.py:SceneStep` constraint `one_running_step_per_scene_and_kind` refuses every
    new step of that kind for the scene ("busy").
  - The whole ad stalls, not just one scene. `tasks.py:_finish` wakes the producer only when
    no other step runs (`_others_running`), and `_something_will_wake_it` sees a running step,
    so there's no nudge either.
  - After 120 s, `another_session_is_busy` stops counting it, so another session can start
    (a known gap in spec #1).
- Redis keeps the queue with its defaults (`deploy/compose.yml` `image: redis:7`, no config):
  snapshots only, no append-only file. A crash loses the last minutes of queued messages
  (Redis docs). Locally (`docker-compose.yml`) Redis has no volume at all.
- The producer already recovers this way. `wake_producer` writes `producer_running` first, and
  `restart_dead_producers` starts it again every minute. Steps have no such relay.
- `tests/test_dead_ends.py:test_a_live_or_waiting_step_is_never_failed_as_dead` pins that an
  untaken step waiting 30 minutes stays running. So the fix must send it again, not fail it.

**How to apply it**
- The `SceneStep` row already is the outbox: `running` with `taken_at` empty means "staged".
- Add a relay to the existing one-minute beat. It sends `run_scene_step` again for each running,
  untaken step not sent in the last N minutes (add a `sent_at` column). After concept 1, a
  duplicate send is harmless.
- Each re-send posts a developer notice of its own `Kind` (`jobs/notices.py`), as the
  no-silent-failures rule asks.
- In `_start_step`, a failed send after the commit should not fail the tool: the relay will
  send it. Use `on_commit(..., robust=True)` or catch it, and record the notice.
- `carry_on_after_the_worker_starts` fails untaken steps with "The worker restarted while it was
  being made", but they never started. The relay can send them again instead.
- Optional: run Redis with `--appendonly yes`, which narrows the loss to about 1 s. The relay
  is what makes it correct.

**One real post**
- Brandur Leach, "Transactionally staged job drains in Postgres": jobs staged in a table in the
  same transaction, an enqueuer sends them to Redis and deletes them only once sent.
  <https://brandur.org/job-drain>

---

## 3. Graceful shutdown and draining · **must apply now**

**What it is**
- Draining a deploy means: stop taking new work, let running work finish, then restart.
- The order matters. Check and stop must be one step, or new work slips in between them.

**Why it matters here**
- `deploy/deploy.sh:busy` reads the first line of `manage.py shell` output. Django now prints
  "N objects imported automatically" first, so it refuses with a wrong count. `docs/azure.md`
  then says "Check by hand, then `FORCE=1`". The safety check is routinely bypassed.
- Check-then-act: between `busy` saying 0 and `docker compose up -d --build`, a message to
  `chat/api.py:_send` can start an ad. The restart then kills it, and
  `carry_on_after_the_worker_starts` fails its steps.
- Any backend change restarts the worker. `backend`, `worker` and `beat` share the image
  `adforge-backend` (`deploy/compose.yml`), so a prompt or API edit drains everything.
- `stop_grace_period: 10m` is the warm-shutdown window. A scene-agent run longer than that is
  killed mid-work.

**How to apply it**
- Fix the check so it parses nothing. Either use `manage.py shell --no-imports -c ...`
  (Django 5.2+), or better a small management command that exits 0 when idle and 1 when busy.
- Add a drain switch **(Julian's call: what the owner is told)**:
  - a "draining" row that `chat/api.py:_send` checks next to `another_session_is_busy`, so no new
    ad starts;
  - `deploy.sh` sets it under `tasks.py:hold_one_at_a_time_lock`, which closes the race;
  - then it waits for busy to reach 0 (with a time limit), restarts, and clears the switch.
- Once migrations follow concept 8, the API can be deployed without restarting the worker:
  `docker compose up -d --build --no-deps backend web`. The worker then restarts only at a drain.

**One real post**
- Celery docs, "Stopping the worker": warm, soft (5.5+) and cold shutdown. Since 5.6, heartbeats
  are kept during warm shutdown, and restoring unacked messages after a hard stop is not
  guaranteed. <https://docs.celeryq.dev/en/stable/userguide/workers.html>

---

## 4. Timeouts and deadlines · **must apply now**

**What it is**
- Every wait gets an upper bound: a network call, a poll, a lock, a whole task.
- A heartbeat proves the process is alive. It does not prove the work moves. Liveness is not
  progress.

**Why it matters here**
- `tasks.py:_beating` beats from its own thread. A step whose main thread is stuck stays "seen"
  forever, so `fail_dead_steps` never fails it.
- What waits with no bound:
  - `gateway/gateway.py:collect_clip`: `while state == "working": sleep(CLIP_POLL_SECONDS)`.
    A render the provider reports as "working" forever holds the step forever.
  - `jobs/job_locks.py:one_at_a_time`: blocking `pg_advisory_lock`, held across paid work. One
    hung holder makes every waiter hang too, each still beating.
  - No Celery `task_time_limit` or `task_soft_time_limit` anywhere (`settings.py`).
- Effect: the ad stalls (one running step blocks the wake, as in concept 2), and
  `deploy.sh:busy` refuses forever, which leads back to `FORCE=1`.
- Spec #1 promised "A stuck worker is stopped before another one takes the step over". The code
  never stops a stuck worker.
- A deadline costs no money. `jobs/work.py:_clip_asked_for` already finds a clip paid for with
  the same handoff by an earlier step "that stopped or gave up", and the next try collects it
  without paying.

**How to apply it**
- Proposal **(Julian's call: the length and what the owner is told)**: each step kind gets a
  deadline. Past it, `fail_dead_steps` fails the step even though it is "seen", with a notice. The
  producer starts it again, and a paid clip is collected for free.
- Mechanism: a `deadline_at` column checked by `fail_dead_steps`, or Celery `soft_time_limit`
  per task (it raises inside the task, so it can clean up).
- `one_at_a_time`: loop on `pg_try_advisory_lock` with a deadline instead of blocking.
- Keep lock windows short. `chat/api.py:_send` saves uploaded videos while it holds the app-wide
  lock, so every other message and the worker-start cleanup wait behind the copy. Save first,
  then lock.

**One real post**
- Marc Brooker (Amazon Builders' Library), "Timeouts, retries and backoff with jitter": a
  request that waits holds "the resources it was using for that request for a longer time".
  <https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter>

---

## 5. Dead man's switch (monitoring scheduled jobs) · **must apply now**

**What it is**
- An alarm that fires when an expected signal does not arrive, such as a job that should run
  every minute and didn't.
- Error tracking can't see this: nothing failed, nothing ran.

**Why it matters here**
- All recovery runs on beat: `settings.py:CELERY_BEAT_SCHEDULE` runs `restart_dead_producers`
  and `fail_dead_steps` every 60 s. If beat stops, or the one worker can't run them, dead
  producers stay dead and killed steps stay "running". Ads stall with no notice.
- `adforge/monitoring.py:start` sets up `CeleryIntegration()` without beat monitoring.
- `deploy/compose.yml` has a healthcheck on `db` only. A hung worker, beat or Redis looks
  "running".
- `deploy/backup.sh` failing at night only writes to `/var/log/adforge-backup.log`.
- This breaks the AGENTS.md rule: no silent failures.

**How to apply it**
- `CeleryIntegration(monitor_beat_tasks=True)`. Settings start Sentry in beat and in the worker,
  which the Sentry docs require.
- The check-in is sent when the task runs on the worker. One monitor on `fail_dead_steps` checks
  beat, Redis and the worker together.
- Cost **(Julian's call)**: the free plan includes 1 cron monitor; each extra one is $0.78 a month
  (sentry.io/pricing). To stay free, keep one with `exclude_beat_tasks`, or merge the two sweeps
  into one beat task.
- Add compose healthchecks: `redis-cli ping` for Redis, `celery -A adforge inspect ping` for the
  worker.
- Give the beat entries `"options": {"expires": 60}`, so a pile of sweeps queued while the worker
  was down is dropped rather than all run at once.

**One real post**
- Sentry docs, "Celery Beat Auto Discovery": alerts on beat tasks that are missed, fail or run too
  long. <https://docs.sentry.io/platforms/python/integrations/celery/crons/>

---

## 6. Backups you have restored (3-2-1, RPO/RTO) · **must apply now**

**What it is**
- 3-2-1: three copies, on two kinds of storage, one off the machine.
- A backup counts only once you have restored from it.
- RPO is how much work you can lose. RTO is how long getting back takes.

**Why it matters here**
- `deploy/backup.sh` writes `pg_dump` to `/srv/adforge-backups` on the same VM disk. Losing the
  VM or its disk loses both the database and every backup.
- The media volume (every picture, clip and ad) is never backed up. `adforge/file_store.py:save`
  stores on local disk, and the database only holds the keys. Spec #1 "What is stored": "files
  must survive".
- Restoring has never been rehearsed (`deploy/README.md` only gives the command).
- Today's RPO: 24 h for the database, everything for media. RTO: unknown.

**How to apply it**
- Copy each night's dump and the media volume off the VM, for example to an Azure Blob
  container with `azcopy sync`. Or move `file_store` to Blob via `STORAGES`, as its docstring
  already plans.
- Cost **(Julian's call)**: Blob is billed per GB-month. Measure the media first
  (`du -sh` of the volume), then price it on Azure's page.
- Do one restore drill: restore the latest dump into a scratch database, then open a session in
  admin with its media. Write the steps in `deploy/README.md`.
- Alert when a backup doesn't land (concept 5).

**One real post**
- GitLab, "Postmortem of database outage of January 31" (2017, on Azure): the backups had failed
  silently, nobody tested restores, and hours of data were lost.
  <https://about.gitlab.com/blog/2017/02/10/postmortem-of-database-outage-of-january-31/>

---

## 7. Integration tests against a real broker · good to know

**What it is**
- Test the worker path the way it runs: a real Celery worker and a real Redis, plus real
  parallel runs, not only calls in sequence.

**How this area is tested today**
- `tests/conftest.py` sets `task_always_eager=True`. The `steps` fixture holds every
  `run_scene_step.delay` in `HeldSteps`, and tests run them by hand.
- A crash is simulated by a fake model raising `WorkerStopped`, after which the test calls
  `run_scene_step` again
  (`test_producer_scenes.py:test_a_step_run_again_after_its_worker_stopped_pays_for_nothing_twice`).
- A worker start is simulated by calling `carry_on_after_the_worker_starts()` directly
  (`test_producer_scenes.py:test_a_step_working_when_the_worker_stopped_fails_as_the_worker_starts_and_the_producer_is_told`,
  `test_scene_agent.py:test_worker_restart_keeps_the_scene_chat_and_does_not_repeat_paid_work`).
- A dead step is simulated by moving `taken_at`/`seen_at` back in time, then calling
  `fail_dead_steps()` (`test_dead_ends.py`).
- A redelivery is tested only after the step finished
  (`test_a_step_handed_out_again_once_it_has_finished_is_left_alone`).
- Real threads appear only in the pause race (`test_pause.py`) and in some lock tests
  (`test_recolour.py`, `test_video_watcher.py`).

**Why it matters here**
- Never exercised: a second delivery while the first runs (concept 1), a send failing after
  commit (concept 2), the real `acks_late`, visibility timeout and `worker_ready` order.
- CI (`.github/workflows/ci.yml`) has Postgres but no Redis.
- Spec #1 Testing promised "Concurrency tests start the same scene step twice and check that only
  one produces it". No such test exists.
- Celery's docs say eager mode is "by definition not suitable".

**How to apply it**
- Write the two-thread test with concept 1.
- Write a `.delay` raising test with concept 2.
- Add one `slow` smoke test with Celery's `celery_worker` fixture against a `redis:7` service
  in CI.

**One real post**
- Celery docs, "Testing with Celery": eager mode only emulates a worker; `celery_worker` starts a
  real one for integration tests. <https://docs.celeryq.dev/en/stable/userguide/testing.html>

---

## 8. Expand/contract migrations · good to know

**What it is**
- Every migration must work with the code from before it.
- Expand: add columns as nullable or with a database default.
- Contract: drop or rename only a deploy later.

**Why it matters here**
- `deploy/compose.yml` runs `migrate` as `backend` starts, while the old worker may still be
  finishing for up to 10 minutes and old beat keeps calling it.
- The rule was met once by hand: `agents/models.py:SystemNote.assembled` has `db_default=False`,
  "so a worker still running code from before it can add a note while the new code is being
  rolled out".
- Needed before concept 3's "deploy the API without restarting the worker".

**How to apply it**
- Make it a written rule and add a CI check that flags `RemoveField` and `RenameField` in a new
  migration.
- Retire a field in the code first, and drop its column a deploy later.

**One real post**
- PostHog handbook, "Safe Django migrations": "Never run a migration that drops, renames, or
  removes anything while any running code could still reference it."
  <https://posthog.com/handbook/engineering/safe-django-migrations>

---

## 9. Bulkheads (queue isolation) · good to know

**What it is**
- Separate queues and slots for different kinds of work, so one kind can't starve another.

**Why it matters here**
- Producer turns, every scene step and the one-minute sweeps share Celery's default queue and the
  one worker's 48 slots (`deploy/compose.yml` `WORKER_CONCURRENCY`).
- `tasks.py:_work_until_it_stops` sleeps 30 s, then 120 s, inside a slot.
- Steps waiting on clips each hold a whole prefork process (up to 1.5 GB each,
  `--max-memory-per-child`).
- If the slots fill, the owner's reply and `fail_dead_steps` wait behind renders.
- Rare now, with one ad at a time. It matters once that rule goes.

**How to apply it**
- `CELERY_TASK_ROUTES`: producer, steps and sweeps each get their own queue.
- Real isolation needs a second small worker for producer and sweeps, which needs leases
  (taught concept 8) first.
- Waits that only poll could run on a thread pool instead of processes.

**One real post**
- Amazon Builders' Library, "Avoiding insurmountable queue backlogs": separate queues and
  per-workload limits so "no customer's workload impacts another".
  <https://builder.aws.com/content/3EuRcgkTP1MI0c7zM8W6HL3WIqA/avoiding-insurmountable-queue-backlogs>

---

## 10. Immutable, pinned build artifacts · good to know

**What it is**
- Build once, with exact versions, and keep the image you ran, so rolling back means switching
  back, not rebuilding.

**Why it matters here**
- `deploy/backend.Dockerfile` copies `ghcr.io/astral-sh/uv:latest`.
- `deploy/compose.yml` uses floating tags: `postgres:16`, `redis:7`, `caddy:2`, `node:24`,
  `python:3.14-slim`.
- ffmpeg comes from Debian through the base image. A base bump can change how ads render, and the
  tests replay ffmpeg (`tests/ffmpeg_replay.py`), so they won't notice.
- `deploy/deploy.sh` rolls back by rebuilding an old commit, which may pull newer layers than
  last time.

**How to apply it**
- Pin `uv` and the base images by version or digest, and let Dependabot (`docker`) raise PRs to
  bump them.
- Before `up --build`, tag the running image (`adforge-backend:previous`), so rollback is a free
  re-tag.

**One real post**
- Docker docs, "Building best practices", pin base image versions: "Image tags are mutable".
  <https://docs.docker.com/build/building/best-practices/>

---

## 11. Input fingerprints (cache keys) for staleness · good to know

**What it is**
- Store one hash of everything a piece of work was made from. The work is stale when the hash of
  today's inputs differs. Build systems work this way.

**Why it matters here**
- `jobs/stale.py:is_stale` compares hand-listed columns: `jobs/models.py:SceneStep.snapshot`,
  `BROLL_FIELDS`, `CLIP_ONLY_INPUTS`.
- Each new input needs a new `SceneStep` column, a list entry and a migration (comments show
  #223, #247 point 9 and ticket #256 each added one). Miss one, and an old clip is silently never
  stale.
- The gateway uses a second definition of "same inputs": the whole handoff
  (`jobs/work.py:_clip_asked_for`, `handoff=handoff.model_dump()`).

**How to apply it**
- Keep Julian's per-kind rules for what makes what stale.
- Store a per-kind fingerprint of the snapshot dict on each step.
- Add one test that fails when a handoff field isn't covered by its kind's snapshot.

**One real post**
- Bazel docs, "Remote caching": an action is keyed by a hash of its inputs, command and
  environment, and inputs it doesn't track cause wrong cache hits.
  <https://bazel.build/remote/caching>

---

## 12. Durable execution · good to know

**What it is**
- A workflow engine records each finished step's result and, after a crash, replays the workflow,
  returning recorded results instead of re-running them. Steps must be idempotent.

**Why it matters here**
- ADR 0003 hand-builds this:
  - `agents/models.py:ToolCall` is the step checkpoint;
  - `SceneStep` is a child workflow;
  - `tasks.py:carry_on_after_the_worker_starts` and `fail_dead_steps` are the recovery;
  - `owner_answers.py:run_again` is a retry cap.
- Each new long flow means more heartbeat and recovery code (concepts 1, 2 and 4 are gaps in it).

**How to apply it**
- No rewrite now. Use the shared words in ADRs: workflow, step, checkpoint, recovery.
- Before the next long flow, write an ADR weighing a Postgres-only engine (DBOS for Python)
  against more hand-built code.

**One real post**
- DBOS docs, "Architecture": "Every workflow input and step output is durably stored in the
  system database", and recovery resumes "from the last completed step".
  <https://docs.dbos.dev/architecture>

---

## Also seen here

- **1. Stopping conditions:** `agents/tasks.py:_nudge` (`PRODUCER_MOST_NUDGES` = 2 per owner
  message), `_work_until_it_stops` (2 safety-filter retries), `jobs/owner_answers.py:run_again`
  (`MOST_RUNS_AGAIN` = 1 re-queue after a worker stop).
- **7. Explicit state machine:** `SceneStep.Status` is written from `agents/tasks.py:_finish`,
  `_fail`, `_ask_back` (a question stored as `failed`), `_end_keeping_the_clip`,
  `agents/pause.py:pause` (`cancelled`) and `jobs/owner_answers.py:_run_again` (back to `running`).
  `stopped` is defined in `jobs/models.py:SceneStep.Status` and never set.
- **8. Leases instead of "one worker, always":**
  - `agents/tasks.py:carry_on_after_the_worker_starts`: "There is one worker, so as it starts
    nothing from before can still be running";
  - `deploy/compose.yml` worker comment "ONE worker, always";
  - `agents/tasks.py:fail_dead_steps` is a heartbeat with no owner id the claim checks
    (see concept 1).
- **10. Atomic counters:** `jobs/owner_answers.py:run_again` bumps `runs_again` inside a JSON field
  under `select_for_update`. That is safe by row lock; `F()` can't reach into JSON.

Owned by another area: ADR 0003's gap of "a worker can stop after paying and before writing"
for a submitted clip is a case for idempotency keys at the paid-API boundary (gateway mentor).
