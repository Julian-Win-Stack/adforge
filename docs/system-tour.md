# AdForge: a tour of what's built, and what to read

> **Out of date as a design document.** This describes the fixed-pipeline system as built
> up to #5, and is still accurate about the code on `main` today. The design has since changed:
> spec #1 now describes a chat agent that chooses its own order of work. See `CONTEXT.md`,
> `docs/adr/0001-agent-loop-with-safety-in-the-tools.md` and tickets #14 and #15.

Written for someone new to this codebase. Read this top to bottom once, then use Part 3 as
your reading list.

---

## Part 1 — What the product is

**The promise:** a shop owner pastes a link to a product page, optionally says "make it 15
seconds", and gets back a finished short vertical video ad — a person talking to camera
about the product, with music and captions.

**Why it exists:** it's a rebuild of Creatify's ad agent. The developer ran Creatify 5 times
and wrote down everything that went wrong (wrong prices, ads 2× too long, dead air between
scenes, work lost on crashes). The whole design is a reaction to that list. Spec is GitHub
issue **#1**; the problem list is `docs/creatify-problems.md`.

**The core idea that shapes every file:** the system is an *agent*, not a fixed script. A
model called "the producer" decides how many scenes there are, what each says, and *when to
stop and ask the user a question instead of guessing*. Two rules run through everything:

1. **Never invent a fact.** Every claim in the ad must be written on the product page. If
   it isn't, the line gets rewritten or the user gets asked.
2. **Never spend money on something you'll throw away.** All the text-level checking happens
   *before* any video is rendered.

---

## Part 2 — The architecture

### 2.1 The boxes

```
Browser (React + TypeScript, Vite)
   │  polls every 2 s over HTTP
   ▼
Django + Django REST Framework  ──writes──▶  Postgres  (the single source of truth)
   │                                              ▲
   │ queues a task                                │ same DB
   ▼                                              │
Redis ──▶ Celery worker ────────────────────────┘
                │
                ├──▶ jobs/page.py   → fetches the shop's product page over HTTP
                └──▶ gateway/       → the ONE door to every AI provider
                                        ├─ OpenAI  (text + the portrait image)
                                        └─ Inworld (voice design + speech)
                        │
                        └──▶ adforge/file_store.py → the ONE door to files (local disk now)
```

Everything runs with `docker compose up` (see `docker-compose.yml`): Postgres, Redis, the
Django server, a Celery worker, and the Vite dev server. The browser calls `/api/...` on its
own address and Vite proxies it to Django — that's why there's no CORS config anywhere.

### 2.2 The two "one door" rules (the most important design decisions)

A junior-friendly way to think about this: when you need to do something risky or
expensive, you build **one** function that everybody has to go through, so you can put all
your safety checks in that one place.

| The door | File | Everything behind it |
|---|---|---|
| **The model gateway** | `backend/gateway/gateway.py` | Every single AI call |
| **The file store** | `backend/adforge/file_store.py` | Every single file read/write |

The gateway is the heart of the backend. On *every* call it:

1. **Validates the handoff** with Pydantic *before* spending money. A "handoff" is the data
   one part of the system hands to a model. Creatify's bug #8 was a scene length arriving as
   a decimal instead of a whole number — here that raises an error before the request goes out.
2. **Retries** when the provider is down (`adforge/retry.py`, 3 attempts: now, +2 s, +8 s).
3. **Records every attempt** as a `ModelCall` row: what model, what it cost in dollars, how
   many milliseconds, whether it worked, and the model's one-sentence reason. This table is
   the *only* source of cost and time numbers for the project writeup.

The gateway is also enforced by a lint rule — `pyproject.toml` bans importing `openai`
anywhere except `gateway/openai_adapter.py`. You physically cannot bypass it by accident.

### 2.3 The data model (`backend/jobs/models.py`)

Six tables. Learn these names; they're the project's vocabulary.

