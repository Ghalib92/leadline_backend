#!/usr/bin/env bash
# The server half of scripts/deploy.sh, run from the new release:
#   bash /srv/leadline/releases/<commit>/deploy/install.sh /srv/leadline /srv/leadline/releases/<commit>
# A file, not a script piped through ssh: `docker compose exec` reads stdin, and would
# swallow the rest of a piped script (the first deploy lost its last steps that way).
set -euo pipefail
root="$1"
release="$2"
cd "$root"

if [ ! -f .env ]; then
    echo "Missing $root/.env: fill one in from deploy/env.production.example." >&2
    exit 1
fi

ln -sfn "$release" backend
cp backend/deploy/docker-compose.yml docker-compose.yml
install -m 755 backend/deploy/backup.sh backup.sh

if [ -n "$(docker compose ps --status running -q postgres 2>/dev/null)" ]; then
    echo "==> Backing up before migrating"
    ./backup.sh pre-deploy
fi

echo "==> Building and starting (waits for the health checks)"
docker compose build api
docker compose up -d --wait --wait-timeout 300

echo "==> Caddy site"
site=/srv/edge/sites/leadline.caddy
if ! cmp -s backend/deploy/leadline.caddy "$site"; then
    edge="docker compose -f /srv/edge/docker-compose.yml"
    [ -f "$site" ] && cp "$site" "$site.previous"
    cp backend/deploy/leadline.caddy "$site"
    if ! $edge exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
        echo "The Caddy site is invalid; putting the previous one back." >&2
        if [ -f "$site.previous" ]; then mv "$site.previous" "$site"; else mv "$site" "$site.invalid"; fi
        exit 1
    fi
    $edge exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
fi

echo "30 2 * * * root $root/backup.sh daily >> $root/backups/backup.log 2>&1" \
    > /etc/cron.d/leadline-backup

docker compose ps
