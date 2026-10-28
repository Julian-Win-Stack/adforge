#!/usr/bin/env bash
# Saves the database to /srv/adforge-backups, keeping the last 14. Run every night by cron
# (deploy/README.md). Restore: gunzip -c <file> | docker compose -f deploy/compose.yml exec -T
# db psql -U adforge adforge, into an empty database.
set -euo pipefail
cd "$(dirname "$0")/.."
backups=/srv/adforge-backups
mkdir -p "$backups"
docker compose -f deploy/compose.yml exec -T db pg_dump -U adforge adforge \
  | gzip > "$backups/adforge-$(date -u +%Y%m%d-%H%M).sql.gz"
ls -1t "$backups"/adforge-*.sql.gz | tail -n +15 | xargs -r rm --