| Model | What it is |
|---|---|
| **Job** | One ad being made. Holds the link, target length, current `status`, the page text, and the producer's choices (product colour, how the person looks and sounds). |
| **ActivityEntry** | One line in the live activity feed: a message plus *why*. Numbered 1, 2, 3… per job. |
| **ProductPhoto** | A photo pulled off the page (or uploaded by the user), plus whether it shows the product in the chosen colour. |
| **Scene** | One shot: a number and the line the person says. Carries `fact_checked` and a history of `fact_problems`. |
| **Question** | Something the job asked the user, and their answer. **A job can have at most one unanswered question** — enforced by a database constraint, not just by code. |
| **ProducedItem** | Something a model made (portrait, voice). Versioned — a remake adds v2, it never overwrites v1. |
| **ModelCall** | (in `gateway/models.py`) One attempt at one AI call, with cost and timing. |

### 2.4 The state machine

`Job.status` is how the backend and frontend agree on what's happening. This is the spine of
the whole system:

```
queued
  └─▶ reading_page ──▶ page_read ──▶ planning ──▶ planned
                                                    │
                                          making_person
                                                    │
                                          checking_plan ──▶ ready_to_render   ← the finish line TODAY
```

Plus four off-ramps:

- `needs_working_link` — couldn't read a single product's page; asking for a better link
- `needs_product_photos` — no usable photo; asking the user to upload one
- `needs_answer` — the producer or a check has a question
- `failed` — something went wrong for good

**How the crash-safety works:** each Celery task starts by looking at `job.status`. If the
job is already past this step, it skips and moves on; if it's not yet at this step, it does
nothing. That's why `_plan_ad()` opens with `if job.status == PLANNED: return True`. Re-running
a task is always safe. Read `_plan_ad` and `_make_person` in `tasks.py` with this in mind —
those opening `if` statements are the whole trick.

### 2.5 The pipeline as it runs today

