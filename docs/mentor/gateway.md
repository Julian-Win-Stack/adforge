# Design mentor: gateway, paid services & money

**Code read:** `backend/gateway/` (gateway, catalog, types, models, every adapter, fake),
`backend/adforge/retry.py`, `backend/adforge/file_store.py`, `backend/agents/money.py` and its
readers (`agents/snapshot.py`, `agents/fold.py`), the clip step in `backend/jobs/work.py`,
`scripts/ad-runs/run.py` (read only).

**Design read:** `docs/rate-limits.md`, `real-api-replies.md`, `video-model-tests.md`,
`creatify-problems.md`, `plans/broll-boreal-h3.md`; CONTEXT.md Gateway, Handoff, Model call;
#1 Stack, AI services, Reliability; #210 Contracts; #247 Picture money fallback, Contracts;
#272 Part 3. #327 (approved, not built) is context only.

**How this area is tested today**
- Real adapters against local stand-in servers (`pytest-httpserver`) replaying replies recorded
  once: `tests/test_providers.py`, with `docs/real-api-replies.md` and `tests/creatify/*.json`.
- Everything above the adapters meets one scripted fake at the gateway seam:
  `gateway/fake.py:FakeModel`, swapped in by `gateway.py:use_model`.
- Gateway rules (handoff checks, billing, waiting for clips, asking again):
  `tests/test_gateway.py`. Money fallback: `tests/test_pictures_on_azure.py`.
- Restarts that must not pay twice: `tests/test_producer_restarts.py`. They stop the worker
  after a call's record is written, never between the provider charging and the record.

## Ranked

