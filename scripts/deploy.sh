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

ssh "$host" bash "$release/deploy/install.sh" "$root" "$release" </dev/null

echo "==> Deployed $commit"
