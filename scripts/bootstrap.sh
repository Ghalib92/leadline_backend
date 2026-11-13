#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements/local.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update database credentials before continuing."
fi

echo "Bootstrap complete."