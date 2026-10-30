# Azure: the cloud machine

How the app runs and is set up: `deploy/README.md`. This page is how to use it.

## What is there

- Resource group `adforge`, VM `adforge-vm` (always on). Code at `/srv/adforge`, secrets in `/srv/adforge/deploy/.env` (never committed).
- The app: `https://<SITE_ADDRESS>` (in `deploy/.env`), behind a password. Ask Julian for the link and password.
- On the VM itself the API is open without the password at `http://127.0.0.1:8000`.

## Getting on the VM

There is no SSH. Two ways in:

- A Claude Code / Codex session running on the VM (Julian starts it).
- `az vm run-command invoke -g adforge -n adforge-vm --command-id RunShellScript --scripts "<cmd>"`, signed in as the `adforge-deploy` service principal (Julian adds the certificate).

## Deploying

```
cd /srv/adforge && deploy/deploy.sh <branch-or-commit>
```

- It refuses while ads are being made. Check nothing is running before `FORCE=1`: a restart kills running steps.
- Known bug: the busy check reads the first line of the Django shell's output, which is now "N objects imported automatically", so it can refuse with a wrong count. Check by hand, then `FORCE=1`.
- With no argument it builds what is checked out, local changes included (how a local-only test patch goes on).
- A deploy that changes keys in `deploy/.env` only takes effect after `deploy.sh` restarts the containers.

## Running ads on Azure

Use the scripts in `scripts/ad-runs/` (README there). They drive the API on the VM, so the ads show in the Azure app:

| Call | What it does |
|---|---|
| `POST /api/sessions/` | New ad session |
| `POST /api/sessions/<id>/messages/` `{"text": "<product link>"}` | Start it, or answer the producer |
| `GET /api/sessions/<id>/` | State, chat and scenes |
| `POST /api/sessions/<id>/pause/` | Pause an ad (stops new spending) |
| `GET /api/sessions/<id>/warnings/` | Every warning recorded, owner-facing or not |

- Keep `QUALITY_CHECKS` on. Watch RAM; one worker runs up to `WORKER_CONCURRENCY` (48) tasks.
- Logs: `docker compose -f deploy/compose.yml logs -f worker`.

## Running tests on Azure

The test suite runs in GitHub Actions (`.github/workflows/ci.yml`) on every PR, and locally. The production containers don't hold the test dependencies or a test database, so don't run `pytest` on the VM.
