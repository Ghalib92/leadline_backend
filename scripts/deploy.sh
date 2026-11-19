#!/usr/bin/env bash
# Deploy the committed backend (HEAD) to the server. See deploy/README.md.
#
#   DEPLOY_HOST=root@<server-ip> scripts/deploy.sh
#
# Copies the code to /srv/leadline/releases/<commit> and points /srv/leadline/backend
# at it, backs up the database, builds the image, starts everything and waits for the
# health checks, then installs the Caddy site for the API. Running it again with the
# same commit is safe.
set -euo pipefail
cd "$(dirname "$0")/.."

host="${DEPLOY_HOST:?Set DEPLOY_HOST, e.g. DEPLOY_HOST=root@203.0.113.10}"
root=/srv/leadline
commit="$(git rev-parse --short HEAD)"
release="$root/releases/$commit"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Note: uncommitted changes are not deployed; deploying $commit." >&2
fi

echo "==> Copying $commit to $host:$release"
ssh "$host" "mkdir -p '$release' '$root/backups'"
git archive --format=tar HEAD | ssh "$host" "tar -x -C '$release'"

ssh "$host" bash -s -- "$root" "$release" <<'REMOTE'
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
REMOTE

echo "==> Deployed $commit"
