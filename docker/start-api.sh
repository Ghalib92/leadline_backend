#!/usr/bin/env bash
# The API container: apply migrations, then serve. The worker waits for this one to be
# healthy, so it never runs against an unmigrated database.
set -euo pipefail

python manage.py migrate --no-input

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --access-logfile -
