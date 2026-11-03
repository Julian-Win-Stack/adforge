# Design mentor: chat, API and frontend

What this area's design is missing, ranked by how much it hurts this build now.
The design read: `README.md`, `CONTEXT.md` (Session, Message, Notice, Developer record, Attachment,
Brief, Interrupt, Pause, Page panel, Clip version picker, Variant, System note), spec #272 Part 1,
spec #247 "Pause" and "Traces and the grading page", spec #1 "The chat", "Sessions",
"Interrupting and steering", "Production and progress", "What the user sees", "API and progress".

Teaching only. Each fix below is a code change, and anything that changes what the shop owner
sees needs Julian's yes first.

## Two bugs found while reading (reproduced)

Both were reproduced with a test run against the app's own fake backend, in a scratch copy of
`frontend/` (no repo file changed). The 163 tests already in the suite pass; the 2 new ones fail.

1. **A message that arrives just before the owner sends is never shown.** Code posts
   "Making scene 1's clip." after the last poll; the owner sends before the next poll; the notice
   never appears until a reload (concept 1).
2. **After the producer stops, the chat still says "The producer is working."** The stop line
   `say_it_stopped` posts is a notice, and the chat skips notices when it decides who has the
   floor (concept 2).

## How this area is tested today

- Backend: `backend/tests/test_chat_api.py` drives the real API with DRF's `APIClient` (send, poll
  `?after=`, rename, reopen, 409 while another session works, sent lists on clips).
  `test_who_sees_a_notice.py` checks every `Kind` lands in the chat or the developer record, never
  both, never neither. `test_pause.py`, `test_page_panel.py`, `test_card_versions.py` cover the
  three owner controls.
- Frontend: Vitest + Testing Library, 165 tests. `App.test.tsx:fakeBackend` is a hand-written
  copy of `chat/api.py` in TypeScript (it stubs `fetch`). Only one shape is shared with the
  backend: `backend/tests/fixtures/sent_list.json`, read by `frontend/src/sentListFixture.ts`.
- Gaps: no test runs the browser against the real backend; nothing checks the fake against the
  real responses; no test counts queries on the chat endpoints.

---

## 1. Cursor sync with a high-water mark — **must apply now**

**What it is**
- The client keeps a cursor: "I have everything up to #N". It asks for "after N".
- The rule: only move the cursor past a number once you hold every message up to it.
- Linear's sync engine works this way: every change gets an ordered ID, and a client sends "the
  ID of the last change it applied".

**Why it matters here**
- The backend works hard to keep this rule: `chat/messages.py:add` numbers messages under a row
  lock "so a poll could [not] see #12 before #11 is saved ... and never show #11".
- The frontend breaks it. `frontend/src/Chat.tsx:Chat` → `send` calls `show([sent.message])`, and
  `show` moves `lastSeq` to the sent message's number.
- So any message numbered between the last poll and the owner's send is skipped for good:
  the next poll asks `after=` the owner's message. A poll already on its way drops them too.
