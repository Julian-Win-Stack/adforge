AdForge: a chat agent that turns a product page into a short video ad. Django + Celery backend (`backend/`), React + Vite frontend (`frontend/`). How it works: `README.md`; domain words: `CONTEXT.md`; decisions: `docs/adr/`.

## Working with Julian (the owner)

- **Advice first.** When he states an opinion or plan, say plainly whether he's right and why, with evidence, then wait for his go.
- **Design is his call.** What the app or a model checks, decides or tells the shop owner (rules, prompts, thresholds, flows) needs his yes on a plain-words proposal before any build or run. Give one proposal with a marked pick. Prompt wording, code structure, names and tests are yours.
- **Read the code before describing app behaviour**, and cite `file:function`. Issues and docs describe intent, not behaviour.
- **Held for him:** spending money on paid APIs (state the cost and any cheaper way first), deletes, messages outside the repo, and judging videos or pictures. Only he grades ads.
- **Proven B-roll rules** change only with his explicit OK: `docs/agents/proven-broll-rules.md`.
- **Replies:** short and plain, problems named in words, one question at a time. Apologize first when wrong.
- Pushing, opening PRs and merging don't need his yes.

## Coding standards

- Prompt rules must be general, never hardcoded to a tested product: `CODING_STANDARDS.md`. Find the root cause first.
- **No silent failures.** Every fallback or failure posts a notice of a `Kind` (`backend/jobs/notices.py`). Owner kinds reach the chat; developer kinds go to `job.warnings` and Sentry.
- One kebab-case branch per piece of work, cut from latest `main` (or the unmerged branch it depends on); finished work gets a PR into `main`. Commit messages matter.
- Known limitations (don't build without his go): `docs/limitations.md`.

## Testing

- Commands are in `.github/workflows/ci.yml` (backend: `uv run ruff format --check . && uv run ruff check . && uv run mypy . && uv run pytest -q -n auto`; frontend: `npm run format:check && npm run lint && npm test && npm run build`). Backend needs Python 3.14, Postgres 16, ffmpeg.
- Lead with integration tests that run a main flow through the chat, one per main case, outside services faked. Unit tests only for pure logic.
- ffmpeg runs are replayed (`backend/tests/ffmpeg_replay.py`); mark a test `real_ffmpeg` only when it must render.
- Two pytest runs at once need different `DATABASE_URL` database names.
- Tests read developer notices with `recorded()` in `backend/tests/conftest.py`.
- A real test run starts from a first chat message (just the product link) with `QUALITY_CHECKS` on, and fakes only the paid step not under test, never in pushed code.

## Running and deploying

- Local: `docker compose up` (Postgres, Redis, Django, one Celery worker, beat, Vite). Env vars: `.env.example`, plus `GEMINI_API_KEY` (videos are watched on Gemini). Never commit `.env` or any key.
- Azure (production and paid test runs): setup in `deploy/README.md`; getting on the VM, deploying and running ads there in `docs/azure.md`.
- **Never start a second Celery worker or restart the worker while ads are being made**: a starting worker marks every running step dead (`carry_on_after_the_worker_starts` in `backend/agents/tasks.py`). `deploy.sh` checks first.
- Paid services: OpenAI (pictures), HeyGen (talking clips), Creatify/Boreal-H3 (B-roll), Gemini, Firecrawl, Inworld, ElevenLabs, fal. Text and vision models run on Azure OpenAI when its keys are set.

## Gotchas

- Two branches can each add the same migration number; add a merge migration (`manage.py makemigrations --merge`).
- Sentry's DSN lives only in `.env` (`SENTRY_DSN`), never in the repo.

## Agent skills

- Issues and specs: GitHub Issues on `Julian-Win-Stack/adforge` via `gh`. See `docs/agents/issue-tracker.md`.
- Triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.
- Domain docs: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
