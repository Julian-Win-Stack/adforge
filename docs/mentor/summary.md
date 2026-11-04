# Design mentor: the whole repo, one learning plan

What AdForge's design is missing, from two mentor runs, merged into one plan for Julian.
Teaching only: nothing in the app changed. Applying any concept is a design change and needs
Julian's yes first (design-mentor skill, step 3).

- **Run 1 (Nov 3):** 8 areas of the code, at `bfc4dfc`.
- **Run 2 (Nov 4):** every AI system in the app, in 6 groups, plus the agent architecture. Run at
  `fd346df`: the latest `main` (`f87430b`), after ADR 0012 made every scene an H3 clip.
- **Method:** `.claude/skills/design-mentor/SKILL.md` steps 2–3, one mentor per area or group.
  - An area's design was its ADRs, plan docs, `CONTEXT.md` entries and closed `Spec:` issues
    (#1, #210, #247, #272, #273, #303, #327).
  - Run 2 also filled one checklist per AI system: what it's asked, which model and why, in and
    out, what checks it, its eval, how it fails, its cost per ad.
  - In run 2, an independent reader then tried to refute every must-apply-now finding.
- **Paths:** backend paths are under `backend/`. Every cite in this file resolves at `fd346df`.

| Key | Report | Concepts | Must apply now |
|---|---|---|---|
| SA | [scene-agents.md](scene-agents.md): scene agents (PR #324) | 10 | — |
| PL | [producer-loop.md](producer-loop.md): producer and agent loop | 8 | 4 |
| DW | [durable-work.md](durable-work.md): durable work, worker and deploy | 12 | 6 |
| GW | [gateway.md](gateway.md): gateway, paid services and money | 6 | 2 |
| PG | [page-library.md](page-library.md): reading the page and the library | 10 | 5 |
| PT | [planning-talking.md](planning-talking.md): planning and talking scenes | 11 | 6 |
| CE | [checks-evals.md](checks-evals.md): checks and evals | 11 | 6 |
| AC | [assembly-cards.md](assembly-cards.md): assembly and scene cards | 15 | 6 |
| CF | [chat-frontend.md](chat-frontend.md): chat, API and frontend | 18 | 7 |
| PA | [ai-page.md](ai-page.md): junk check, labelling call, face note, photo picker | 9 | 2 |
| PI | [ai-pictures.md](ai-pictures.md): the person, place pictures, owner photos and parts, face swaps | 8 | 2 |
| PC | [ai-picture-checks.md](ai-picture-checks.md): picture checks, cut-outs, recolours | 7 | 2 |
| VW | [ai-voice-words.md](ai-voice-words.md): H3's voice, the transcript, the word check, the music | 8 | 4 |
| VM | [ai-video-model.md](ai-video-model.md): the video model, H3 for every scene | 9 | 4 |
| LR | [ai-line-rewrites.md](ai-line-rewrites.md): rewrite, shorten and lengthen | 9 | 5 |
| AR | [architecture.md](architecture.md): every agent and AI call as one system | 9 | 4 |

- **Run 1:** 91 area concepts merged into 77 new, plus the 10 taught: #1–#87.
- **Run 2:** 59 concepts.
  - 10 merged with each other or with run-1 concepts.
  - 49 are new: #88–#136.
- **All:** 136 concepts, each listed once below.

---

## Learn first

In this order. Each one makes the next easier.

1. **Idempotency keys (#1).**
   - Top risk, and a habit in 8 places.
   - It makes every retry, resend and restart safe; the rest of the reliability list assumes it.
2. **Timeouts, deadlines and retry budgets (#3).**
   - A habit in 7 places, and safe only once #1 holds.
   - Nothing in the app has an upper bound on waiting.
   - Pair it with #98: retry only what can change.
3. **Explicit state machine (#24).**
   - Taught, but now in 10 places.
   - It's behind the busy-scene guard bug, the stuck session after a failed re-assembly, and the
     "working" dots that never stop.
4. **Golden dataset (#9).**
   - A habit in 7 places, and the first AI-engineering step.
   - Every eval needs one, and none can be re-run from the repo today.
   - Then #93 (evaluate what ships) and #109 (online evaluation).
5. **Canary release with go/no-go criteria (#88).**
   - #339 just put every ad on H3 speaking the line.
   - Write the bar before the first paid round.
6. **Typed handoff contracts between agents (#92).**
   - ADRs 0011 and 0012 made AdForge a multi-agent system.
   - What the producer and the scene agents pass each other is still prose.
7. **Fail-open vs fail-closed (#89), with severity tiers (#90).**
   - A clip where H3 never spoke, or said the wrong price, can reach the ad today.
8. **Transactional outbox (#2).**
   - A step whose message never reached Redis stalls the whole ad, silently.
9. **Short transactions (#10).**
   - A habit in 5 places, and an hour of Postgres locking to learn.
   - It explains frozen job rows and sends waiting behind uploads.
10. **Grounded extraction (#4).**
    - The price is the one fact never checked against the page.
    - Data provenance (#23) is the same lesson for video transcripts.

---

## Habits (seen in 3 or more places)

Counted across 16 sources: the scene-agent list, run 1's 8 areas and run 2's 7 groups. A
concept counts in a place when that report teaches it or lists it under "Also seen here".
34 concepts qualify.

**What the counts say:**
- **The taught concepts didn't carry over.** All 10 from the scene-agent run show up again,
  in 6 to 13 places each:
  - stopping conditions (no dollar cap anywhere) in 13;
  - the state machine, judge calibration and independent evidence in 10 each.
- **The biggest new habit is evals.** These are the same gap in seven forms: almost no AI call
  in AdForge is measured as it ships.
  - golden dataset #9 (7 places)
  - regression evals #11 (4)
  - confidence intervals #27 (3)
  - judge calibration #34 (10)
  - agent evals #35 (7)
  - eval–production skew #93 (2)
  - online evaluation #109 (3)
- **Run 2 adds two habits:**
  - fail-open vs fail-closed (#89): every check picks it by accident;
  - online evaluation (#109): the data is already recorded, and nobody reads it.

**In 7 or more places**

| # | Concept | Count | Areas |
|---|---|---|---|
| 19 | Stopping conditions: no dollar cap (taught) | 13 | SA PL DW GW PG PT CE CF PA PI VM LR AR |
| 24 | Explicit state machine (taught) | 10 | SA PL DW GW PG PT AC CF LR AR |
| 34 | Calibrating an LLM judge (taught) | 10 | SA PL PG PT CE CF PA PC VW VM |
| 43 | Independent evidence for checks (taught) | 10 | SA PG PT CE PI PC VW VM LR AR |
| 1 | Idempotency keys and idempotent consumers | 8 | DW GW AC CF PI VW VM LR |
| 3 | Timeouts, deadlines and retry budgets | 7 | DW GW CF PI PC VM AR |
| 9 | Golden dataset | 7 | CE PA PI PC VW VM LR |
| 35 | Agent evals on a real model (taught) | 7 | SA PL PG PT CE PA AR |
| 42 | Leases instead of "one worker, always" (taught) | 7 | SA DW GW PG CF PA AR |
| 44 | Stable prompt prefix (taught) | 7 | SA PL GW PG PT PC AR |
| 46 | App-side rate limiter (taught) | 7 | SA GW PG PT CE PI VM |

**In 4 to 6 places**

| # | Concept | Count | Areas |
|---|---|---|---|
| 33 | Contract and integration tests against the real thing | 6 | DW GW CF PC VW VM |
| 37 | Constrained decoding (put the rule in the schema) | 6 | PG PT PA PC VW LR |
| 45 | Atomic counters (taught) | 6 | SA DW PG AC CF AR |
| 47 | Precision vs. recall (taught) | 6 | SA PG PT CE PA PC |
| 58 | Feedback-driven retries (re-ask with the error) | 6 | PG PT PI PC VW LR |
| 63 | Content-addressed keys (input fingerprints) | 6 | DW AC PA PI PC VM |
| 10 | Short transactions (no network call while holding a lock) | 5 | DW GW PG CF PA |
| 12 | Append-only, immutable records | 5 | PL AC CF PI AR |
| 36 | Text normalization before length estimates | 5 | PT AC VW VM LR |
| 11 | Regression evals (eval-gated judge and model changes) | 4 | CE PA PC VM |
| 20 | Cost reconciliation (recorded cost vs the bill) | 4 | GW VW VM AR |
| 23 | Data provenance (a transcript is model output) | 4 | PG PI PC LR |
| 61 | Identity consistency check (face and voice embeddings) | 4 | PT PI PC VW |

**In 3 places**

| # | Concept | Count | Areas |
|---|---|---|---|
| 15 | Aggregate invariants (one rule check on every write) | 3 | PT LR AR |
| 17 | Over-optimizing against an imperfect judge (Goodhart) | 3 | CE VM AR |
| 25 | Context budget (context rot) | 3 | PL PC AR |
| 27 | Confidence intervals on eval scores | 3 | CE PA PC |
| 48 | Webhooks instead of polling | 3 | GW VW VM |
| 59 | Pixel-preserving segmentation · deferred in #273 | 3 | PG PI PC |
| 62 | Human-in-the-loop approval as a structured action | 3 | PT PI LR |
| 66 | Expand/contract migrations | 3 | DW VM AR |
| 89 | Fail-open vs fail-closed, chosen per check and cause | 3 | PI PC VW |
| 109 | Online evaluation from production traces | 3 | PI PC VW |

---

## Top 20, checked against the code

All 20 are **must apply now**. For each one I opened the code behind it and confirmed the
claim.

### Run 2's top 10

**#88 Canary release with go/no-go criteria** · VM
- Goes wrong: #339 put every new ad on H3-voiced clips, and no H3 clip with speech has ever been
  made from AdForge's own prompts. Nothing written says what the 3 smoke ads must show before
  the 12 run.
- Cites:
  - `gateway/fake.py:FakeModel.submit_broll` returns a tone exactly as long as asked.
  - `jobs/work.py:transcribe_clip` already keeps what a free go/no-go check would read.
- Checked: none of the 3 recorded replies in `tests/creatify/` has a speech tag, and no go/no-go
  list exists in `docs/` or `scripts/`.

**#89 Fail-open vs fail-closed, chosen per check and cause** · PI PC VW · habit
- Goes wrong: a clip where H3 never spoke passes into the ad, and the owner is only told to
  listen to it.
- Cites:
  - `jobs/work.py:_clip_sound` pulls the sound with ffmpeg `-vn`. A clip with no audio stream
    fails here.
  - `jobs/work.py:transcribe_clip` catches every error and returns None.
  - `jobs/word_check.py:check` rules "unchecked" when nothing was heard.
  - `agents/scene_agent/lifecycle.py:_passes` fails a try only on "fail".
- The same event (no answer) has 7 different outcomes across the picture checks (PC). When
  every face-swap try fails, the stranger's real face is sent (PI).
- Checked: all four functions.

**#90 Severity-tiered checks (hard vs soft gates)** · VW
- Goes wrong: when the 6-clip cap or a give-up ends a run, the ad keeps the last try. That can
  be a try where H3 said the wrong price, while the caption shows the right one.
- Cites:
  - `jobs/work.py:_as_the_last_try_filmed_it` keeps `landing[-1]`.
  - `agents/scene_agent/lifecycle.py:_passes` treats word fails and judge fails alike.
- Known in part: the limitation "When the fixing agent gives up, the ad keeps a clip the judge
  failed". ADR 0012 widened it to wrong words without a decision on it.
- Checked: both.

**#91 Isochrony (fit speech into a fixed slot, with headroom)** · VM
- Goes wrong: a clip's length is its line at 2.5 words a second, rounded up, plus 1 s for the
  action after the last word. There's no margin for pace: at 2.0 words a second, a 25-word line
  runs 1.5 s past its 11 s clip.
- Cites: `jobs/checks.py:h3_clip_seconds`, `jobs/work.py:sent_handoff`.
- Checked: the formula is `max(5, ceil(words / 2.5) + 1)`.

**#92 Typed handoff contracts between agents** · AR
- Goes wrong:
  - A first ad has to quote the product link as "the owner's words" for every scene.
  - A run's result is one sentence, and the cost it reports is the whole step's, not the run's.
  - A give-up reaches the owner twice.
- Cites:
  - `agents/scene_tools.py:StartSceneAgent`: `quotes` needs at least one.
  - `agents/scene_agent/__init__.py:finished` reports `agents/tasks.py:_cost`.
  - `agents/scene_agent/__init__.py:finish` posts `SCENE_AGENT_GAVE_UP` to the owner.
- Checked: all three.

**#13 Single source of truth, as one length model** · CF LR (a run-1 concept; LR found its second form)
- Goes wrong: the check against the owner's target length measures speech only, while clips
  play whole and longer. A 15 s target with 3 lines of 12 words "fits" at 14.4 s and plays 18 s,
  and the owner's question quotes the wrong length.
- Cites: `jobs/work.py:_fit_length` uses `jobs/checks.py:script_seconds`; clip length is
  `jobs/checks.py:h3_clip_seconds`.
- Checked: `_fit_length` calls `script_seconds`.

**#93 Eval–production skew (evaluate what ships)** · PA PC
- Goes wrong:
  - The only picture-judge eval measures a function nothing on the live path calls.
  - The photo picker's "92%" was measured on an older model and an older prompt.
- Cites:
  - `evals/calibrate_picture_check.py:judge_as_the_app_does` is the only caller of
    `jobs/work.py:judge_starting_picture`.
  - The "92%" reason sits in `gateway/catalog.py:MODEL_FOR_PURPOSE`.
- Checked: both.

**#94 Prompt management (one source per prompt, pinned by tests)** · LR
- Goes wrong: the live rewrite prompt is an inline string. It lost three rules:
  - the producer's messages are not facts (ADR 0002);
  - keep the price;
  - the length rule.
- `jobs/checks.py:REWRITE_INSTRUCTIONS` has no caller in app code.
- Cites: `jobs/work.py:_rewrite_scene`, `jobs/checks.py:REWRITE_INSTRUCTIONS`.
- Checked: I read the inline prompt.

**#95 Stable identifiers (key by ID, not position)** · LR
- Goes wrong:
  - Line-fit tries are counted by scene number, and that number moves on a reorder.
  - Shortening the script puts the model's answers onto scene rows by position. It then
    hard-deletes the rows left over, with their paid steps and clips.
- Cites: `jobs/work.py:_fit_for` (`handoff__scene=scene.number`), `jobs/work.py:_shorten`
  (`zip(...)`, then `scene.delete()`).
- Checked: both.

**#96 Defense in depth for a safety label (an independent face detector)** · PA
- Goes wrong: one model's yes or no decides whether a stranger's face in a shop photo is swapped
  out. A wrong "no" sends the real face to the video model, with no notice. That call has never
  run on a real model in any test or eval.
- Cites:
  - `jobs/face_swap.py:_photo_with_a_face` reads only `has_face`.
  - `jobs/face_swap.py:for_clip` returns the photo unswapped.
  - `jobs/work.py:note_face` makes the call.
- Checked: both face-swap functions.

### Run 1's top 10

Checked on `bfc4dfc`, then again on `f87430b` (Nov 4): all 10 still hold.

**1. Idempotency keys and idempotent consumers** · GW CF DW AC · habit
- Goes wrong: paid work is paid twice or lost, and two scene agents can run on one scene.
- Cites:
  - `gateway/gateway.py:_recorded` writes the `ModelCall` only after `make()` returns.
  - `gateway/creatify_adapter.py:CreatifyProvider._send` turns a lost reply into `ClipFailed`,
    so a clip that may be made and charged is never collected.
  - (HeyGen's resend of a paid POST on a 5xx went away with HeyGen, ADR 0012.)
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

### Corrections

- **Run 2, by the independent readers:**
  - They checked 29 must-apply-now findings: 10 confirmed, 19 corrected, none refuted.
  - They retagged 6 as good to know:
    - generation loss #118;
    - online evaluation #109 (two of its three sources);
    - pass@k #111;
    - code-based graders #117;
    - trace propagation #110.
  - They fixed 5 cites. Each report ends with "Checked by an independent reader".
  - I fixed 3 wording errors they flagged in `ai-page.md`, `ai-voice-words.md` and
    `architecture.md`.
  - My own check of run 2's top 10 found nothing more to correct.
- **Run 1, by me:**
  - **#5:** the mentor wrote that the session is stuck "until the code changes". That holds only
    for a failure that repeats on the same inputs, such as the clip-shape failure in #16. A
    one-off ffmpeg error clears on the next message.
  - **#3:** "up to 30 attempts" for one call is the worst case: safety-filter remakes and an
    unusable reply on top of retries. The plain case is 5 tries and about 3.5 min of sleeping.
  - **#22 (graceful shutdown):** the broken `deploy/deploy.sh:busy` check is already a known bug
    in `docs/azure.md`. It stays must apply now, marked known.
  - **Nothing dropped:** every top-10 claim held in the code.

### Stale since ADR 0012

- **Cites that no longer resolve:** 6 run-1 cites point at code ADR 0012 retired. The ranked
  list below drops or replaces them:
  - HeyGen's `_send` (#1), Inworld's `_account_full` (#33) and `voices.designed_voice` (#46)
    were dropped.
  - `work._measure_voice` (#36) is now `checks.h3_clip_seconds`.
  - `scenes.starting_picture_choice_for` (#37) was dropped.
  - `assembly.cuts` (#69, #70) is now `assembly.whole_clips`.
- **Old text in run 1's reports:** some still describe the HeyGen and Inworld path. Examples:
  - `gateway.md`'s HeyGen retry;
  - `planning-talking.md`'s #41, since built as the word check.

  The `ai-*` reports judge the current code.
- **A rule in AGENTS.md is out of date:**
  - It says a starting worker "marks every running step dead".
  - `agents/tasks.py:carry_on_after_the_worker_starts` now resumes scene-agent steps
    (architecture report).
  - I left AGENTS.md as it is: this run writes only to `docs/mentor/`.

---

## The one ranked list

Ranked by impact on this build, across the whole repo.
- **Rank** is the concept's place in the list. **#** is its ID, which the reports cite.
- **Tags:**
  - **taught:** from the scene-agent run;
  - **habit:** seen in 3 or more places;
  - **known:** behind a known limitation.
- Where a concept shows up in many places, the six most relevant cites are listed; the rest are
  in the reports.

### Top 20 (must apply now, checked)

| Rank | # | Concept | Areas | Where (file:function) |
|---|---|---|---|---|
| 1 | 1 | Idempotency keys and idempotent consumers · habit | DW GW AC CF PI VW VM LR | `gateway/gateway.py:_recorded`; `gateway/creatify_adapter.py:CreatifyProvider._send`; `gateway/openai_adapter.py:_asked`; `agents/tasks.py:run_scene_step`; `chat/api.py:_send`; `jobs/cut_and_send.py:_say`; +3 more in the reports |
| 2 | 88 | Canary release with go/no-go criteria | VM | `jobs/work.py:transcribe_clip`; `gateway/gateway.py:collect_clip`; `gateway/fake.py:FakeModel.submit_broll` |
| 3 | 2 | Transactional outbox | DW | `agents/producer.py:_start_step`; `agents/tasks.py:fail_dead_steps` |
| 4 | 89 | Fail-open vs fail-closed, chosen per check and cause · habit | PI PC VW | `jobs/work.py:_clip_sound`; `gateway/gateway.py:transcribe`; `jobs/work.py:owner_photo_check`; `jobs/owner_parts.py:_redrawn`; `jobs/work.py:judge_starting_picture`; `jobs/product_only.py:_cut` |
| 5 | 3 | Timeouts, deadlines and retry budgets · habit | DW GW CF PI PC VM AR | `gateway/gateway.py:collect_clip`; `agents/tasks.py:_beating`; `jobs/job_locks.py:one_at_a_time`; `adforge/retry.py:with_retries`; `frontend/src/api.ts:getMessagesSince`; `jobs/checked_pictures.py:made_and_checked`; +2 more in the reports |
| 6 | 90 | Severity-tiered checks (hard vs soft gates) | VW | `jobs/work.py:_as_the_last_try_filmed_it`; `jobs/work.py:_ended`; `agents/scene_agent/__init__.py:finish` |
| 7 | 4 | Grounded extraction (check the price against the page) | PG | `jobs/work.py:copy_page_text`; `jobs/page_text.py:record_prices` |
| 8 | 91 | Isochrony (fit speech into a fixed slot, with headroom) | VM | `jobs/checks.py:h3_clip_seconds`; `jobs/work.py:sent_handoff`; `agents/scene_agent/__init__.py:_conversation` |
| 9 | 5 | Failure isolation (poison messages) | AC PI | `agents/tasks.py:_work_on`; `agents/producer.py:assembled_again`; `agents/card_picks.py:assemble_after_swaps`; `jobs/work.py:person_and_room` |
| 10 | 92 | Typed handoff contracts between agents | AR | `agents/scene_tools.py:StartSceneAgent`; `tests/test_end_to_end.py:test_one_product_link_starts_all_scene_agents_together_and_finishes_with_a_talking_scene`; `agents/scene_agent/__init__.py:finished` |
| 11 | 6 | Cursor sync with a high-water mark | CF | `frontend/src/Chat.tsx:Chat` |
| 12 | 13 | Single source of truth (server state, one length model) | CF LR | `frontend/src/Chat.tsx:Chat`; `agents/loop.py:say_it_stopped`; `jobs/work.py:_fit_length`; `jobs/checks.py:script_seconds` |
| 13 | 7 | Backups you have restored | DW | `deploy/backup.sh` (script); `adforge/file_store.py:save` |
| 14 | 93 | Eval–production skew (evaluate what ships) | PA PC | `gateway/catalog.py:MODEL_FOR_PURPOSE`; `jobs/work.py:pick_photos`; `jobs/work.py:judge_starting_picture`; `evals/calibrate_picture_check.py:judge_as_the_app_does` |
| 15 | 8 | Groundedness eval for the fact check | CE LR | `jobs/work.py:_ask_fact_check`; `jobs/checks.py:FACT_CHECK_INSTRUCTIONS` |
| 16 | 94 | Prompt management (one source per prompt, pinned by tests) | LR | `jobs/checks.py:REWRITE_INSTRUCTIONS`; `jobs/work.py:_rewrite_scene`; `jobs/checks.py:LENGTHEN_LINE_INSTRUCTIONS` |
| 17 | 9 | Golden dataset · habit | CE PA PI PC VW VM LR | `evals/calibrate_picture_check.py:load_cases`; `jobs/work.py:check_page`; `jobs/face_swap.py:SWAP_PROMPT`; `jobs/word_check.py:check` |
| 18 | 95 | Stable identifiers (key by ID, not position) | LR | `jobs/work.py:_fit_for`; `jobs/work.py:_shorten`; `jobs/plan_edits.py:renumber` |
| 19 | 10 | Short transactions (no network call while holding a lock) · habit | DW GW PG CF PA | `jobs/watcher.py:_keep`; `jobs/work.py:save_video_pictures`; `chat/api.py:_send`; `jobs/job_locks.py:one_at_a_time`; `gateway/gateway.py:_one_azure_picture_at_a_time` |
| 20 | 96 | Defense in depth for a safety label (an independent face detector) | PA | `jobs/face_swap.py:_photo_with_a_face`; `jobs/face_swap.py:for_clip`; `jobs/work.py:note_face` |

### Must apply now

| Rank | # | Concept | Areas | Where (file:function) |
|---|---|---|---|---|
| 21 | 11 | Regression evals (eval-gated judge and model changes) · habit | CE PA PC VM | `jobs/clip_judge.py:wording_version`; `tests/test_frozen_rules.py:test_everything_the_clip_judge_is_told_is_the_version_its_rulings_name`; `gateway/catalog.py:MODEL_FOR_PURPOSE`; `jobs/cut_out.py:check_cut_out`; `jobs/work.py:_clip_purposes` |
| 22 | 97 | Conflicting implicit decisions (one owner per decision) | AR | `agents/scene_agent/change_scene.py:ChangeScene.run`; `agents/scene_agent/lifecycle.py:_validate_line`; `jobs/work.py:run_checks` |
| 23 | 12 | Append-only, immutable records · habit | PL AC CF PI AR | `agents/loop.py:_conversation`; `agents/page_panel.py:_refuse`; `chat/api.py:MessageSerializer.get_tries`; `jobs/work.py:bring_back_clip`; `jobs/card_versions.py:shown`; `jobs/owner_parts.py:_kept`; +1 more in the reports |
| 24 | 98 | Transient vs persistent failures (retry only what can change) | VW | `jobs/work.py:sent_handoff`; `jobs/checks.py:h3_clip_seconds`; `agents/scene_agent/instructions.py:INSTRUCTIONS` |
| 25 | 14 | Cache freshness (TTL) for facts | PG | `jobs/firecrawl.py:read_page` |
| 26 | 15 | Aggregate invariants (one rule check on every write) · habit | PT LR AR | `jobs/planning.py:Plan._opens_on_the_person`; `jobs/plan_edits.py:reorder`; `jobs/checks.py:shortened_script_for`; `agents/scene_agent/change_scene.py:ChangeScene.run` |
| 27 | 99 | Context scoping per agent (maker and checker see the same request) | AR | `agents/scene_agent/picture_check.py:check`; `agents/scene_tools.py:PassNote.run`; `agents/scene_agent/change_scene.py:_fact_checked` |
| 28 | 16 | Canonical intermediate format | AC | `jobs/assembly.py:join` |
| 29 | 17 | Over-optimizing against an imperfect judge (Goodhart) · habit · known | CE VM AR | `jobs/clip_judge.py:judge_clip`; `jobs/auto_fix.py:clip_to_keep`; `agents/scene_agent/lifecycle.py:_passes` |
| 30 | 100 | Conditioning modes (image-to-video, first–last frame, reference-to-video) | VM | `jobs/work.py:sent_handoff`; `agents/scene_forms.py:video_prompt`; `agents/scene_agent/picture_check.py:check` |
| 31 | 18 | Anchoring bias (a blind second opinion) | PL CE | `agents/look.py:_version_shown`; `evals/grading_page.py:_verdict`; `evals/results.py:load_run` |
| 32 | 19 | Stopping conditions: no dollar cap · taught · habit · known | SA PL DW GW PG PT CE CF PA PI VM LR AR | `agents/loop.py:settle`; `agents/money.py:cost_of`; `scripts/ad-runs/run.py:spent`; `jobs/watcher.py:_past_the_filter`; `jobs/work.py:_past_refused_tiles`; `agents/producer.py:MakePicture`; +3 more in the reports |
| 33 | 101 | Yield-adjusted cost (cost per usable clip) | VM | `gateway/catalog.py:video_cost_usd`; `agents/tasks.py:_cost`; `agents/scene_agent/film.py:Film.run` |
| 34 | 20 | Cost reconciliation (recorded cost vs the bill) · habit | GW VW VM AR | `gateway/catalog.py:video_cost_usd`; `gateway/creatify_adapter.py:CreatifyProvider.status`; `gateway/gateway.py:transcribe`; `gateway/catalog.py:PRICE_PER_SECOND_OF_VIDEO` |
| 35 | 102 | Failure attribution across agents | AR | `evals/results.py:CAUSES`; `evals/round_export.py:export_ad`; `evals/round_export.py:_first_clip_step` |
| 36 | 21 | Dead man's switch (monitor scheduled jobs) | DW | `adforge/monitoring.py:start` |
| 37 | 22 | Graceful shutdown and draining · known | DW | `deploy/deploy.sh:busy` |
| 38 | 103 | Cross-shot continuity from a shared anchor | PI | `jobs/work.py:_before_picture`; `jobs/scenes.py:ROOM_ALONE_PROMPT`; `jobs/work.py:scene_place` |
| 39 | 23 | Data provenance (a transcript is model output) · habit · known | PG PI PC LR | `jobs/watcher.py:_keep`; `jobs/work.py:_video_transcripts`; `jobs/checks.py:FACT_CHECK_INSTRUCTIONS`; `jobs/library_items.py:came_from`; `jobs/recolour.py:recolour`; `jobs/work.py:_fit_for` |
| 40 | 104 | Contextual biasing for speech-to-text (keyterms, known language) | VW | `gateway/elevenlabs_adapter.py:ElevenLabsProvider.transcribe`; `jobs/assembly.py:timed_script`; `jobs/word_check.py:tidy` |
| 41 | 24 | Explicit state machine · taught · habit | SA PL DW GW PG PT AC CF LR AR | `agents/tasks.py:_something_will_wake_it`; `jobs/work.py:_clip_asked_for`; `frontend/src/scenes.ts:sceneCards`; `jobs/watcher.py:watch_videos`; `jobs/models.py:Scene.change_line`; `agents/producer.py:assemble_the_ad`; +1 more in the reports |
| 42 | 105 | Two-axis edit evaluation (instruction fidelity and preservation) | PC | `jobs/face_swap.py:FACE_CHECK`; `jobs/face_swap.py:for_clip`; `jobs/recolour.py:recolour` |
| 43 | 25 | Context budget (context rot) · habit | PL PC AR | `agents/fold.py:fold_point`; `agents/loop.py:_as_given`; `jobs/product_only.py:_helpers_for`; `agents/scene_agent/__init__.py:_conversation` |
| 44 | 26 | Passing reasoning items back across tool calls | PL | `gateway/openai_adapter.py:take_turn` |
| 45 | 106 | Instruction hierarchy (code's rules outrank free text) | PI | `jobs/work.py:ON_TOP`; `jobs/work.py:_on_top`; `jobs/work.py:remake_person` |
| 46 | 27 | Confidence intervals on eval scores · habit | CE PA PC | `evals/results.py:_judge_lines`; `evals/ai_checks.py:Score.line`; `jobs/photos.py:NOTE_FACE_INSTRUCTIONS`; `jobs/face_swap.py:SWAP_PROMPT` |
| 47 | 28 | Perceptual hashing's colour blind spot | PG | `jobs/photos.py:dhash`; `agents/page_panel.py:_take` |
| 48 | 107 | Length-controlled generation (models can't count words) | LR | `jobs/work.py:_shorter_line`; `jobs/work.py:_lengthen_line`; `jobs/work.py:_fit_for` |
| 49 | 29 | Loudness normalisation (EBU R128, LUFS) | AC VW | `jobs/assembly.py:join` |
| 50 | 30 | Post-render quality control | AC | `jobs/work.py:assemble_ad` |
| 51 | 31 | Safe zones | AC | `jobs/assembly.py:write_text_to_draw` |
| 52 | 108 | Evaluator-optimizer loop (every writer sees every check) | LR | `jobs/checks.py:RewriteHandoff`; `jobs/checks.py:ShortenHandoff`; `jobs/work.py:_fit_length` |
| 53 | 32 | Error boundaries and browser error tracking | CF | `frontend/src/ScenePanel.tsx:ScenePanel`; `adforge/monitoring.py:start` |
| 54 | 33 | Contract and integration tests against the real thing · habit | DW GW CF PC VW VM | `gateway/fake.py:FakeModel`; `frontend/src/App.test.tsx:fakeBackend`; `tests/conftest.py:HeldSteps`; `gateway/fake.py:FakeModel.edit`; `gateway/fake.py:FakeModel.transcribe`; `gateway/fake.py:FakeModel.submit_broll` |
| 55 | 34 | Calibrating an LLM judge · taught · habit | SA PL PG PT CE CF PA PC VW VM | `agents/look.py:look_at`; `agents/card_picks.py:use`; `jobs/cut_out.py:check_cut_out`; `evals/calibrate_ai_checks.py:main`; `jobs/work.py:check_page`; `jobs/recolour.py:recolour`; +2 more in the reports |
| 56 | 35 | Agent evals on a real model · taught · habit | SA PL PG PT CE PA AR | `agents/producer.py:PRODUCER`; `jobs/photos.py:PICK_INSTRUCTIONS`; `jobs/library_items.py:label_photo`; `tests/scene_script.py:script_scene_agents` |
| 57 | 36 | Text normalization before length estimates · habit | PT AC VW VM LR | `jobs/checks.py:count_words`; `jobs/assembly.py:timed_script`; `jobs/checks.py:h3_clip_seconds`; `jobs/work.py:sent_handoff` |
| 58 | 37 | Constrained decoding (put the rule in the schema) · habit | PG PT PA PC VW LR | `jobs/planning.py:producer_decision_for`; `gateway/gateway.py:call_model`; `jobs/watcher.py:_asked`; `jobs/photos.py:PickedPicture.label_of`; `jobs/product_only.py:HelperAnswer`; `jobs/word_check.py:Answer`; +1 more in the reports |
| 59 | 38 | Tagged unions (make illegal states unrepresentable) | PT CF | `jobs/planning.py:ScriptScene.check_broll_details`; `jobs/models.py:Scene.change_line`; `chat/models.py:Message`; `frontend/src/Chat.tsx:isFinishedAd` |
| 60 | 39 | Prompt decomposition (prompt chaining) | PT PA | `jobs/planning.py:PLAN_INSTRUCTIONS`; `jobs/work.py:plan`; `jobs/photos.py:PICK_INSTRUCTIONS` |
| 61 | 40 | Text measurement (fit by rendered width) | AC | `jobs/planning.py:PlannedScene._a_few_words`; `jobs/assembly.py:captions` |
| 62 | 41 | ASR round-trip check (WER with a shared normalizer) | PT VW | `agents/producer.py:_heard_otherwise`; `jobs/word_check.py:check` |
| 63 | 42 | Leases instead of "one worker, always" · taught · habit | SA DW GW PG CF PA AR | `agents/tasks.py:carry_on_after_the_worker_starts`; `gateway/gateway.py:_one_azure_picture_at_a_time`; `jobs/library_items.py:add_kept_photos`; `agents/tasks.py:another_session_is_busy` |
| 64 | 43 | Independent evidence for checks · taught · habit | SA PG PT CE PI PC VW VM LR AR | `jobs/clip_judge.py:sent_references`; `jobs/product_only.py:_cut`; `jobs/face_swap.py:_check`; `agents/scene_agent/picture_check.py:check`; `agents/scene_agent/film.py:_check_words`; `agents/scene_agent/film.py:Film.run`; +2 more in the reports |
| 65 | 44 | Stable prompt prefix · taught · habit | SA PL GW PG PT PC AR | `gateway/openai_adapter.py:OpenAIProvider.take_turn`; `gateway/catalog.py:cost_usd`; `jobs/product_only.py:_helpers_for`; `gateway/gateway.py:call_model`; `agents/scene_agent/__init__.py:_conversation` |
| 66 | 45 | Atomic counters · taught · habit | SA DW PG AC CF AR | `jobs/work.py:save_video_pictures`; `jobs/product_videos.py:keep_owner_video`; `chat/messages.py:add`; `jobs/owner_answers.py:run_again`; `agents/scene_agent/film.py:Film.run` |
| 67 | 46 | App-side rate limiter · taught · habit | SA GW PG PT CE PI VM | `gateway/gateway.py:_wait_for_azures_pace`; `jobs/firecrawl.py:scrape`; `jobs/watcher.py:watch_videos`; `gateway/gateway.py:_picture_made`; `gateway/creatify_adapter.py:_checked` |
| 68 | 47 | Precision vs. recall · taught · habit | SA PG PT CE PA PC | `jobs/clip_judge.py:MOTION_CHECK`; `jobs/photos.py:PICK_INSTRUCTIONS`; `jobs/work.py:judge_starting_picture`; `jobs/work.py:note_face`; `jobs/product_only.py:_helpers_for` |

### Good to know

| Rank | # | Concept | Areas | Where (file:function) |
|---|---|---|---|---|
| 69 | 109 | Online evaluation from production traces · habit | PI PC VW | `gateway/gateway.py:_recorded`; `jobs/checked_pictures.py:tried_before`; `gateway/gateway.py:VERDICT_SCORES`; `gateway/gateway.py:_scores`; `jobs/checks.py:FIXED_WORDS_PER_SECOND`; `jobs/work.py:_transcribe_clip` |
| 70 | 48 | Webhooks instead of polling · habit | GW VW VM | `gateway/gateway.py:collect_clip`; `gateway/fal_adapter.py:FalProvider.compose` |
| 71 | 110 | Trace context propagation across agents | AR | `agents/tasks.py:_do_the_work`; `agents/scene_tools.py:open_note`; `agents/scene_agent/__init__.py:run` |
| 72 | 49 | Circuit breaker | GW | `gateway/gateway.py:_picture_made` |
| 73 | 111 | Sampling variance and pass@k (re-roll vs re-prompt) | VM | `agents/scene_agent/instructions.py:INSTRUCTIONS`; `agents/scene_agent/lifecycle.py:_passes`; `tests/test_h3_scene_words.py:test_six_word_failures_end_the_run_and_offer_every_try` |
| 74 | 112 | Compounding error (series reliability, pass^k) | AR | `agents/producer.py:assemble_the_ad`; `jobs/planning.py:producer_decision_for`; `agents/scene_agent/lifecycle.py:_passes` |
| 75 | 50 | Recitation (the agent keeps its own task list) | PL | `agents/snapshot.py:ad_as_it_stands`; `agents/tasks.py:_whats_left` |
| 76 | 51 | Double-texting strategy | PL | `agents/loop.py:_work` |
| 77 | 52 | Tool masking (`allowed_tools`) | PL | `agents/producer.py:PRODUCER`; `agents/loop.py:_held_by_the_pause` |
| 78 | 113 | Workflows vs agents (code the path you already know) | AR | `agents/producer.py:INSTRUCTIONS`; `agents/producer.py:PRODUCER`; `agents/tasks.py:_nudge` |
| 79 | 114 | Fan-in barrier and tail latency | AR | `agents/tasks.py:_finish`; `agents/tasks.py:_a_result_wakes_it`; `agents/scene_agent/film.py:Film.run` |
| 80 | 53 | Cost-sensitive decision threshold | CE VW | `jobs/clip_judge.py:MOTION_CHECK`; `jobs/word_check.py:QUESTION` |
| 81 | 54 | Selective prediction (abstention) | CE | `evals/results.py:_judge_lines`; `jobs/clip_judge.py:judged` |
| 82 | 55 | Intra-rater reliability | CE | `evals/grading_page.py:_scene_card` |
| 83 | 115 | Inter-annotator agreement (Cohen's kappa) | PA | `jobs/work.py:_labels_and_copies`; `jobs/library_items.py:label_photo`; `jobs/library_items.py:needs_product_only` |
| 84 | 56 | Position bias in pairwise judges | CE | `jobs/clip_judge.py:_frames_shown`; `jobs/fix_check.py:settle_all` |
| 85 | 57 | Native video input vs frame sampling | CE VM | `jobs/clip_judge.py:cut_pictures` |
| 86 | 58 | Feedback-driven retries (re-ask with the error) · habit | PG PT PI PC VW LR | `jobs/watcher.py:_asked`; `jobs/cut_out.py:redraw_with_check`; `jobs/checked_pictures.py:made_and_checked`; `agents/scene_agent/lifecycle.py:_told`; `jobs/work.py:_rewrite_scene` |
| 87 | 116 | Best-of-n with a verifier (rejection sampling) | PI LR | `jobs/checked_pictures.py:made_and_checked`; `jobs/checked_pictures.py:tried_before`; `jobs/work.py:_fit_for`; `jobs/work.py:_new_line` |
| 88 | 117 | Code-based (deterministic) graders before LLM judges | PC LR | `jobs/recolour.py:recolour`; `jobs/reference_photos.py:clean_photo_of`; `gateway/gateway.py:_recorded`; `jobs/work.py:_shorter_line` |
| 89 | 118 | Generation loss across chained edits | PI | `gateway/openai_adapter.py:OpenAIProvider.draw`; `jobs/work.py:person_and_room`; `agents/scene_agent/draw_starting_picture.py:make_picture` |
| 90 | 59 | Pixel-preserving segmentation · deferred in #273 · habit | PG PI PC | `jobs/product_only.py:_cut`; `jobs/recolour.py:recolour`; `jobs/owner_answers.py:_clean_picture`; `jobs/face_swap.py:for_clip` |
| 91 | 119 | Negative caching (a stored failure needs an expiry or a key) | PC | `jobs/product_only.py:_unusable`; `jobs/product_only.py:_cut`; `gateway/gateway.py:call_model` |
| 92 | 60 | Normalization (one row per real thing) | PT | `jobs/owner_parts.py:_sharing` |
| 93 | 61 | Identity consistency check (face and voice embeddings) · habit | PT PI PC VW | `jobs/face_swap.py:_check`; `jobs/work.py:person_and_room`; `agents/scene_forms.py:video_prompt`; `jobs/work.py:_how_the_voice_sounds` |
| 94 | 62 | Human-in-the-loop approval as a structured action · habit | PT PI LR | `agents/producer.py:ApproveOwnerPart.run`; `jobs/owner_parts.py:approve`; `jobs/owner_parts.py:draw_part_picture`; `agents/producer.py:RunPlanningChecks.run` |
| 95 | 63 | Content-addressed keys (input fingerprints) · habit | DW AC PA PI PC VM | `jobs/stale.py:is_stale`; `jobs/models.py:SceneStep.snapshot`; `agents/producer.py:assemble_the_ad`; `jobs/cards.py:card_key`; `jobs/work.py:note_face`; `jobs/work.py:_edited_once`; +2 more in the reports |
| 96 | 120 | Reasoning effort per task (and an output cap) | PA | `gateway/openai_adapter.py:OpenAIProvider.complete`; `gateway/catalog.py:THINKING_LEVEL_FOR_PURPOSE` |
| 97 | 121 | Model routing (right-size the model per purpose) | LR | `gateway/catalog.py:MODEL_FOR_PURPOSE`; `gateway/catalog.py:PRICE_PER_MILLION_TOKENS`; `tests/test_frozen_rules.py:MOVED_TO_6_1_SOL` |
| 98 | 122 | Image tokenization and detail levels | PA | `gateway/gateway.py:MAX_IMAGE_SIDE`; `gateway/openai_adapter.py:_image_part`; `jobs/photos.py:parts` |
| 99 | 123 | Contradictory instructions in a prompt built up over time | PA | `jobs/photos.py:PICK_INSTRUCTIONS`; `jobs/photos.py:PickedPhotos`; `jobs/photos.py:picked_links` |
| 100 | 124 | Negation in prompts (the pink-elephant problem) | VM | `agents/scene_agent/lifecycle.py:BANNED`; `agents/scene_agent/film.py:Film.run`; `agents/scene_forms.py:video_prompt` |
| 101 | 125 | Prompt upsampling (the provider's prompt rewriter) | VM | `jobs/work.py:sent_handoff`; `agents/scene_forms.py:VideoForm` |
| 102 | 126 | Reference leakage (a reference gives more than its job) | PI | `jobs/scenes.py:PERSON_IN_ROOM_PROMPT`; `agents/scene_agent/draw_starting_picture.py:make_picture` |
| 103 | 127 | Open-set classification (an 'other' value) | PA | `jobs/photos.py:photo_label_for`; `jobs/colours.py:answer`; `agents/scene_agent/draw_starting_picture.py:swapped` |
| 104 | 128 | Word-level confidence scores | VW | `gateway/elevenlabs_adapter.py:ElevenLabsProvider.transcribe`; `gateway/types.py:Word`; `jobs/word_check.py:check` |
| 105 | 129 | Audio event classification (music and singing) | VW | `agents/scene_forms.py:video_prompt`; `jobs/work.py:MUSIC_PROMPT`; `gateway/fal_adapter.py:FalProvider.compose` |
| 106 | 130 | Audio-visual sync scoring (lip-sync metrics) | VM | `jobs/clip_judge.py:check_words`; `jobs/clip_judge.py:cut_pictures`; `video-eval/blind.py:RULES` |
| 107 | 64 | Optimistic concurrency control | AC | `chat/api.py:use_version`; `jobs/card_versions.py:use` |
| 108 | 65 | Bulkheads (queue isolation) | DW | `agents/tasks.py:_work_until_it_stops` |
| 109 | 131 | Bounded parallel fan-out | PA PC | `jobs/work.py:save_photos`; `jobs/library_items.py:add_kept_photos`; `agents/scene_agent/draw_starting_picture.py:make_picture`; `agents/scene_agent/draw_starting_picture.py:swapped` |
| 110 | 132 | Delta debugging with cheap probes | PA | `jobs/work.py:_past_refused_tiles`; `jobs/work.py:_picked_past_the_safety_filter`; `gateway/gateway.py:refused_by_halving` |
| 111 | 66 | Expand/contract migrations · habit | DW VM AR | `agents/tasks.py:_nudge`; `agents/pause.py:pause`; `agents/card_picks.py:assemble_after_swaps`; `jobs/work.py:_clip_asked_for`; `jobs/work.py:go_back` |
| 112 | 67 | Immutable, pinned build artifacts | DW | config only: `deploy/backend.Dockerfile`, `deploy/compose.yml`, `deploy/deploy.sh` |
| 113 | 68 | Durable execution | DW AR | `agents/tasks.py:carry_on_after_the_worker_starts` |
| 114 | 133 | Anti-corruption layer (a provider-neutral clip request) | VM | `agents/scene_forms.py:video_prompt`; `gateway/types.py:BrollClipHandoff`; `gateway/catalog.py:MODEL_FOR_PURPOSE` |
| 115 | 134 | Model inventory (one record per AI use) | AR | `gateway/catalog.py:MODEL_FOR_PURPOSE`; `jobs/work.py:judge_starting_picture`; `jobs/work.py:_clip_purposes` |
| 116 | 69 | A/V sync tolerance budget | AC | `jobs/assembly.py:whole_clips` |
| 117 | 70 | Property-based testing | AC | `jobs/assembly.py:captions`; `jobs/assembly.py:whole_clips` |
| 118 | 71 | Forced alignment | AC VW | `jobs/assembly.py:timed_script` |
| 119 | 72 | Caption chaining (minimum gap and duration) | AC | `jobs/assembly.py:captions` |
| 120 | 135 | Minimal edits (ask for the changes, not the whole) | LR | `jobs/work.py:_shorten`; `jobs/models.py:Scene.change_line`; `jobs/checks.py:shortened_script_for` |
| 121 | 73 | Typed records at the storage boundary (tolerant reader) | AC | `jobs/models.py:ProducedItem.cuts`; `agents/producer.py:assemble_the_ad` |
| 122 | 74 | Temporal grounding in video | PG | `jobs/product_videos.py:cut_pieces` |
| 123 | 75 | SSRF defence (egress proxy) | PG | `jobs/page.py:check_where_it_points`; `jobs/product_videos.py:_player_file` |
| 124 | 76 | Indirect prompt injection | PG PA | `jobs/photos.py:handoff`; `jobs/work.py:copy_page_text`; `agents/producer.py:ReadPage.run` |
| 125 | 136 | AI disclosure and content provenance (C2PA) · known | PI | `jobs/work.py:create_person`; `jobs/face_swap.py:SWAP_PROMPT` |
| 126 | 77 | CSRF with ambient credentials, and real auth later | CF | `chat/api.py:sessions` |
| 127 | 78 | N+1 queries and query-count tests | CF | `chat/api.py:scenes`; `jobs/cards.py:card_url` |
| 128 | 79 | Rows, not a growing JSON array | CF AR | `jobs/notices.py:post_notice`; `jobs/word_check.py:check` |
| 129 | 80 | Direct-to-storage uploads | CF | `chat/api.py:_send`; `adforge/file_store.py:save_upload` |
| 130 | 81 | Optimistic updates | CF | `frontend/src/Chat.tsx:Chat` |
| 131 | 82 | A server-state library (TanStack Query) | CF | `frontend/src/ScenePanel.tsx:ScenePanel`; `frontend/src/App.tsx:App` |
| 132 | 83 | Server-sent events | CF | `chat/api.py:session_messages`; `chat/messages.py:add` |
| 133 | 84 | Streaming agent output to the chat | CF | `agents/loop.py:_work` |
| 134 | 85 | State colocation and memoized lists | CF | `frontend/src/Chat.tsx:Chat` |
| 135 | 86 | Unfaithful explanations (the `why` isn't the cause) | PL | `agents/loop.py:WHY`; `evals/agent_page.py:_call` |
| 136 | 87 | Semantic deduplication | PT | `jobs/planning.py:same_action`; `jobs/plan_edits.py:_check_the_actions` |

### How concepts were merged

**Run 1**
- **Idempotency:** GW 1, CF 4, DW 1 and AC 15. A repeat must not do the work twice.
- **Timeouts:** GW 4, DW 4 and CF 7.
- **Short transactions:** PG 5 and CF 8.
- **Append-only, immutable records:** PL 4, CF 3 and AC 8.
- **Contract and integration tests:** GW 6, CF 6 and DW 7.
- **Pairs:**
  - anchoring: PL 3 + CE 4;
  - tagged unions: PT 3 + CF 9;
  - feedback-driven retries: PG 7 + PT 9;
  - content-addressed keys: DW 11 + AC 7;
  - constrained decoding: PT 5 + PG 7;
  - text normalization: PT 4 + AC 12.

**Run 2**
- **Eval–production skew:** PA 1 + PC 1 (#93).
- **Fail-open vs fail-closed:** VW 3 + PI 6 + PC 4 (#89).
- **Online evaluation:** PI 4 + PC 3 + VW 5 (#109).
- **Pairs:**
  - best-of-n: PI 7 + LR 8 (#116);
  - code-based graders: PC 6 + LR 6 (#117);
  - bounded fan-out: PA 9 + PC 7 (#131).
- **Into run-1 concepts:**
  - LR 1 (one length model) joined #13, single source of truth.
  - VW 7 (speaker embeddings) joined #61, identity consistency.

---

## Bugs and loose ends found while reading

Not concepts. Each would be its own fix, outside this teaching run. Each report also ends with
its own list.

**Run 2**
- **Nothing stops assembly or a version swap while a scene agent is fixing a scene.**
  - The busy checks look for a running clip step.
  - The scene agent's try steps are created already finished.
  - Cites: `agents/producer.py:assemble_the_ad`; `agents/scene_agent/lifecycle.py:_try_step`.
  - Status: AR read it; I confirmed the guard.
- **A shop owner's own line under 10 words is rewritten by a model, with no notice.**
  - Cite: `jobs/work.py:_fit_for`.
  - Status: LR reproduced it in a scratch test.
- **A failed or refused H3 render posts no notice.**
  - It only reaches the scene agent as a tool answer.
  - Cites: `agents/scene_agent/film.py:Film.run`, `agents/loop.py:_run`.
  - Found by: VM.
- **A refunded render is counted as spent.**
  - Cite: `gateway/gateway.py:submit_broll_clip`.
  - Found by: VM.
- **The grading export reads the old clip-step shape.**
  - Each scene shows only its first try, listed twice as remake 1.
  - Cite: `evals/round_export.py:_first_clip_step`.
  - Found by: AR.
- **A refused before-picture fails every scene step, with no notice.**
  - Nothing can draw it again.
  - Cite: `jobs/work.py:_before_picture`.
  - Found by: PI.
- **The line goes into H3's speech tags unescaped.**
  - A `<` or `]` in a line may break the tag.
  - Cite: `agents/scene_forms.py:video_prompt`.
  - Found by: VW.
- **Four catalog purposes have no live caller:** `make_starting_picture`,
  `check_starting_picture`, `fix_scene`, `scene_picture_check`.
  - Cite: `gateway/catalog.py:MODEL_FOR_PURPOSE`.
  - Found by: PC and AR.

**Run 1** (the first four re-checked on `f87430b`; they still hold)
- **The chat drops a message** posted between the last poll and the owner's send.
  `frontend/src/Chat.tsx:Chat`. CF reproduced it in a scratch copy; I confirmed the code.
- **The chat keeps showing "working"** after the producer's stop notice.
  `frontend/src/Chat.tsx:Chat`, `agents/loop.py:say_it_stopped`. CF reproduced it; I confirmed it.
- **The producer stops answering** after a card swap whose re-assembly keeps failing.
  `agents/producer.py:assembled_again`. AC read it from the code; I confirmed it.
- **A redelivered step can run beside the first copy.** `agents/tasks.py:run_scene_step`. DW
  read it from the code; I confirmed it.
- **HeyGen's paid POST is sent again on a 5xx.** Gone: HeyGen was retired by ADR 0012.
- **`assembly.captions` raised `ZeroDivisionError`** on a scene with no timed words (AC, run locally on
  `bfc4dfc`; not re-checked after ADR 0012).
- **Docs and code disagree:**
  - `docs/rate-limits.md` says retries wait 2 s + 8 s; the code waits 5/20/60/120 s (GW).
  - The `picture_check.py` docstring names callers it doesn't have (PT).
  - `AssembleAd` tells the producer the captions are "the words as spoken" (AC).
  - `ProducedItem.cuts` help text lists 6 of its 11 keys (AC).
- **Unused:**
  - `settings.FAL_QUEUE_URL` (GW).
  - `SceneStep.Status.stopped` is never set (DW).
- **Card pictures turn transparent page PNGs black** (`jobs/cards.py:_card_picture`, AC).
