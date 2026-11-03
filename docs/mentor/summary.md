# Design mentor: the whole repo, one learning plan

What AdForge's design is missing, across 8 areas plus the scene agents, merged into one plan for
Julian. Teaching only: nothing in the app changed. Applying any concept is a design change and
needs Julian's yes first (design-mentor skill, step 3).

- **Method:** `.claude/skills/design-mentor/SKILL.md` steps 2–3 in area mode, one mentor per area.
  An area's design was its ADRs, plan docs, `CONTEXT.md` entries and closed `Spec:` issues
  (#1, #210, #247, #272, #273).
- **Code as of:** `bfc4dfc` (main, 2026-11-03). Backend paths below are under `backend/`.
- **Area reports:** each holds the full What / Why / How / post for every concept.

| Key | Area | Report | Concepts | Must apply now |
|---|---|---|---|---|
| PL | Producer & agent loop | [producer-loop.md](producer-loop.md) | 8 | 4 |
| DW | Durable work, worker & deploy | [durable-work.md](durable-work.md) | 12 | 6 |
| GW | Gateway, paid services & money | [gateway.md](gateway.md) | 6 | 2 |
| PG | Reading the page & the library | [page-library.md](page-library.md) | 10 | 5 |
| PT | Planning & talking scenes | [planning-talking.md](planning-talking.md) | 11 | 6 |
| CE | Checks & evals | [checks-evals.md](checks-evals.md) | 11 | 6 |
| AC | Assembly & scene cards | [assembly-cards.md](assembly-cards.md) | 15 | 6 |
| CF | Chat, API & frontend | [chat-frontend.md](chat-frontend.md) | 18 | 7 |
| SA | Scene agents (taught before, PR #324) | [scene-agents.md](scene-agents.md) | 10 | — |

91 area concepts merge into **77 new concepts**, plus the **10 already taught**: 87 in all,
each listed once below.

---

## Learn first

In this order. Each one makes the next easier.

1. **Idempotency keys and idempotent consumers.** The top risk and a habit in 4 areas. Every
   retry, resend and restart in AdForge is only safe once this holds; the rest of the
   reliability list assumes it.
2. **Timeouts, deadlines and retry budgets.** A habit in 3 areas. Retrying is only safe after
   #1, and today nothing in the app has an upper bound on how long it waits.
3. **Transactional outbox.** Builds on 1 and 2. Postgres plus Redis is a write to two systems
   with no shared commit, and a send that never arrives stalls a whole ad today.
4. **Short transactions (no network call while holding a lock).** A habit in 4 areas and an
   hour of Postgres locking to learn. It explains frozen job rows and every send waiting behind
   an upload.
5. **Explicit state machine** (taught). Still the most repeated habit: 8 areas. It is the root
   of the stuck-session-after-assembly bug and the "working" dots that never stop.
6. **Append-only, immutable records.** A habit in 3 areas. It is behind the steering-order bug
   in the producer's history and the chat that reads differently after a reload.
7. **Golden dataset.** The first AI-engineering step. Every eval number (judge, fact check,
   regression) needs one, and today none can be reproduced from the repo.
8. **Grounded extraction.** ADR 0002 promises facts only from the page, and the price is the
   one fact that isn't checked against it. Data provenance (#23) is the same lesson for the
   video transcript.
9. **Contract tests.** A habit in 3 areas. It is why 163 frontend tests and the gateway's
   fakes stay green while the real services and the real API drift.

---

## Habits (seen in 3 or more areas)

Counted from each area's concepts and its "Also seen here" lines, plus the scene-agent list.

**Taught with the scene agents, and still repeated everywhere:**

| Concept | Areas | Count |
|---|---|---|
| Stopping conditions | SA PL GW CF CE PG PT DW | 8 |
| Explicit state machine | SA PL GW CF PG PT DW AC | 8 |
| Calibrating an LLM judge | SA PL CF CE PG PT | 6 |
| Agent evals on a real model | SA PL CE PG PT | 5 |
| Stable prompt prefix | SA PL GW PG PT | 5 |
| Leases instead of "one worker, always" | SA GW CF PG DW | 5 |
| App-side rate limiter | SA GW CE PG PT | 5 |
| Atomic counters | SA CF PG DW AC | 5 |
| Independent evidence for checks | SA CE PG PT | 4 |
| Precision vs. recall | SA CE PG PT | 4 |

**New this run:**

| Concept | Areas | Count |
|---|---|---|
| Idempotency keys and idempotent consumers | GW CF DW AC | 4 |
| Short transactions (no network call under a lock) | PG CF DW GW | 4 |
| Timeouts, deadlines and retry budgets | GW DW CF | 3 |
| Append-only, immutable records | PL CF AC | 3 |
| Contract and integration tests against the real thing | GW CF DW | 3 |

- Every one of the 10 taught concepts showed up again in 2 or more other areas. Learning them
  once for the scene agents didn't carry over to the rest of the code. Use the list as a
  checklist on every new design.
- Short transactions: PG and CF teach it as a concept. DW (`jobs/job_locks.py:one_at_a_time`,
  `chat/api.py:_send`) and GW (`gateway/gateway.py:_one_azure_picture_at_a_time`) flag the same
  lock-across-a-network-call under other headings.

---

## Top 10, checked against the code

All 10 are **must apply now**. I opened every cite below and confirmed the claim.

**1. Idempotency keys and idempotent consumers** · GW CF DW AC · habit
- Goes wrong: paid work is paid twice or lost, and two scene agents can run on one scene.
- Cites:
  - `gateway/gateway.py:_recorded` writes the `ModelCall` only after `make()` returns.
  - `gateway/creatify_adapter.py:CreatifyProvider._send` turns a lost reply into `ClipFailed`,
    so a clip that may be made and charged is never collected.
  - `gateway/heygen_adapter.py:HeyGenProvider._send` raises `OutsideServiceDown` on any 5xx,
    so `adforge/retry.py:with_retries` sends the paid POST again.
  - `gateway/openai_adapter.py:_asked` retries `APITimeoutError`, a subclass of
    `APIConnectionError`.
  - `agents/tasks.py:run_scene_step` claims with `filter(pk, status=RUNNING).update(...)`,
    which a second delivery also passes.
  - `chat/api.py:_send` has no idempotency key.
  - `jobs/cut_and_send.py:_say` dedupes notices by exact text (AC).

**2. Transactional outbox** · DW
- Goes wrong: a step whose send never reached Redis stays `running` forever, and the ad stalls
  with no notice.
- Cites:
  - `agents/producer.py:_start_step` commits the step, then `transaction.on_commit(run_scene_step.delay)`.
  - `agents/tasks.py:fail_dead_steps` only looks at steps with `taken_at` set.

**3. Timeouts, deadlines and retry budgets** · GW DW CF · habit
- Goes wrong: a stuck step, poll or lock waits forever, and the retry layers multiply.
- Cites:
  - `gateway/gateway.py:collect_clip`: `while ... == "working": sleep` with no deadline.
  - `agents/tasks.py:_beating` beats from its own thread, so a stuck step stays "alive".
  - `jobs/job_locks.py:one_at_a_time`: a blocking `pg_advisory_lock`.
  - `adforge/retry.py:with_retries`: 5 tries, sleeping 5/20/60/120 s.
  - `gateway/gateway.py:_remade_past_the_filter` wraps it.
  - No Celery time limit in `adforge/settings.py`.
  - `frontend/src/api.ts:getMessagesSince`: `fetch` with no timeout, so "Lost contact" never
    shows (CF).

**4. Grounded extraction (check the price against the page)** · PG
- Goes wrong: an ad can say a price the page doesn't, because the price is never compared with
  the page, and the fact check checks it against itself.
- Cites:
  - `jobs/work.py:copy_page_text` appends `page_text.prices(...)` after `match_back`, unchecked.
  - `jobs/page_text.py:record_prices` reads Firecrawl's record only.

**5. Failure isolation (poison messages)** · AC
- Goes wrong: after a card swap, an assembly failure that repeats stops the producer on every
  later owner message.
- Cites:
  - `agents/tasks.py:_work_on` calls `card_picks.assemble_after_swaps` before `loop.run`.
  - `agents/producer.py:assembled_again` catches only `Refused` and `Failed`.
  - `jobs/assembly.py:AssemblyFailed` is a plain `Exception`.
  - `agents/card_picks.py:assemble_after_swaps` raises before setting `note.assembled`.

**6. Cursor sync with a high-water mark** · CF
- Goes wrong: a notice posted between the last poll and the owner's send never shows until a
  reload. CF's mentor reproduced it in a scratch copy.
- Cites:
  - `frontend/src/Chat.tsx:Chat`: `send` calls `show([sent.message])`, and `show` moves
    `lastSeq` past messages it never got.

**7. Backups you have restored** · DW
- Goes wrong: losing the VM loses the database, every backup and every picture, clip and ad.
- Cites:
  - `deploy/backup.sh` writes `pg_dump` to `/srv/adforge-backups` on the same VM.
  - The `media` volume (`deploy/compose.yml`) is never copied.
  - `adforge/file_store.py:save` stores on local disk.

**8. Groundedness eval for the fact check** · CE
- Goes wrong: the only guard against false claims in a paid ad has never been measured, and the
  runs already recorded misses.
- Cites:
  - `jobs/work.py:_ask_fact_check` and `jobs/checks.py:FACT_CHECK_INSTRUCTIONS`.
  - `docs/decision-map.md`: "The fact check is not measured."
  - No eval in `evals/` scores it.

**9. Golden dataset** · CE
- Goes wrong: no judge number can be re-run from a checkout.
- Cites:
  - `evals/calibrate_picture_check.py:load_cases` reads 62 cases. All 62 pictures sit under
    `/mnt/project-files/`, and none exist in this checkout (checked).
  - `evals/results.py` scores "following evals/clip-judge-v2/score.py (project files)".

**10. Short transactions (no network call while holding a lock)** · PG CF DW GW · habit
- Goes wrong: the Job row stays locked through vision calls and their retries, so the other
  videos and every notice wait.
- Cites:
  - `jobs/watcher.py:_keep` opens `transaction.atomic()`.
  - `library.add_to_library` takes `select_for_update` on the Job.
  - `jobs/work.py:save_video_pictures` → `keep_photo(has_face=None)` → `note_face` →
    `call_model`, all inside it.
  - Same shape: `chat/api.py:_send` copies uploads while holding the app-wide lock (CF).

### Corrections made while checking

- **#5:** the mentor wrote that the session is stuck "until the code changes". That holds only
  for a failure that repeats on the same inputs, such as the clip-shape failure in #16. A
  one-off ffmpeg error clears on the next message.
- **#3:** "up to 30 attempts" for one call is the worst case: safety-filter remakes and an
  unusable reply on top of retries. The plain case is 5 tries and about 3.5 min of sleeping.
- **#22 (graceful shutdown):** the broken `deploy/deploy.sh:busy` check is already a known bug
  in `docs/azure.md`. It stays must apply now, marked known.
- **Nothing dropped:** every top-10 claim held in the code.

---

## The whole ranked list

Ranked by impact on this build. Each concept appears once, with every area it showed up in.
"Taught" means it was taught with the scene agents, and the cites are where it shows up now.

### Must apply now

| # | Concept | Areas | Where (file:function) |
|---|---|---|---|
| 1–10 | The top 10 above | | |
| 11 | Regression evals (eval-gated judge and model changes) | CE | `jobs/clip_judge.py:wording_version`; `tests/test_frozen_rules.py:test_everything_the_clip_judge_is_told_is_the_version_its_rulings_name` |
| 12 | Append-only, immutable records · habit | PL CF AC | `agents/loop.py:_conversation`; `agents/page_panel.py:_refuse`, `_keep`; `chat/api.py:MessageSerializer.get_tries`; `jobs/work.py:bring_back_clip`; `jobs/card_versions.py:shown` |
| 13 | Single source of truth for server state | CF | `frontend/src/Chat.tsx:Chat` (`lastSaid`); `agents/loop.py:say_it_stopped` |
| 14 | Cache freshness (TTL) for facts | PG | `jobs/firecrawl.py:read_page`, `product_record` |
| 15 | Aggregate invariants (one rule check on every write) | PT | `jobs/planning.py:Plan._opens_on_the_person`; `jobs/plan_edits.py:reorder`, `_check` |
| 16 | Canonical intermediate format | AC | `jobs/assembly.py:join` |
| 17 | Over-optimizing against an imperfect judge (Goodhart) · known | CE | `jobs/clip_judge.py:judge_clip`; `jobs/auto_fix.py:clip_to_keep` |
| 18 | Anchoring bias (a blind second opinion) | PL CE | `agents/look.py:_version_shown`, `_try_of`; `evals/grading_page.py:_verdict`; `evals/results.py:load_run` |
| 19 | Stopping conditions (taught): no dollar cap · known | SA PL GW CF CE PG PT DW | `agents/loop.py:settle`; `agents/money.py:cost_of`; `scripts/ad-runs/run.py:spent`; `jobs/watcher.py:_past_the_filter` |
| 20 | Cost reconciliation (recorded cost vs the bill) | GW | `gateway/catalog.py:video_cost_usd`, `cost_usd`; `gateway/creatify_adapter.py:CreatifyProvider.status` |
| 21 | Dead man's switch (monitor scheduled jobs) | DW | `adforge/monitoring.py:start` |
| 22 | Graceful shutdown and draining · known | DW | `deploy/deploy.sh:busy` |
| 23 | Data provenance (a transcript is model output) · known | PG | `jobs/watcher.py:_keep`; `jobs/work.py:_video_transcripts`; `jobs/checks.py:FACT_CHECK_INSTRUCTIONS` |
| 24 | Explicit state machine (taught) | SA PL GW CF PG PT DW AC | `agents/tasks.py:_something_will_wake_it`; `jobs/work.py:_clip_asked_for`; `frontend/src/scenes.ts:sceneCards`; `jobs/watcher.py:watch_videos`; `jobs/models.py:Scene.change_line`; `agents/producer.py:assemble_the_ad` |
| 25 | Context budget (context rot) | PL | `agents/fold.py:fold_point`; `agents/loop.py:_as_given` |
| 26 | Passing reasoning items back across tool calls | PL | `gateway/openai_adapter.py:take_turn`, `_conversation` |
| 27 | Confidence intervals on eval scores | CE | `evals/results.py:_judge_lines`; `evals/ai_checks.py:Score.line` |
| 28 | Perceptual hashing's colour blind spot | PG | `jobs/photos.py:dhash`, `Copies`; `agents/page_panel.py:_take` |
| 29 | Loudness normalisation (EBU R128, LUFS) | AC | `jobs/assembly.py:join` (`MUSIC_VOLUME`) |
| 30 | Post-render quality control | AC | `jobs/work.py:assemble_ad` |
| 31 | Safe zones | AC | `jobs/assembly.py:write_text_to_draw`, `join` |
| 32 | Error boundaries and browser error tracking | CF | `frontend/src/ScenePanel.tsx:ScenePanel`; `adforge/monitoring.py:start` |
| 33 | Contract and integration tests against the real thing · habit | GW CF DW | `gateway/fake.py:FakeModel`; `gateway/inworld_adapter.py:_account_full`; `frontend/src/App.test.tsx:fakeBackend`; `tests/conftest.py:HeldSteps` |
| 34 | Calibrating an LLM judge (taught) | SA PL CF CE PG PT | `agents/look.py:look_at`; `agents/card_picks.py:use`; `jobs/cut_out.py:check_cut_out`; `evals/calibrate_ai_checks.py:main` |
| 35 | Agent evals on a real model (taught) | SA PL CE PG PT | `agents/producer.py:PRODUCER`; `jobs/photos.py:PICK_INSTRUCTIONS`; `evals/owner_notes/__main__.py` |
| 36 | Text normalization before length estimates | PT AC | `jobs/work.py:_measure_voice`; `jobs/checks.py:count_words`; `jobs/assembly.py:timed_script` |
| 37 | Constrained decoding (put the rule in the schema) | PT PG | `jobs/planning.py:producer_decision_for`; `jobs/scenes.py:starting_picture_choice_for`; `gateway/gateway.py:call_model`; `jobs/watcher.py:_asked` |
| 38 | Tagged unions (make illegal states unrepresentable) | PT CF | `jobs/planning.py:ScriptScene.check_broll_details`; `jobs/models.py:Scene.change_line`; `chat/models.py:Message`; `frontend/src/Chat.tsx:isFinishedAd` |
| 39 | Prompt decomposition (prompt chaining) | PT | `jobs/planning.py:PLAN_INSTRUCTIONS`; `jobs/work.py:plan` |
| 40 | Text measurement (fit by rendered width) | AC | `jobs/planning.py:PlannedScene._a_few_words`; `jobs/assembly.py:captions` |
| 41 | ASR round-trip check (WER with a shared normalizer) | PT | `agents/producer.py:_heard_otherwise`, `MakeClip.run` |
| 42 | Leases instead of "one worker, always" (taught) | SA GW CF PG DW | `agents/tasks.py:carry_on_after_the_worker_starts`, `another_session_is_busy`; `gateway/gateway.py:_one_azure_picture_at_a_time`; `jobs/library_items.py:add_kept_photos` |
| 43 | Independent evidence for checks (taught) | SA CE PG PT | `jobs/clip_judge.py:sent_references`; `jobs/product_only.py:_cut`; `jobs/face_swap.py:_check` (done right) |
| 44 | Stable prompt prefix (taught) | SA PL GW PG PT | `gateway/openai_adapter.py:OpenAIProvider.take_turn`; `gateway/catalog.py:cost_usd`; `jobs/product_only.py:_helpers_for`; `gateway/gateway.py:call_model` |
| 45 | Atomic counters (taught) | SA CF PG DW AC | `jobs/work.py:save_video_pictures`, `assemble_ad`; `jobs/product_videos.py:keep_owner_video`; `chat/messages.py:add`; `jobs/owner_answers.py:run_again` |
| 46 | App-side rate limiter (taught) | SA GW CE PG PT | `gateway/gateway.py:_wait_for_azures_pace`; `jobs/firecrawl.py:scrape`; `jobs/watcher.py:watch_videos`; `jobs/voices.py:designed_voice` |
| 47 | Precision vs. recall (taught) | SA CE PG PT | `jobs/clip_judge.py:MOTION_CHECK`; `jobs/photos.py:PICK_INSTRUCTIONS`; `jobs/work.py:judge_starting_picture` |

### Good to know

| # | Concept | Areas | Where (file:function) |
|---|---|---|---|
| 48 | Webhooks instead of polling | GW | `gateway/gateway.py:collect_clip` |
| 49 | Circuit breaker | GW | `gateway/gateway.py:_picture_made`, `_refused_for_money_before` |
| 50 | Recitation (the agent keeps its own task list) | PL | `agents/snapshot.py:ad_as_it_stands`; `agents/tasks.py:_whats_left` |
| 51 | Double-texting strategy | PL | `agents/loop.py:_work`, `_held_since_the_user_spoke` |
| 52 | Tool masking (`allowed_tools`) | PL | `agents/producer.py:PRODUCER`; `agents/loop.py:_held_by_the_pause` |
| 53 | Cost-sensitive decision threshold | CE | `jobs/clip_judge.py:MOTION_CHECK` |
| 54 | Selective prediction (abstention) | CE | `evals/results.py:_judge_lines`; `jobs/clip_judge.py:judged` |
| 55 | Intra-rater reliability | CE | `evals/grading_page.py:_scene_card` |
| 56 | Position bias in pairwise judges | CE | `jobs/clip_judge.py:_frames_shown`; `jobs/fix_check.py:settle_all` |
| 57 | Native video input vs frame sampling | CE | `jobs/clip_judge.py:cut_pictures`, `_kept` |
| 58 | Feedback-driven retries (re-ask with the error) | PG PT | `jobs/watcher.py:_asked`; `jobs/cut_out.py:redraw_with_check`; `jobs/checked_pictures.py:made_and_checked` |
| 59 | Pixel-preserving segmentation · deferred in #273 | PG | `jobs/product_only.py:_cut`; `jobs/recolour.py:recolour` |
| 60 | Normalization (one row per real thing) | PT | `jobs/owner_parts.py:_sharing`, `_give`, `_shows` |
| 61 | Identity consistency check (face embeddings) | PT | `jobs/face_swap.py:_check`; `jobs/work.py:person_and_room` |
| 62 | Human-in-the-loop approval as a structured action | PT | `agents/producer.py:ApproveOwnerPart.run`; `jobs/owner_parts.py:approve` |
| 63 | Content-addressed keys (input fingerprints) | DW AC | `jobs/stale.py:is_stale`; `jobs/models.py:SceneStep.snapshot`; `agents/producer.py:assemble_the_ad`; `jobs/cards.py:card_key` |
| 64 | Optimistic concurrency control | AC | `chat/api.py:use_version`; `jobs/card_versions.py:use` |
| 65 | Bulkheads (queue isolation) | DW | `agents/tasks.py:_work_until_it_stops` |
| 66 | Expand/contract migrations | DW | `agents/tasks.py:_nudge`; `agents/pause.py:pause`; `agents/card_picks.py:assemble_after_swaps` |
| 67 | Immutable, pinned build artifacts | DW | config only: `deploy/backend.Dockerfile`, `deploy/compose.yml`, `deploy/deploy.sh` |
| 68 | Durable execution | DW | `agents/tasks.py:carry_on_after_the_worker_starts` |
| 69 | A/V sync tolerance budget | AC | `jobs/assembly.py:cuts` |
| 70 | Property-based testing | AC | `jobs/assembly.py:captions`, `cuts`, `timed_script` |
| 71 | Forced alignment | AC | `jobs/assembly.py:timed_script` |
| 72 | Caption chaining (minimum gap and duration) | AC | `jobs/assembly.py:captions` |
| 73 | Typed records at the storage boundary (tolerant reader) | AC | `jobs/models.py:ProducedItem.cuts`; `agents/producer.py:assemble_the_ad` |
| 74 | Temporal grounding in video | PG | `jobs/product_videos.py:cut_pieces` |
| 75 | SSRF defence (egress proxy) | PG | `jobs/page.py:check_where_it_points`; `jobs/product_videos.py:_player_file` |
| 76 | Indirect prompt injection | PG | `jobs/photos.py:handoff`; `jobs/work.py:copy_page_text` |
| 77 | CSRF with ambient credentials, and real auth later | CF | `chat/api.py:sessions`, `session_messages` |
| 78 | N+1 queries and query-count tests | CF | `chat/api.py:scenes`; `jobs/cards.py:card_url` |
| 79 | Rows, not a growing JSON array | CF | `jobs/notices.py:post_notice`, `asked_since` |
| 80 | Direct-to-storage uploads | CF | `chat/api.py:_send`; `adforge/file_store.py:save_upload` |
| 81 | Optimistic updates | CF | `frontend/src/Chat.tsx:Chat` (`send`) |
| 82 | A server-state library (TanStack Query) | CF | `frontend/src/ScenePanel.tsx:ScenePanel`; `frontend/src/App.tsx:App` |
| 83 | Server-sent events | CF | `chat/api.py:session_messages`; `chat/messages.py:add` |
| 84 | Streaming agent output to the chat | CF | `agents/loop.py:_work` |
| 85 | State colocation and memoized lists | CF | `frontend/src/Chat.tsx:Chat` (`draft`) |
| 86 | Unfaithful explanations (the `why` isn't the cause) | PL | `agents/loop.py:WHY`; `evals/agent_page.py:_call` |
| 87 | Semantic deduplication | PT | `jobs/planning.py:same_action`; `jobs/plan_edits.py:_check_the_actions` |

### How concepts were merged

- **Idempotency:** GW 1 (paid requests), CF 4 (POSTs), DW 1 (idempotent consumer) and AC 15
  (notice dedupe) are one idea: a repeat must not do the work twice.
- **Timeouts:** GW 4 (retry budgets), DW 4 (timeouts and deadlines) and CF 7 (poll timeouts and
  backoff). GW 3's "bounded wait" is the same fix for `collect_clip`; its webhook half stays #48.
- **Short transactions:** PG 5 and CF 8.
- **Append-only, immutable records:** PL 4 (the producer's history), CF 3 (chat rows changed
  after numbering) and AC 8 (versions as copies with recomputed numbers).
- **Contract and integration tests:** GW 6 (real services), CF 6 (frontend vs backend) and
  DW 7 (a real broker). All three check a fake against the real thing.
- **Pairs:** PL 3 + CE 4 (anchoring); PT 3 + CF 9 (tagged unions); PG 7 + PT 9 (feedback-driven
  retries); DW 11 + AC 7 (content-addressed keys); PT 5 + PG 7's schema half (constrained
  decoding); PT 4 + AC 12's number step (text normalization).

---

## Bugs and loose ends found while reading

Not concepts. Each would be its own fix, outside this teaching run.

- **The chat drops a message** posted between the last poll and the owner's send.
  `frontend/src/Chat.tsx:Chat`. CF reproduced it in a scratch copy; I confirmed the code.
- **The chat keeps showing "working"** after the producer's stop notice.
  `frontend/src/Chat.tsx:Chat`, `agents/loop.py:say_it_stopped`. CF reproduced it; I confirmed it.
- **The producer stops answering** after a card swap whose re-assembly keeps failing.
  `agents/producer.py:assembled_again`. AC read it from the code; I confirmed it.
- **A redelivered step can run beside the first copy.** `agents/tasks.py:run_scene_step`. DW
  read it from the code; I confirmed it.
- **HeyGen's paid POST is sent again on a 5xx.** `gateway/heygen_adapter.py:HeyGenProvider._send`.
  This goes away with #327.
- **`assembly.captions` raises `ZeroDivisionError`** on a scene with no timed words. Only the
  caller's refusal guards it (AC, run locally).
- **Docs and code disagree:**
  - `docs/rate-limits.md` says retries wait 2 s + 8 s; the code waits 5/20/60/120 s (GW).
  - The `picture_check.py` docstring names callers it doesn't have (PT).
  - `AssembleAd` tells the producer the captions are "the words as spoken" (AC).
  - `ProducedItem.cuts` help text lists 6 of its 11 keys (AC).
- **Unused:**
  - `settings.FAL_QUEUE_URL` (GW).
  - `SceneStep.Status.stopped` is never set (DW).
- **Card pictures turn transparent page PNGs black** (`jobs/cards.py:_card_picture`, AC).
