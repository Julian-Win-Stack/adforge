#!/usr/bin/env bash
# Puts new code on the cloud machine and starts it: deploy/deploy.sh [branch-or-commit]
# With no argument it builds what is checked out now, local changes included (a test's
# local-only patch, say). Run from anywhere on the machine.
#
# It refuses while ads are being made: restarting the worker marks every running step dead
# (agents/tasks.py, carry_on_after_the_worker_starts). FORCE=1 restarts anyway.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f deploy/compose.yml)

busy() {
  # Producers and steps seen in the last five minutes. A dead one stays marked running
  # forever, so only recent heartbeats count.
  "${compose[@]}" exec -T backend python manage.py shell -c "
from datetime import timedelta
from django.utils import timezone
from chat.models import Session
from jobs.models import SceneStep
alive = timezone.now() - timedelta(minutes=5)
producers = Session.objects.filter(producer_running=True, producer_seen_at__gt=alive).count()
steps = SceneStep.objects.filter(status=SceneStep.Status.RUNNING, seen_at__gt=alive).count()
print(producers + steps, f'{producers} producers and {steps} steps are working')
"
}

if "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx backend; then
  read -r count said < <(busy)
  echo "$said"
  if [[ "$count" != 0 && "${FORCE:-0}" != 1 ]]; then
    echo "Not restarting while ads are being made. Wait for them, or FORCE=1 to kill them." >&2
    exit 1
  fi
fi

if [[ $# -gt 0 ]]; then
  git fetch origin "$1"
  git checkout --detach FETCH_HEAD
fi

"${compose[@]}" up -d --build --remove-orphans
"${compose[@]}" ps
echo "Running $(git rev-parse --short HEAD) $(git status --short | grep -q . && echo '(with local changes)')"
