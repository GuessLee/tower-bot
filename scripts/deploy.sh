#!/usr/bin/env bash
# Run on Tower from the checkout at /mnt/user/appdata/tower-bot/src.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker build -t tower-bot:local .
# Compose Manager on Tower expects its compose file under
# /boot/config/plugins/compose.manager/projects/<name>/ to show up in its UI;
# this invokes the checkout's own deploy/docker-compose.yml directly instead.
# Task 17 decides how (or whether) Compose Manager picks this project up.
docker compose -f deploy/docker-compose.yml up -d --force-recreate
PROM=/mnt/user/appdata/monitoring/textfile/tower_bot.prom
before=$(stat -c %Y "$PROM" 2>/dev/null || echo 0)
for _ in $(seq 1 18); do
  sleep 10
  now=$(stat -c %Y "$PROM" 2>/dev/null || echo 0)
  if [ "$now" -gt "$before" ] && grep -q '^tower_bot_up 1' "$PROM"; then
    echo "tower-bot healthy"; exit 0
  fi
done
echo "tower-bot did not report healthy within 3 min" >&2
docker logs --tail 50 tower-bot >&2
exit 1
