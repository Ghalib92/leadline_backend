PYTHON ?= python
PIP ?= pip

.PHONY: install install-prod migrate migrations superuser seed run shell test coverage lint format check temporal-dev temporal-worker dev precommit

install:
	$(PIP) install -r requirements/local.txt

install-prod:
	$(PIP) install -r requirements/production.txt

migrate:
	$(PYTHON) manage.py migrate

migrations:
	$(PYTHON) manage.py makemigrations

superuser:
	$(PYTHON) manage.py createsuperuser

seed:
	$(PYTHON) manage.py seed

run:
	$(PYTHON) manage.py runserver

shell:
	$(PYTHON) manage.py shell

test:
	pytest

coverage:
	pytest --cov=apps --cov-report=term-missing --cov-report=html

lint:
	ruff check .

format:
	ruff format .
	ruff check . --fix

check:
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run

temporal-dev:
	$(PYTHON) manage.py temporal_dev_server

temporal-worker:
	$(PYTHON) manage.py temporal_worker

# All local processes in one terminal (Procfile.dev). Postgres must be running.
dev:
	PYTHONUNBUFFERED=1 $(PYTHON) -m honcho -f Procfile.dev start

precommit:
	pre-commit run --all-files