| # | Concept | Tag |
|---|---|---|
| 1 | Idempotency keys (record a paid request before sending it) | **must apply now** |
| 2 | Cost reconciliation (recorded cost vs the provider's bill) | **must apply now** |
| 3 | Webhooks instead of polling, with a bounded wait | good to know |
| 4 | Retry budgets and deadlines | good to know |
| 5 | Circuit breaker | good to know |
| 6 | Contract tests against the real services | good to know |

---

## 1. Idempotency keys (record a paid request before sending it) · must apply now

**What it is**
- Every paid request gets a key that names it.
- Before sending, write "about to send request K" to your own database, and commit.
- After any unclear end (timeout, lost reply, crash), look K up, at the provider or in your
  record. Don't guess. The thing is then paid for at most once, and never lost.
- Textbook case: Stripe's `Idempotency-Key` header. A charge sent again with the same key
  hands back the first charge.

**Why it matters here**
- **The record comes after the money.** `gateway.py:_recorded` writes the `ModelCall` only
  once `make()` returns. If a worker stops between Creatify's answer and that insert, the clip's
  id is lost. The restart finds no record (`jobs/work.py:_clip_asked_for`) and pays again.
- **A lost reply drops a clip that may already be made.** On a reply lost after the POST,
  `creatify_adapter.py:CreatifyProvider._send` raises `ClipFailed`. Creatify may still make the
  clip and charge for it, but it is never collected, and it is recorded FAILED with no cost.
  The scene agent may then film again. That is two payments, and only the second shows in the
  records.
- **HeyGen sends a paid POST again on 500, 502 and 504.** `heygen_adapter.py:HeyGenProvider._send`
  raises `OutsideServiceDown` for any 5xx, so `retry.py:with_retries` sends it again. Creatify's
  adapter refuses exactly this case (`test_providers.py:test_a_clip_creatify_errors_on_isnt_asked_for_again`).
  No test covers HeyGen.
- **Paid calls that time out are tried again.** In `openai_adapter.py:_asked`, `APITimeoutError`
  is a subclass of `APIConnectionError`, which counts as worth retrying. So a picture that takes
  over 120 s is drawn again, up to 5 tries, and each try may be billed. Producer turns
  (`OpenAIProvider.take_turn`) work the same way, and a big turn costs about $1 at $2 per million
  input tokens. Speech, transcripts and music too: `httpx.TransportError` covers read timeouts.
- **Music is made and fetched in one try.** `fal_adapter.py:FalProvider.compose` makes the
  music and downloads it in the same attempt, so a failed download makes and pays for the music
  again. Clips already split the two (`submit_*`, then `collect_clip`).
- **`pay_once` only works after the fact.** `gateway.py:_answered_before` compares the whole
  handoff as JSON, with no index, and only once a record exists.
- Under #327 every scene is an H3 clip, so this path covers every scene. The HeyGen bug goes
  away with HeyGen.

**How to apply it**
- Creatify takes no idempotency key: `POST /api/boreal/` has none, per docs.creatify.ai read
  Nov 3. It does list tasks: `GET /api/boreal/?model=boreal-h3&created_after=<time>` returns each
  task's `id`, `prompt` and `status`. Use that list as the lookup.
- **Write the intent first.** Before the provider call, commit a `ModelCall` with:
  - outcome `sending` (new);
  - `key` = sha256 of purpose + handoff + images, under a unique index;
  - `sent_at`.

  When the call ends, update that same row.
- **Add a third outcome, `unknown`** ("sent, reply lost"). Count it as maybe paid, never as
  failed.
- **On `unknown`, or a restart that finds a `sending` row:** list Creatify's tasks created
  after `sent_at` minus 1 min, with the same prompt. One found: collect it. None: it is safe to
  send again.
- **Give `pay_once` the same key**, so it becomes an indexed lookup. The unique index also stops
  two identical calls that are in flight at once from both paying. Today only the step locks
  (another area) prevent that.
- **Split paying from fetching for music.** Record fal's `audio.url` before downloading, so a
  failed download fetches again for free. `settings.FAL_QUEUE_URL` (fal's queue) is set and
  unused.
- **Long OpenAI calls** (clip judge, look, big producer turns): Responses `background: true`
  hands back an id at once. Waiting then costs nothing, and a restart collects by id. Check
  first that the Azure deployment supports it.
- **HeyGen:** stop retrying a paid POST on 500, 502 and 504, or leave it, since #327 deletes HeyGen.
- **Test** (adapter, stand-in server): Creatify takes the POST, the reply is lost, and the
  list shows the task. Expect the clip collected and one `make_broll_clip` charge.

**One real post**
- Brandur Leach, "Implementing Stripe-like Idempotency Keys in Postgres" (2017). It commits a
  recovery point before each outside call, so a retry after a crash picks up where it stopped
  instead of charging twice. https://brandur.org/idempotency-keys

---

## 2. Cost reconciliation (recorded cost vs the provider's bill) · must apply now

**What it is**
- Cost worked out from a price list is an estimate.
- Reconciliation compares it, on a schedule, with what each provider says it charged, and raises
  an alert when the two drift apart.
- When you have both, the bill is the number to trust.

**Why it matters here**
- `ModelCall.cost_usd` is always worked out (`catalog.py:cost_usd`, `video_cost_usd`), never
  read from a bill. Spec #1 calls these records "the only source of the cost and time numbers".
  They drive:
  - the paid-run ceiling (`scripts/ad-runs/run.py:spent`, `per_ad`, `--pause-at`, `--ad-hold`);
  - the producer's "Spent in this chat" (`agents/snapshot.py:_spent`);
  - the costs the owner is told.
- **Signs the numbers are off today:**
  - **H3's price.** `catalog.py:PRICE_PER_SECOND_OF_VIDEO` says $0.0792 a second: 0.4 credits at
    $99 for 500 credits (Starter plan, Oct 3). Creatify's docs on Nov 3 say 0.13 credits a second
    at 768p. Pro is $299 for 2,000 credits, and `docs/rate-limits.md` infers the account is on
    Pro. #327 says Creatify's sessions showed 0.2. So the recorded H3 cost may be 2 to 4 times
    the bill.
  - **Failed clips.** A clip Creatify fails or rejects reports `credits_used: 0`, but
    `gateway.py:submit_broll_clip` already recorded it as paid when it was asked for.
  - **Lost replies.** A paid call whose reply was lost is recorded with no cost
    (`gateway.py:_recorded`, its failure branch). The run ceiling can then be passed without
    `run.py` noticing.
  - **Cached input.** It is priced as full input: `openai_adapter.py:OpenAIProvider.take_turn`
    reads only `input_tokens`.
  - **A known price change.** Gemini's price doubles on 2027-01-01 (comment in
    `catalog.py:PRICE_PER_MILLION_TOKENS`), and nothing switches it.
- `creatify_adapter.py:CreatifyProvider.status` already fetches the task record that holds
  `credits_used`, then ignores it.
- #327 says the same thing itself: the H3 price needs checking against a real bill before the
  first round's estimate.

**How to apply it**
- **Read the bill per call where the provider gives it.** Store Creatify's `credits_used` on the
  collect call once the clip is `done`. It can change until then.
- **Check each run with a free script.** Compare against `Sum(cost_usd)` for each provider:
  - Creatify: `GET /api/remaining_credits/` before and after the run;
  - HeyGen: the wallet;
  - OpenAI: the daily costs (Costs API, needs an admin key).

  A gap over about 10% posts a developer notice, and the catalog price gets fixed.
- **Count `unknown` calls (concept 1) at full price in the ceiling**, so a clip that may be
  paid still counts toward it.
- **Store cached tokens** beside `input_tokens` and price them at their own rate.
- **Give each price a start date**, so January's Gemini price applies on its own.
- **Test:** a unit test of the comparison (pure logic), and an adapter test that
  `credits_used` from a recorded Creatify reply lands on the record.

**One real post**
- Langfuse docs, "Token & Cost Tracking". Langfuse is already in the stack. It says costs are
  either *ingested* (from the provider) or *inferred* (from a price table), and "ingested values
  take priority over inferred ones". OpenAI's input count includes cached tokens, so those must
  be split out to be priced correctly.
  https://langfuse.com/docs/observability/features/token-and-cost-tracking

---

## 3. Webhooks instead of polling, with a bounded wait · good to know

**What it is**
- You ask for a long job, and then either the provider calls you when it's done (a webhook), or
  you check now and then without a process sitting idle.
- The wait has an end. Past a deadline the job becomes "check again later", not "wait forever".

**Why it matters here**
- `gateway.py:collect_clip` calls `status()`, then `time.sleep(CLIP_POLL_SECONDS)`, in a loop
  with no deadline. Spec #1 says the clip is "waited for until it is made or the video model says
  it can't be, however long that takes". So each clip being made holds one Celery process for its
  whole render.
- Azure runs 48 processes (`deploy/compose.yml`, `WORKER_CONCURRENCY`). #327's 12-ad round at
  about 5 clips an ad could have about 60 waits at once. That is more than 48, so planning and
  pictures queue behind processes that are only sleeping.
- A task stuck in Creatify's `in_queue` holds its process until Creatify forgets the task. Its
  videos stay "at least 7 days".
- Under #327 every scene is an H3 clip, so every scene waits this way.

**How to apply it**
- **First, the cheap step: add a deadline** (say 30 min). Past it, the step records "still
  being made, checking later" and frees its process. A short Celery task checks again with
  `countdown` (the durable-work mentor's area). Creatify keeps the video at least 7 days, so
  nothing is lost.
- **Then use Creatify's `webhook_url`** ("URL that receives the result when the video is
  ready").
  - The docs show no signature and no delivery retries. So treat a webhook as a hint: on
    receipt, call `GET /api/boreal/{id}/` and trust what that says.
  - Keep the slow re-check as a backstop.
- **Test:** script the fake's `collect_clip` as `{"state": "working"}` past the deadline.
  Expect the process freed, the clip kept on a later check, and one charge.

**One real post**
- Shopify docs, "About webhooks": "Webhook delivery isn't always guaranteed", so "use
  reconciliation jobs to periodically fetch data". https://shopify.dev/docs/apps/build/webhooks

---

## 4. Retry budgets and deadlines · good to know

**What it is**
- **A retry budget** caps how many retries a client makes, so they can't pile load onto a
  service that is already failing.
- **A deadline** caps the total time one call may take, across all its tries.
- Retry in one layer, not in every layer.

**Why it matters here**
- **Retries sleep inside the worker.** `retry.py:with_retries` waits 5, 20, 60 and 120 s: 5
  tries and about 3.5 min of waiting (`settings.py:RETRY_DELAYS_SECONDS`).
- **No total limit.** Each try has its own 120 s timeout (`catalog.py:ANSWER_SECONDS`), and
  nothing caps all tries together. A picture that keeps timing out holds a worker for about
  13 min.
- **Layers multiply.** `gateway.py:_remade_past_the_filter` (3 makes) wraps `with_retries`
  (5 tries), and `call_model`'s ask-again doubles that: up to 30 attempts for one `call_model`.
  `gateway.py:past_the_safety_filter` adds a call per picture on top.
- **Nothing is shared.** In an outage, every running step retries 5 times on its own. At 12 ads
  at once, that is what hits OpenAI's 20 pictures a minute.
- **A stale doc.** `docs/rate-limits.md` still says retries wait "2 s + 8 s (settings.py:190)".
  The code waits 5/20/60/120 s (`settings.py:RETRY_DELAYS_SECONDS`).
- #272 Part 3 dropped more outside-service retries, so `with_retries` is the whole retry policy.
- Under #327, Inworld (5 requests at once) goes, which removes one of the tightest limits.

**How to apply it**
- **One deadline per call** (e.g. 5 min), checked before each retry.
- **A per-provider retry budget** in the database or Redis. For example, retries may be at most
  10% of the provider's calls in the last minute. Past that, fail at once with
  `OutsideServiceDown`.
- **Count filter remakes against the same budget**, so the layers add instead of multiply.
- **Set each purpose's timeout from measured times.** `ModelCall.duration_ms` already holds
  the p99 for each purpose.
- **Test:** unit tests of the budget and the deadline (pure logic), with `retry._sleep` faked
  as today.

**One real post**
- Marc Brooker, AWS Builders' Library, "Timeouts, retries, and backoff with jitter" (2019).
  It covers:
  - retrying at one point in the stack ("243x" the load when every layer retries);
  - a token bucket for retries;
  - timeouts set from latency percentiles;
  - no retries of calls with side effects unless they are idempotent.

  https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter

---

## 5. Circuit breaker · good to know

**What it is**
- A switch for each outside service.
- After enough failures it "opens": calls fail at once instead of waiting.
- After a pause it lets one trial call through ("half-open"). If that works, it closes again.

**Why it matters here**
- **Nothing remembers that a service is down.** Each call finds out on its own, after 5 tries
  (`retry.py:with_retries`), and holds a worker while it does.
- **The money fallback is a breaker with no way back.** After one `RefusedForMoney`,
  `gateway.py:_picture_made` moves a session's pictures to Azure (`_refused_for_money_before`).
  It never tries OpenAI again in that session, even after a top-up. Azure draws 2 a minute
  (`settings.py:AZURE_PICTURES_PER_MINUTE`), so the rest of that ad's pictures are slow.
- Under #327 every scene depends on Creatify, so an H3 outage stalls the whole ad, not just its
  B-roll.

**How to apply it**
- **Keep the state per provider, read from `ModelCall` rows.** They already hold `provider`,
  `outcome` and `created_at`. N `OutsideServiceDown` errors within M minutes opens the breaker.
- **When open:** fail at once with a typed error, and post one owner notice: "the video
  service is down; I'll carry on when it's back". That follows the no-silent-failures rule.
- **Half-open:** after T minutes, let one call through.
- **The money switch:** after T minutes on Azure, try one picture on OpenAI again.
- **Test:** script `OutsideServiceDown` N times in the fake for one purpose. Expect the next
  call to fail at once and one notice posted.

**One real post**
- Damian Polan, Shopify Engineering, "Your Circuit Breaker is Misconfigured" (2020). It covers
  error thresholds and half-open timeouts, and shows how an untuned breaker leaves workers
  waiting on a dead service. https://shopify.engineering/circuit-breaker-misconfigured

---

## 6. Contract tests against the real services · good to know

**What it is**
- Fakes and recorded replies show how a provider answered once.
- A contract test calls the real provider now and then, on free endpoints, and checks that the
  replies still match what the fakes assume.
- A failure doesn't break the build. It opens a task to update the fakes and the code.

**Why it matters here**
- The adapters are tested against replies recorded once (`tests/test_providers.py`,
  `docs/real-api-replies.md`, `tests/creatify/*.json`). Everything else meets
  `gateway/fake.py:FakeModel`. Nothing checks that those replies still hold.
- **Drift has already happened:**
  - HeyGen's "clip not found" code changed (`heygen_adapter.py:HeyGenProvider.status` accepts
    both).
  - fal's `storage_type=gcs` upload stopped working (`docs/real-api-replies.md`).
  - Creatify's published H3 price no longer matches the catalog (concept 2).
  - Inworld's "account full" reply was never recorded, so `inworld_adapter.py:_account_full`
    always returns False.
- Under #327, H3 returns clips with sound and Scribe hears them. Those are new reply shapes,
  recorded once again.

**How to apply it**
- **One free script**, run before each paid round and daily on the VM. It calls Creatify
  `GET /api/boreal/?limit=1` and `GET /api/remaining_credits/`, Gemini's model list, and a
  HeyGen status of a known id. It compares each reply's fields with the recorded ones, and a
  mismatch posts a developer notice.
- **Keep it out of CI.** It needs keys, and it belongs in the checks before a paid run.
- **Have the same script read the credit balances**, which serves concept 2 too.

**One real post**
- Martin Fowler, "Contract Test" (2011). Test doubles drift from the real service, so run
  contract tests at "the rhythm of changes to the external service". A failure starts a task to
  fix the doubles. It doesn't break the build. https://martinfowler.com/bliki/ContractTest.html

---

## Also seen here

- **1. Stopping conditions (spend caps).** The gateway checks no spend before a paid call
  (`gateway.py:submit_broll_clip`, `_picture_made`). The only money ceiling runs outside the
  app, polling recorded costs (`scripts/ad-runs/run.py:spent`). `agents/money.py:cost_of` adds
  them up, but nothing compares the total to a cap. Known limitation (`docs/limitations.md`):
  "Nothing limits how often the producer retries a failing step but the 100-calls-per-message
  cap."
- **4. Stable prompt prefix (prompt caching).** `openai_adapter.py:OpenAIProvider.take_turn` and
  `complete` read only `usage.input_tokens`, never the cached part. `catalog.py:cost_usd` prices
  all input at full rate, so caching savings never show in the costs.
- **7. Explicit state machine.** A paid clip's state (asked, being made, fetched, failed, given
  up while the service was down) is rebuilt from two `ModelCall` rows and error-text prefixes
  (`jobs/work.py:_clip_asked_for`, `error__startswith="ClipFailed:"`).
  `gateway/models.py:ModelCall.Outcome` has only succeeded and failed. The money switch also
  reads a prefix (`gateway.py:_refused_for_money_before`), so renaming an exception class breaks
  both with no type error.
- **8. Leases.** `gateway.py:_one_azure_picture_at_a_time` holds a session-level Postgres
  advisory lock for a whole picture call (up to 5 tries of 120 s). It waits for the lock with no
  timeout and releases it only when the call ends or the connection dies.
- **9. App-side rate limiter.** `gateway.py:_wait_for_azures_pace` paces Azure pictures to 2 a
  minute from the last call's record, under that lock. Nothing paces OpenAI pictures (20 a
  minute), Inworld (5 at once) or fal (2 at once); `docs/rate-limits.md` says so too ("no
  app-side limiter").
