#!/usr/bin/env bash
# Back up Leadline's database and uploaded documents into /srv/leadline/backups.
#
#   ./backup.sh [label]    cron runs it daily ("daily"); deploys run it first ("pre-deploy")
#
# Keeps 14 days. The copies sit on the same disk as the data: download them now and
# then (scp root@<server>:/srv/leadline/backups/...), or sync them somewhere else.
set -euo pipefail
cd "$(dirname "$0")"

label="${1:-manual}"
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups

docker compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
    > "backups/db-$stamp-$label.dump"
docker run --rm -v leadline_media:/media:ro alpine:3.20 tar -czf - -C /media . \
    > "backups/media-$stamp-$label.tar.gz"

find backups -type f \( -name 'db-*.dump' -o -name 'media-*.tar.gz' \) -mtime +14 -delete
echo "$(date -Is) backed up: db-$stamp-$label.dump, media-$stamp-$label.tar.gz"
