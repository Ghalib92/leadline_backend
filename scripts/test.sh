#!/usr/bin/env bash
set -euo pipefail

ruff check .
pytest
python manage.py check
python manage.py makemigrations --check --dry-run