- This is an interrupt, a main flow (spec #1 stories 14-16): the owner writes while scenes are
  being made, which is exactly when notices are posted every few seconds.
- What the owner sees: a clip or "Making scene 2's clip" missing from the chat, and a scene card
  stuck on "Making the clip" (`scenes.ts:sceneCards` reads the card's state from those messages),
  until a reload.
- Reproduced: test "REPRO a notice posted after the last poll and before the owner's send is
  still shown" fails.

**How to apply it**
- In `send`, don't move the cursor. Either:
  - poll at once after the POST (the sent message comes back in order, a few ms later), or
  - show the sent message, but only move `lastSeq` when `sent.message.seq === lastSeq + 1`;
    otherwise poll at once.
- Pick: poll at once. One path moves the cursor: the poll.
- Keep the repro as a test in `App.test.tsx` (notice added, owner sends, notice shown).

**One real post**
- Linear, "Rebuilding Linear's delta sync read path":
  https://linear.app/blog/rebuilding-delta-sync-read-path

## 2. Single source of truth for server state — **must apply now**

**What it is**
- State the server owns (is the producer working? is it waiting on the owner?) should be read
  from the server, not guessed in the browser from other data.
- TanStack Query's docs put it plainly: server state "is persisted remotely in a location you may
  not control or own" and "can potentially become out of date".

**Why it matters here**
- `frontend/src/Chat.tsx:Chat` guesses: `lastSaid` is the newest message that isn't a notice;
  if it is the owner's, `producerHasTheFloor` is true, which shows the working dots and offers
  Pause (`pausable`).
- `agents/loop.py:say_it_stopped` (called from `agents/tasks.py:_nudge` after 2 nudges, and on the
  100-call cap) posts its "I stopped before ... Send me any message" line as a **notice**.
  The guess skips it, so the dots and the Pause button stay on a producer that has stopped.
- Reproduced: test "REPRO after the producer's stop notice the chat no longer says it is
  working" fails; the dots are still there.
- Same gap for a producer that died mid-turn: nothing is posted, the dots spin forever, while
  the server knows (`Session.producer_running` with a stale `producer_seen_at`).
- The server already does this for one field: `chat/api.py:session_messages` sends
  `X-Session-Paused` with every poll. The working state is the one it doesn't send.

**How to apply it**
- Compute one `status` on the server, sent with every poll like the pause header:
  `working` / `waiting_on_owner` / `paused` / `stopped` / `idle`, from `producer_running`,
  `producer_seen_at`, running `SceneStep`s, `paused_at`, and `notices.Kind.asks` notices.
- The chat renders dots, "Waiting for your answer" and Pause from `status` only. Delete the
  `lastSaid` / `waitingOnOwner` guesses.
- A new owner-facing state (say, showing "stopped") is Julian's call; making the dots match the
  server is a bug fix.
- Tests: a backend test that `status` is `stopped` after `say_it_stopped`; the fake backend
  returns `status`.

**One real post**
- TanStack Query, "Overview" (server state vs client state):
  https://tanstack.com/query/latest/docs/framework/react/overview

## 3. Immutable events in an append-only log — **must apply now**

**What it is**
- In an event log, an entry never changes after it is written. A change is a new entry.
- Then "everything after #N" is the whole truth, and replaying the log rebuilds the same state.
- Fowler: "Capture all changes to an application state as a sequence of events."

**Why it matters here**
- The chat is used as an event log: the poll fetches each message once (`?after=`), and the
  browser folds the messages into cards and panels (`scenes.ts:sceneCards`,
  `pagePanelState.ts:pagePanel`).
- But rows change after they are numbered, so an open chat and a reopened one disagree:
  - `agents/page_panel.py:_refuse` and `_keep` run `Message.objects.filter(pk=...).update(
    pasted_page_result=...)` on the owner's already-polled message.
    `pagePanelState.ts:pagePanel` carries a second code path for it: the result "can come on the
    pasted message itself (as a reopened chat reads it) or on code's notice about it (as an open
    chat ... sees it)".
  - `chat/api.py:MessageSerializer.get_tries` and `get_question` serialize "as it stands now"
    (`jobs/work.py:tries_shown`, `OwnerQuestion.answered_at`). A live chat keeps the values from
    when it fetched; a reload gets today's `in_ad` and `open`.
- `scenes.ts:sceneCards` promises "reopening a session gives the same cards as having watched
  it". With live fields inside old messages, it can't.

**How to apply it**
- Rule: a `Message` row is frozen once numbered. A test can enforce it (a `pre_save` check, or a
  test that no code path calls `.update()` on numbered messages).
- Pasted page: drop the `.update()`; the `PASTE_REFUSED` / `PASTE_KEPT` notice already carries
  the result. That is the right pattern already.
- Tries and questions: freeze what the notice says at post time; move "is the question still
  open" and "which try is in the ad" to the `/scenes/` endpoint, which is already the live view.
- Other way (Linear's): give a changed row a new number from the same counter (`changed_seq`)
  and poll on that. More general, more work. Pick: frozen rows plus `/scenes/`.

**One real post**
- Martin Fowler, "Event Sourcing": https://martinfowler.com/eaaDev/EventSourcing.html

## 4. Idempotency keys for POSTs — **must apply now**

**What it is**
- The client makes a unique key per action and sends it with the POST. If the same key comes
  again, the server returns the first result and does nothing twice.
- It covers the case a retry button can't: the server did the work, but the answer was lost.

**Why it matters here**
- `chat/api.py:_send` saves the message, saves the files and calls `wake_producer` every time.
- If the answer is lost on the way back (phone Wi-Fi), `Chat.tsx:Composer` shows "Couldn't reach
  the server. Try again." and keeps the draft. The owner taps Send: the note is saved twice and
  the producer reads it twice. A scene note read twice can start paid work twice (whether it
  does is up to the model).
- The test "says so when a message can't reach the server ..." only covers a send that never
  arrived (`dropNextSend` throws before saving), not one that arrived.
- `chat/api.py:use_version` replayed answers 409 "v2 is already in the ad": the swap worked,
  but the owner sees an error. `pause_session` is already safe to repeat (`agents/pause.py:pause`
  returns when already paused).

**How to apply it**
- `Composer` makes `crypto.randomUUID()` when a draft is first sent, keeps it for retries of that
  draft, drops it after success. Sent as an `Idempotency-Key` header.
- `Message.client_key` (nullable UUID), `UniqueConstraint(session, client_key)`.
- In `_send`, under the lock: if the key exists, answer 200 with that message; save no files,
  wake nobody.
- Same key idea for `use_version`, or let a replay answer the current versions instead of 409.
- Tests: the fake backend gets a "saved, then the answer drops" mode; a backend test posts twice
  with one key and finds one message.

**One real post**
- Stripe, "Designing robust and predictable APIs with idempotency":
  https://stripe.com/blog/idempotency
- The header's IETF draft: https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/

## 5. Error boundaries and browser error tracking — **must apply now**

**What it is**
- React: "if your application throws an error during rendering, React will remove its UI from
  the screen". An error boundary catches it and shows a fallback.
- Browser error tracking (Sentry's React SDK) sends that error, with the component stack, to the
  same place backend failures go.

**Why it matters here**
- AGENTS.md: "No silent failures." The frontend is the one place where failures are silent.
- `frontend/src/main.tsx` renders `<App />` with no boundary. One bad message shape in
  `scenes.ts:sceneCards` or `Chat.tsx:joinStarts` (which use `!` on `step` and
  `suggested_note`) blanks the whole app, with no line to the owner and nothing in Sentry.
- `ScenePanel.tsx:ScenePanel` swallows a failed `/scenes/` fetch with `() => {}`.
- `adforge/monitoring.py:start` sets up Sentry for Django and Celery only.

**How to apply it**
- `@sentry/react` in `main.tsx`, the same `environment` tag as the backend, no PII (matches
  `monitoring.start`), tag the session id.
- `Sentry.ErrorBoundary` around `Chat` (and one around `ScenePanel`), fallback: "This chat
  couldn't be shown. Reload." The sidebar stays usable.
- Report the swallowed `/scenes/` error and polls that keep failing.
- Rule clash to raise with Julian: AGENTS.md says the DSN lives only in `.env`. A browser DSN
  can come from `.env` at build time (`VITE_SENTRY_DSN`), but it ends up in the served JS. Sentry
  DSNs are made to be public, but that needs his OK.

**One real post**
- Sentry, React ErrorBoundary ("Reports errors to Sentry AND renders fallback UI"):
  https://docs.sentry.io/platforms/javascript/guides/react/features/error-boundary/
- React, error boundaries:
  https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary

## 6. Contract testing between frontend and backend — **must apply now**

**What it is**
- Contract testing checks that what the backend really sends matches what the frontend's tests
  assume, so a mock can't drift from the real API without a failing test.
- Ways to do it: shared golden JSON files, or a schema (OpenAPI) that both sides are typed and
  checked against.

**Why it matters here**
- `frontend/src/App.test.tsx:fakeBackend` reimplements `chat/api.py` in TypeScript ("The owner's
  message ends the pause, as chat/api.py does"). 165 tests trust it.
- `frontend/src/api.ts` types are copied by hand from the serializers; 7 `Message` fields are
  optional (`?`) because old and new shapes are mixed.
- Rename a field in `chat/api.py:MessageSerializer` and every frontend test still passes, while
  the real app breaks.
- The pattern already exists for one shape: `backend/tests/fixtures/sent_list.json` is checked by
  `test_library_contracts.py:test_the_sent_list_fixture_holds_a_left_out_a_trimmed_piece_and_a_fallen_back_recolour`
  and `test_chat_api.py:test_a_clip_in_the_chat_carries_what_it_was_sent`, and read by
  `frontend/src/sentListFixture.ts:sentListFixture`.

**How to apply it**
- Step 1 (no new tools): a backend test builds one session with every message kind through the
  real API and compares the poll's JSON with `tests/fixtures/chat_poll.json` (and `/scenes/`
  with `scenes.json`). The fake backend serves those, and its `add()` returns values typed
  `satisfies Message`.
- Step 2: drf-spectacular for an OpenAPI schema (each `SerializerMethodField` needs
  `@extend_schema_field`; `chat/api.py` has 7), committed and checked in CI; `openapi-typescript`
  generates the types `api.ts` uses. Its docs list "validating mock test data is up-to-date with
  the current schema" as a use.
- Pick: step 1 first.

**One real post**
- Martin Fowler / Ian Robinson, "Consumer-Driven Contracts":
  https://martinfowler.com/articles/consumerDrivenContracts.html
- openapi-typescript: https://openapi-ts.dev/introduction

## 7. Timeouts and exponential backoff on the poll — **must apply now**

**What it is**
- Every network call gets a deadline (`AbortSignal.timeout`), so a stuck call fails instead of
  waiting forever.
- Retries after a failure wait longer each time, with some randomness (jitter).
- A hidden tab stops polling (Page Visibility API) and polls at once when shown again.

**Why it matters here**
- Spec #1 chose polling because a held connection "can drop on public WiFi or behind proxies".
- `Chat.tsx:Chat`'s `ask()` schedules the next poll only after the last one settles, and `fetch`
  has no timeout. A half-open connection stalls the whole loop, and "Lost contact with the
  server" never shows, because no error is thrown.
- While the server is down, every open tab retries every 2 s, fixed.
- Hidden tabs keep polling (browsers throttle timers but don't stop them).

**How to apply it**
- `api.ts`: `fetch(url, { signal: AbortSignal.timeout(10_000) })` on polls and small POSTs;
  a long one for uploads.
- On poll errors: 2 s, 4 s, 8 s ... capped at 30 s, plus jitter; back to 2 s on success.
- `visibilitychange`: stop when hidden, poll at once when visible.
- Tests: the fake's `holdPolls()` already exists; add "a held poll times out and says Lost
  contact".

**One real post**
- AWS Builders' Library, "Timeouts, retries, and backoff with jitter":
  https://builder.aws.com/content/3EumjoZascWd1oZiEgL8ORlv3qE/timeouts-retries-and-backoff-with-jitter
- MDN, Page Visibility API (names polling as the use case):
  https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API

## 8. Short transactions, side effects after commit — good to know

**What it is**
- A transaction holds its locks until it ends. Keep slow work (files, network) out of it.
- Side effects that must only happen if the data is saved (a task, an email, a Sentry event) go
  in `transaction.on_commit`.

**Why it matters here**
- `chat/api.py:_send` takes the global advisory lock (`agents/tasks.py:hold_one_at_a_time_lock`)
  and then copies every upload into the file store (`adforge/file_store.py:save_upload`, up to
  3 × 100 MB videos; photos read whole into memory) before the message is saved.
- Every other send, every Pause press and every card swap waits on that lock meanwhile.
  Small on local disk today; seconds once media moves to object storage.
- A refused or failed save leaves the copied files behind (files don't roll back).
- `jobs/notices.py:post_notice` sends to Sentry after its own `atomic` block, but callers wrap it
  in theirs (`agents/page_panel.py:_refuse`), so Sentry can get an event for a notice that
  rolled back.
- Done right already: `agents/tasks.py:wake_producer` starts the task with `on_commit`.

**How to apply it**
- Save files first (names are unique), then lock only for the busy check, insert and wake.
  Delete the files if the send is refused.
- `post_notice`: `transaction.on_commit(lambda: monitoring.failed(...))`.

**One real post**
- GitLab, "Transaction guidelines" ("When a transaction acquires locks, it holds on to them until
  the transaction finishes"): https://docs.gitlab.com/development/database/transaction_guidelines/
- Django, `on_commit`: https://docs.djangoproject.com/en/5.2/topics/db/transactions/

## 9. Discriminated unions for message types — good to know

**What it is**
- Each message has a `kind` field and a payload whose shape depends on the kind.
- TypeScript narrows on the `kind` field, and a `never` check makes a new kind fail the build
  until every `switch` handles it. Pydantic does the same on the backend.

**Why it matters here**
- `chat/models.py:Message` has grown a column per feature: `step_*`, `suggested_note*`,
  `tries_of`, `owner_question`, `chosen_clip`, `pasted_page*`, `wants_pasted_page`,
  `wants_more_photos` (19 migrations in `chat/`).
- `chat/messages.py:add` and `jobs/notices.py:post_notice` each take 13+ keyword arguments that
  mirror them.
- The frontend works out what a message is from its shape, and the rules disagree:
  `Chat.tsx:isFinishedAd` (agent, no step, a video with no scene) and `scenes.ts:adFinished`
  (agent, exactly one attachment, a video with no scene) both mean "the finished ad".

**How to apply it**
- `Message.kind` (`said`, `step`, `tries`, `question`, `page_ask`, `paste_result`,
  `finished_ad`, ...) plus a `payload` JSONField checked by a Pydantic discriminated union
  (Pydantic already guards `SentList`).
- `api.ts`: `type Message = Said | StepNotice | TriesNotice | ...`; `Chat.tsx` renders with
  `switch (message.kind)`.
- A data migration sets `kind` on old rows. Do it when the next new message shape comes.

**One real post**
- TypeScript Handbook, "Discriminated unions" and "Exhaustiveness checking":
  https://www.typescriptlang.org/docs/handbook/2/narrowing.html
- Pydantic, discriminated unions: https://pydantic.dev/docs/validation/latest/concepts/unions/

## 10. N+1 queries and query-count tests — good to know

**What it is**
- N+1: one query for a list, then one or more per row. Fine at 10 rows, slow at 500.
- A query-count test (`django_assert_max_num_queries`) fails when someone adds one.

**Why it matters here**
- `chat/api.py:MessageSerializer.get_tries` runs `jobs/work.py:tries_shown` per tries notice
  (`versions()`, a `Scene` read, the fix record ...).
- `chat/api.py:AttachmentSerializer.get_sent` → `jobs/cards.py:card_url` checks
  `file_store.exists` for every picture of every clip: a disk check now, a network call per
  picture once media is in Blob storage.
- `chat/api.py:scenes` runs `work.versions` twice per scene and
  `work.says_the_line_as_it_stands` (2+ queries) per version, and `ScenePanel.tsx:ScenePanel`
  asks for it again on every new message, "Making scene N" notices included.
- Only one test file in `backend/tests/` counts queries (`test_safety_filter_turns.py`).

**How to apply it**
- Query-count ceilings on GET session, the poll and `/scenes/`, with a full 6-scene ad.
- Store each card picture's URL when `jobs/cards.py:make_cards` makes it, not on every read.
- `/scenes/` asked for only on messages that change a card (a clip, a step end, a swap).

**One real post**
- Valentino Gagliardi, "Detecting N+1 queries in Django with unit testing":
  https://valentinog.com/blog/n-plus-one

## 11. CSRF with ambient credentials, and real auth later — good to know

**What it is**
- CSRF: another site makes the owner's browser send a request to your app; the browser attaches
  credentials it holds by itself (cookies, and also a Basic auth password).
- Authentication says who someone is; authorization says which rows they may touch.

**Why it matters here**
- `adforge/settings.py` `REST_FRAMEWORK`: no authentication classes, `AllowAny`. DRF checks CSRF
  only for `SessionAuthentication` users ("anonymous requests may be sent without CSRF tokens").
- The only lock is Caddy's `basic_auth` (`deploy/Caddyfile`, `gate.caddy`). Microsoft's docs:
  Basic auth is "vulnerable to CSRF attacks ... the browser automatically sends them on
  subsequent requests".
- Exposure today is small: a forged form could create empty sessions (`POST /api/sessions/`); to
  send a message it needs a session UUID it can't read. Spec #1 chose "no authentication".
- When shop owners get accounts, every `get_object_or_404(Session, pk=...)` in `chat/api.py`
  must also filter by owner.

**How to apply it**
- Now, cheap: refuse unsafe requests without a custom header (`X-Requested-With`) or with a
  foreign `Origin`; a cross-site form can't set either.
- Later: Django sessions + `SessionAuthentication` (brings CSRF), `Session.owner`, and every
  lookup filtered by `request.user`.

**One real post**
- Microsoft Learn, "Basic Authentication in ASP.NET Web API" (the CSRF note):
  https://learn.microsoft.com/en-us/aspnet/web-api/overview/security/basic-authentication
- DRF, SessionAuthentication and CSRF: https://www.django-rest-framework.org/api-guide/authentication/

## 12. Optimistic updates — good to know

**What it is**
- Show the owner's action at once, marked pending, and reconcile when the server answers.
- React 19 has `useOptimistic` for this.

**Why it matters here**
- `Chat.tsx:Chat` → `send` shows the owner's message only after the POST returns, which includes
  copying uploads under the lock (concept 8). With a 100 MB video, the thread shows nothing but
  a spinner for that long.
- Spec #1 story 15: "I want my message acknowledged straight away".

**How to apply it**
- Add the message at once with the idempotency key from concept 4 and a "Sending" mark; swap it
  for the server's when its number arrives; on failure, "Not sent · Retry".
- The wording is Julian's call.

**One real post**
- React, `useOptimistic`: https://react.dev/reference/react/useOptimistic

## 13. A server-state library (TanStack Query) — good to know

**What it is**
- A library that owns fetching, caching, retries, polling and refetch after a change, so
  components don't hand-roll them.
- Its defaults: refetch on focus and reconnect; failed queries "retried 3 times, with exponential
  backoff".

**Why it matters here**
- Hand-rolled today: the poll loop with refs (`Chat.tsx:Chat`), a counter so an old `/scenes/`
  answer can't undo a newer one (`ScenePanel.tsx:ScenePanel` `asked`), swallowed errors, and a
  session list loaded once and never refreshed (`App.tsx:App`).

**How to apply it**
- `useQuery` for the session list and `/scenes/` (`refetchInterval`, invalidate after
  `putVersionInAd`).
- Keep the message log's own reducer: the append-and-cursor merge doesn't fit a query cache.
- Worth it when the next data feature lands, not as a rewrite.

**One real post**
- TanStack Query, "Important defaults":
  https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults

## 14. Server-sent events — good to know

**What it is**
- One GET the server keeps open and writes events into. The browser's `EventSource` reconnects
  by itself and sends `Last-Event-ID`, the id of the last event it got.
- That id is the same cursor as `?after=`.

**Why it matters here**
- Polling every 2 s is spec #1's deliberate pick and is fine for one owner: up to 2 s late, 30
  requests a minute per open tab.
- Moving later is cheap in design because the cursor (`Message.seq`) already exists.
- The blocker is the server: `deploy/compose.yml` runs gunicorn with 3 workers × 8 threads; each
  open stream would hold a thread. Async streaming needs ASGI (uvicorn or daphne).

**How to apply it**
- If latency starts to matter: an async `StreamingHttpResponse` view under ASGI, each event
  `id: <seq>`, woken by Postgres `LISTEN/NOTIFY` sent from `messages.add` on commit.
- Keep the poll as the fallback.

**One real post**
- Valberg, "Writing a chat application in Django 4.2 using async StreamingHttpResponse,
  Server-Sent Events and PostgreSQL LISTEN/NOTIFY":
  https://valberg.dk/django-sse-postgresql-listen-notify.html
- The spec's reconnect rule: https://html.spec.whatwg.org/multipage/server-sent-events.html

## 15. Streaming agent output to the chat — good to know

**What it is**
- Agent UIs stream run and step events, not only the final text: run started / finished /
  error, tool call start / end, text as it is written.
- AG-UI is one open list of those events.

**Why it matters here**
- `agents/loop.py:_work` posts the producer's words only after its whole turn returns. During a
  long reasoning turn, the owner sees only "The producer is working".
- Spec #1 story 37: "tell me what it is doing as it does it". The scene cards' step notices do
  this for scene steps; the producer's own turns have nothing.

**How to apply it**
- Cheap: let the server status from concept 2 carry the producer's current tool in plain words
  ("Reading your page").
- Full: stream text through SSE (concept 14), which also needs streaming from the gateway.
- What the owner is shown mid-turn is Julian's call.

**One real post**
- AG-UI, "Events": https://docs.ag-ui.com/concepts/events

## 16. Direct-to-storage uploads — good to know

**What it is**
- The browser asks the API for a short-lived, write-only upload URL (Azure: a SAS token) and
  sends the file straight to Blob storage. The app server never carries the bytes.

**Why it matters here**
- Today every photo and video goes browser → Caddy → gunicorn → `file_store`
  (`chat/api.py:_send`). A slow phone upload of a 100 MB video holds a gunicorn thread the whole
  time.
- `adforge/file_store.py` says media will move to object storage by changing `STORAGES`. That is
  the moment to change uploads too.

**How to apply it**
- `POST /api/sessions/<id>/uploads/` returns one SAS URL per file; the browser uploads; the
  message is then sent with the blob keys. The server checks size and type on the blob.

**One real post**
- Microsoft Learn, "Upload Image to Azure Blob Storage with TypeScript" (valet key, SAS):
  https://learn.microsoft.com/en-us/azure/developer/javascript/tutorial/browser-file-upload-azure-storage-blob

## 17. Rows, not a growing JSON array, for the developer record — good to know

**What it is**
- Appending to one JSON column rewrites the whole value every time (Postgres copies a large
  value in full on update) and needs the row locked.
- One row per entry is an INSERT: no lock on the parent, filters in SQL.

**Why it matters here**
- `jobs/notices.py:post_notice` locks the `Job` row and rewrites `job.warnings` for every notice,
  so notices from parallel scene steps queue on one row.
- `jobs/notices.py:asked_since` and `agents/pause.py:_stopped_since` load and scan the whole
  array in Python.

**How to apply it**
- A `Warning` table (job, kind, level, text, at, scene); `chat/api.py:session_warnings` and
  `warnings.py` keep the same JSON.

**One real post**
- pganalyze, "Postgres performance cliffs with large JSONB values and TOAST":
  https://pganalyze.com/blog/5mins-postgres-jsonb-toast

## 18. State colocation and memoized lists — good to know

**What it is**
- Keep state in the smallest component that needs it, so a change re-renders only that part.
- Memoize list rows whose data doesn't change (safe once concept 3 holds: rows are frozen).

**Why it matters here**
- `Chat.tsx:Chat` holds `draft`. Every keystroke re-renders the whole chat, re-runs
  `sceneCards`, `joinStarts`, `pagePanel` and `acceptedPastes` over the full history, and
  re-renders every message and video element.
- Fine at 50 messages; a long session full of videos can lag typing.

**How to apply it**
- Move `draft` into `Composer` (it already owns its photos); `React.memo` a `MessageItem` keyed
  by `seq`; `useMemo` for `sceneCards` in `Chat`.

**One real post**
- Kent C. Dodds, "State Colocation will make your React app faster":
  https://kentcdodds.com/blog/state-colocation-will-make-your-react-app-faster

---

## Also seen here

Concepts already taught with the scene agents (PR #324), as they show up in this area:

- **Stopping conditions:** `jobs/notices.py:asked_since` (`Kind.asks`) decides whether
  `agents/tasks.py:_nudge` nudges, capped at `PRODUCER_MOST_NUDGES` = 2 per owner message.
- **Calibrating an LLM judge:** `agents/card_picks.py:use` — each owner swap on a card, often away
  from the judge's verdict, is a free label not kept as one. Using it is Julian's call (only he
  grades ads).
- **An explicit state machine:** `frontend/src/scenes.ts:sceneCards` builds each card's state
  from five flags (`making`, `failed`, `clipOutOfDate`, `beingRemade`, `busy`) instead of one
  named state.
- **Leases:** `agents/tasks.py:another_session_is_busy` (called by `chat/api.py:_send`) treats
  `producer_seen_at` and step `seen_at` heartbeats as leases (120 s).
- **Atomic counters:** `chat/messages.py:add` takes `MAX(seq) + 1` under the session row lock.
  Correct; a `last_seq = F("last_seq") + 1` counter on `Session` would avoid the MAX scan.
