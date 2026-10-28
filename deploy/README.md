# The cloud machine

The whole app runs on one Azure machine with Docker Compose, the same services as on a Mac:
Postgres, Redis, Django (gunicorn), ONE Celery worker, Celery beat, and Caddy in front.

- Caddy is the only thing reachable from outside (ports 80 and 443). It gets the https
  certificate by itself, asks for the password (the lock), passes `/api` to Django, and
  serves `/media` and the built page.
- The API is also open on the machine itself at `http://127.0.0.1:8000`, without the lock,
  for scripts run there.
- Media, the database and Redis live in Docker volumes on the machine's disk.

## Files

| File | What it is |
|---|---|
| `compose.yml` | The services. Run from the repo root: `docker compose -f deploy/compose.yml ...` |
| `backend.Dockerfile` | `backend/Dockerfile` plus gunicorn and `cloud_settings.py` |
| `cloud_settings.py` | The usual settings, with Django's errors printed to the logs |
| `web.Dockerfile`, `Caddyfile` | The built page and Caddy |
| `deploy.sh` | Puts new code on and restarts; refuses while ads are being made |
| `backup.sh` | Nightly database backup, run by cron |
| `cloud-init.yaml` | What a new machine runs on its first start |

## On the machine only, never committed

`deploy/.env`: every key from `.env.example`, plus

```
SITE_ADDRESS=<the machine's name>.<region>.cloudapp.azure.com
DJANGO_SECRET_KEY=<long random>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=<SITE_ADDRESS>,localhost,127.0.0.1,backend
# WORKER_CONCURRENCY=48          how many tasks the one worker runs at once
# WORKER_MAX_MEMORY_KB=1500000   a worker process past this is replaced after its task
```

`deploy/gate.caddy`: the lock. Make the hash with
`docker compose -f deploy/compose.yml exec web caddy hash-password`, then:

```
basic_auth {
	julian <hash>
}
```

An empty file means no password (only if the firewall locks the machine instead).

## Running it

- Start or update: `deploy/deploy.sh [branch-or-commit]`. With no argument it builds what
  is checked out, local changes included.
- Logs: `docker compose -f deploy/compose.yml logs -f worker` (or `backend`, `web`, ...).
- Never start a second worker, and never restart the worker while ads are being made: a
  worker that starts marks every running step dead (`agents/tasks.py`,
  `carry_on_after_the_worker_starts`). `deploy.sh` checks for work first; `FORCE=1` skips the
  check. A machine reboot restarts the worker too.
- Restore a backup: into an empty database,
  `gunzip -c /srv/adforge-backups/<file> | docker compose -f deploy/compose.yml exec -T db psql -U adforge adforge`.