| Step | Task | What happens |
|---|---|---|
| 1 | `read_page` | Fetch the page over plain HTTP. Store three things: the raw HTML as a file (a receipt of what the page said that day), the **page text** (visible words + the JSON-LD product data shops publish for Google — that's often where the price lives), and the product photos. Then a cheap model (`gpt-5-mini`) judges: is this one product's readable page? |
| 2 | `plan_ad` | The producer (`gpt-5.6-sol`) gets the page text, the target length, and the photos (shrunk to 512×512 to keep the bill down). It returns either a **plan** — the scenes and their lines, the product's colour, which photos show that colour, and a description of the presenter's looks and voice — or a **question** for the user. |
| 3 | `make_person` | Draw a portrait from the looks description (OpenAI image model). Design a voice from the voice description (Inworld), then **have it read the whole script and measure how many words per second it actually speaks.** |
| 4 | `check_plan` | Two checks, in a loop, before anything is rendered. |

**Why step 3 measures the voice:** Creatify assumed 3 words/second; real speech was 1.6–2.5.
So all their length maths was wrong. Here there is no hard-coded speed anywhere — the system
listens to the actual voice and times it.

### 2.6 The planning checks (`_check_plan` in tasks.py)

This is the cleverest part of the code. It's a `while` loop over `job.status == CHECKING_PLAN`:

**Fact check** — every line goes to a model with the page text. Each line comes back `ok` or
`wrong` with a reason and a quote of what the page actually says. A wrong line is sent back
to the producer to rewrite, then re-checked. After **2 failed rewrites**, the user is asked:
"keep this line, or write your own?" If the *page itself* is contradictory (two prices for
the same thing), the model says `unclear` and the user is asked immediately.

A line that *names the product's colour* also counts as wrong. The ad **shows** the colour
through the photos and never says it — so the words can never disagree with the picture.

**Length fit** — only if the user set a target. Words ÷ measured speed = seconds. It fits if
it's no more than 1 second over. If it doesn't fit, the user picks: "shorten it" (the producer
rewrites, up to 2 tries) or "keep it longer".

Notice how the loop resumes: when the user answers, the API sets the status back to
`checking_plan` and re-queues `check_plan`. Lines already marked `fact_checked` stay checked,
so nothing is paid for twice. That's the same idempotency trick as in 2.4.

### 2.7 How the browser stays in sync

Polling, not WebSockets — chosen because runs are long and a held-open connection drops on
public WiFi.

`GET /api/jobs/<id>/?after=7` returns the job plus **only activity entries numbered above 7**.
The browser remembers the last number it saw. Nothing is missed, nothing shown twice.

The subtle bit is in `jobs/activity.py`: it locks the job row while assigning the next number
and writes the status change in the same transaction. Without the lock, entry #12 could become
visible before #11, the browser would move its marker past 11, and #11 would be lost forever.

### 2.8 Testing philosophy

**Tests use only the HTTP API.** They POST a job, poll it, answer its questions — exactly
like the browser does. They never call internal functions. That means you can rearrange the
insides freely and the tests still pass.

Everything outside the system is swapped for something real-but-local:

- AI calls → `gateway/fake.py`, a scripted stand-in. You tell it "the first `plan_ad` call
  returns this, the second raises this error."
- The shop → `pytest-httpserver`, a real HTTP server on localhost serving a fake mug page.
- Files → a temp folder, through the file store.
- DNS → a fake resolver, so the private-address safety check can be tested.

76 backend tests, 9 frontend tests. CI runs ruff, mypy in strict mode, and pytest.

### 2.9 What is NOT built yet

Everything from here on is spec'd in #1 and ticketed, but the code does not exist:

- **Rendering** — starting pictures, clips (HeyGen), transcripts (ElevenLabs), music (fal) — #6, #7
- **Assembly with ffmpeg**, captions, on-screen text — #7
- **Cancel, retry a failed scene, concurrency locking** — #8
- **Editing by commenting at a moment in the video** — #9
- **Quality checks** (the on/off switch, brand check, scene comparison) — deliberately deferred
  until after the first full run shows what actually goes wrong

`Scene.Status` has exactly one value, `planned`, because nothing downstream exists yet. That's
a deliberate placeholder, not an oversight.

**State of the tickets:** #2, #3, #4 closed. **#5 (person + planning checks) is written and
committed but still open** — the last four commits are its implementation. #6–#9 not started.

---

## Part 3 — What to read, in order

Rough budget: ~2,600 lines of production code. Tiers 1–2 are about 1,100 lines and give you
80% of the understanding.

### Tier 1 — Read these four, carefully, in this order (~700 lines)

You cannot understand this codebase without these.

| # | File | Lines | Why |
|---|---|---|---|
| 1 | `backend/jobs/models.py` | 243 | The vocabulary. Every other file manipulates these six tables. Read the `help_text` strings — they're written as documentation. |
| 2 | `backend/jobs/tasks.py` | 698 | **The main file.** The whole pipeline lives here. Read `_read_page` → `_plan_ad` → `_make_person` → `_check_plan` in that order and you've read the product. |
| 3 | `backend/gateway/gateway.py` | 333 | The one door for AI. Focus on `call_model()` and `_recorded()`. |
| 4 | `backend/jobs/api.py` | 270 | The HTTP surface: 3 endpoints. Focus on `answer_question` and the `_TAKE_ANSWER` dict at the bottom — it maps each kind of question to how its answer restarts the pipeline. |

**While reading `tasks.py`, keep asking:** *"what happens if the worker dies right here and
the task runs again?"* Every answer is in the comments.

### Tier 2 — Read these five next (~400 lines)

These fill in the parts Tier 1 delegates to.

| # | File | Lines | Why |
|---|---|---|---|
| 5 | `backend/jobs/planning.py` | 112 | The producer's prompt and the exact Pydantic shape its answer must fit. This is where "never invent a fact" is actually enforced. |
| 6 | `backend/jobs/checks.py` | 172 | Same, for the fact check, rewrite and shorten prompts, plus the length maths. |
| 7 | `backend/gateway/types.py` | 120 | The contracts: `Handoff`, `Judgement`, and the three `Protocol` classes that define what a provider must do. Short and high-value. |
| 8 | `backend/jobs/activity.py` | 21 | Tiny. Read the docstring — it explains a real race condition clearly. |
| 9 | `frontend/src/api.ts` | 150 | The frontend's mirror of the backend's shapes. Reading it confirms you understood the API. |

### Tier 3 — Skim, don't study (~600 lines)

Read the module docstring and the function names. Dive in only when you need that area.

| File | Skim for |
|---|---|
| `backend/jobs/page.py` (203) | HTML → text + photo URLs. The interesting bit is `_check_where_it_points()`, which blocks links to localhost/private IPs (an SSRF guard). The BeautifulSoup and JSON-LD parsing is ordinary plumbing. |
| `backend/gateway/openai_adapter.py` (102) | How a handoff becomes an OpenAI request. Note it reads the token usage *before* parsing the answer, so a bad reply is still billed correctly. |
| `backend/gateway/inworld_adapter.py` (73) | Voice design needs two calls: design, then publish. |
| `backend/gateway/catalog.py` (47) | Which model does which job, and prices. Read it once — it's a lookup table. |
| `backend/gateway/fake.py` (76) | Read this before reading any test; the tests won't make sense otherwise. |
| `frontend/src/JobView.tsx` (154) | The polling loop in the `useEffect`. |
| `frontend/src/QuestionForm.tsx` (106) | One form that shapes itself to six kinds of question. |
| `backend/adforge/settings.py`, `retry.py`, `file_store.py` | All short, all worth the 2 minutes. |

### Tier 4 — Read only when you need them

- **`backend/tests/conftest.py` (266)** — read this the moment you write or debug a test. The
  mug fixtures (`PRODUCT_PAGE`, `PLAN`, `FACTS_OK`) appear in every test file.
- **`backend/tests/test_checks.py` (697)** and **`test_jobs_api.py` (785)** — don't read
  front-to-back. Use them as a searchable index: the test names are full English sentences
  describing exactly one rule each. When you want to know "what happens if X", grep for X here.
- **`backend/jobs/admin.py`** — configuration, no logic.
- **`frontend/src/App.test.tsx` (450)** — same as above, grep don't read.

### Skip entirely

- **`frontend/node_modules/`**, `backend/.venv/`, `.ruff_cache/`, `.mypy_cache/` — dependencies.
- **`backend/*/migrations/*.py`** — auto-generated schema history. The current shape is in
  `models.py`; reading 10 migration files just tells you the order things were added.
  (Exception: `jobs/migrations/0005` and `0010` contain hand-written data migrations, worth a
  look only if you're doing a migration yourself.)
- **`.build-check/` and `.local/build-checks/`** — 100 KB+ of past code-review reports. Useful
  history if you're auditing #4, useless for learning the system.
- **`uv.lock`, `package-lock.json`, `tsconfig.json`, `eslint.config.js`, `.prettierrc.json`,
  `Dockerfile`** — tooling.
- **`frontend/src/main.tsx`, `App.tsx`, `StartForm.tsx`** — trivial. 90 lines total, no logic
  worth studying.

### Worth reading outside the code

Read these *before* Tier 1 if you have 20 minutes — they explain *why* almost every decision
was made:

1. **GitHub issue #1** (`gh issue view 1`) — the full spec. Long, but the "Order of work for a
   job" and "Planning checks" sections are the direct source of `tasks.py`.
2. **`docs/creatify-problems.md`** — the 11 problems this project exists to solve, each with
   its status. The single best "why is it like this?" document.
3. **`docs/video-model-tests.md`** — real cost and latency numbers from testing 5 video models.
4. **`docs/real-api-replies.md`** — what Inworld and OpenAI actually send back. Read it if
   you touch either adapter.

### A suggested first day

1. `docs/creatify-problems.md` (15 min) — understand the *why*.
2. `models.py` (20 min) — learn the nouns.
3. `tasks.py` (60 min) — follow one job end to end.
4. `gateway.py` + `types.py` (30 min) — understand the one door.
5. `api.py` (20 min) — see how the user gets in and out.
6. Then: run `docker compose up`, start a job against a real product URL, and watch the
   activity view. Cross-reference each line you see with the `record(...)` call that wrote it.

---

## Glossary

Terms the code uses consistently. Use the same words in issues and commits.

- **the producer** — the model that plans the ad and rewrites lines (never called "the LLM")
- **handoff** — the validated data one part hands a model
- **judgement** — a model's decision plus one sentence of reasoning, written for the shop owner
- **the page text** — visible words + the page's declared product data; never the raw HTML
- **planning checks** — fact check + length fit; run before anything is rendered
- **quality checks** — checks on rendered media; not built yet, and switchable off as a group
- **produced item** — something a model made, stored as a version
