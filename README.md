# Leadline backend

Leadline helps organizations find and win opportunities: grants, tenders, RFPs,
fellowships, accelerators, partnerships and events. An organization describes
itself once; AI agents then search the web for matching opportunities, a person
approves the good ones, and the AI drafts the proposal, finds the funder's
contacts and drafts the outreach emails. Nothing is sent to a funder without a
person's approval.

This repository is the Django REST API and the AI agents (LangGraph). The web app
is [`leadline-ui`](../leadline-ui) (React Router); it talks to this API through its
own `/api/*` proxy.

**Plan and progress:** [`my-folder/project.md`](my-folder/project.md).

- [How Leadline works](#how-leadline-works)
- [Set up locally](#set-up-locally)
- [Run it](#run-it)
- [Fake or real AI](#fake-or-real-ai)
- [How the code is organized](#how-the-code-is-organized)
- [What happens when a user clicks “Search for opportunities”](#what-happens-when-a-user-clicks-search-for-opportunities)
- [API map](#api-map)
- [Background jobs](#background-jobs)
- [Configuration](#configuration)
- [Tests, linting and conventions](#tests-linting-and-conventions)
- [Adding a feature](#adding-a-feature)
- [Troubleshooting](#troubleshooting)

---

## How Leadline works

```
 1. Organization setup          profile, preferences, links, documents, team
          │
 2. Company knowledge           documents are read and embedded (pgvector);
          │                     the AI writes a "dossier": what it knows about the org
          ▼
 3. Discovery run               agents plan searches, search the web and grant/
          │                     procurement sources, read pages, score the fit
          ▼
 4. Review                      a person approves or rejects (with a reason)
          │  ┌──────────────────────────────────────────────────────────┐
          │  │ rejections teach the agents (decision memory, preference │
          │  │ suggestions)                                             │
          ▼  └──────────────────────────────────────────────────────────┘
 5. Proposal Writer             approval starts a full proposal draft, with
          │                     evidence from the org's documents
          ▼
 6. Leads and outreach          the lead scout finds funder contacts, the email
                                agent drafts emails, an admin approves each send;
                                deadlines go to a calendar with email reminders
```

| Step | App | Main models |
|---|---|---|
| 1 Setup | `apps/organizations` | `Organization`, `OpportunityPreferences`, `OrganizationDocument`, `OrganizationMembership`, `OrganizationInvitation` |
| 2 Knowledge | `apps/knowledge` | `DocumentChunk` (pgvector), `CompanyDossier` |
| 3 Discovery | `apps/discovery` + `apps/ai/graphs/discovery` | `SearchAgent`, `DiscoveryRun`, `RunEvent`, `RunCandidate` |
| 4 Review | `apps/opportunities`, `apps/learning` | `Opportunity`, `OpportunityReview`, `PreferenceSuggestion` |
| 5 Proposals | `apps/proposals` + `apps/ai/graphs/proposal` | `Proposal`, `ProposalVersion`, `ProposalEvent` |
| 6 Outreach | `apps/outreach` + `apps/ai/graphs/lead_scout.py` | `Contact`, `ContactSearch`, `OutreachEmail`, `ReminderPreference` |

Roles: **admins** manage the organization, send emails and manage every agent;
**members** read everything, create agents, review opportunities, work on
proposals and draft emails.

---

## Set up locally

### 1. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 | the app |
| PostgreSQL | 15+ with **pgvector** | data and document embeddings |
| API keys | optional | `GROQ_API_KEY` (models), `TAVILY_API_KEY` (web search), `HUNTER_API_KEY` (contacts). Without them, use the fake runners (below). |

Install pgvector once (Ubuntu: `sudo apt install postgresql-16-pgvector`, macOS:
`brew install pgvector`).

### 2. Code and settings

```bash
git clone <repo> leadline_backend && cd leadline_backend
git checkout dev                       # work branches start from dev
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt  # or: make install
cp .env.example .env
```

In `.env`, set at least `DJANGO_SECRET_KEY` and the `DB_*` values. Generate a
secret with:

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

Already have a `.env`? Keep it and copy in any setting from `.env.example` that it
is missing.

### 3. Database

Open `sudo -u postgres psql` and run:

```sql
CREATE ROLE leadline WITH LOGIN PASSWORD 'replace-with-your-local-password';
CREATE DATABASE leadline OWNER leadline;
ALTER ROLE leadline CREATEDB;            -- local only: pytest creates a test database
\c leadline
CREATE EXTENSION IF NOT EXISTS vector;  -- the app user is not a superuser
\c template1
CREATE EXTENSION IF NOT EXISTS vector;  -- so test databases get it too
```

Then:

```bash
python manage.py migrate
make check                 # system checks + fails if a migration is missing
python manage.py seed      # demo accounts and a complete demo organization
```

`seed` prints the demo logins:

| Who | Email | Password | Role |
|---|---|---|---|
| Amina Otieno | `amina.otieno@example.org` | `LeadlineDemo2026!` | admin |
| Brian Kamau | `brian.kamau@example.org` | `LeadlineDemo2026!` | member |

The demo organization, **Tujenge Data Initiative**, has a full profile, two agents,
six past runs, twelve opportunities (some reviewed), a proposal, contacts and
outreach emails. Its funders are fictional and their links go to `example.org`.
`python manage.py seed --reset` recreates it. To create your own admin for Django
admin: `python manage.py createsuperuser`.

---

## Run it

One command, in one terminal (activate the venv first; Postgres must be running):

```bash
make dev    # runserver + Temporal server + Temporal worker (Procfile.dev, via honcho)
```

| Process | What it is |
|---|---|
| `web` | `python manage.py runserver`: the API on http://localhost:8000/api/docs/ |
| `temporal` | a local [Temporal](https://temporal.io) server; its UI is on http://localhost:8233 (the SDK downloads it once) |
| `worker` | `python manage.py temporal_worker`: runs every background job and creates the Schedules |

Ctrl+C stops all three. You can still start one alone (`make run`, `make temporal-dev`,
`make temporal-worker`).

**The worker is required.** Without it, uploads stay "Queued", the AI summary stays
"Updating", searches stay "Starting", and proposals, contact searches and email
drafts never start. The Temporal UI shows every job as a workflow (a search is
`discovery-run-<run id>`), with its steps, retries and errors.

Then run the frontend (`leadline-ui`):

```bash
cd ../leadline-ui
pnpm install
API_URL=http://127.0.0.1:8000/api/v1 pnpm dev   # http://localhost:5173
```

Sign in with a demo account above. Emails (verification codes, invitations,
outreach) print in the `runserver` / worker terminal with the default console
email backend, so nothing reaches a real inbox locally.

---

## Fake or real AI

Two settings decide whether the AI is real. **Restart `runserver` and the worker
after changing `.env`.**

| Setting | `fake` (default) | `graph` (real) |
|---|---|---|
| `DISCOVERY_RUNNER` | acts out a search in ~15 s and adds `[Sample]` opportunities; no keys | the real agents: Groq models + Tavily web search + Grants.gov / World Bank; 15–90 s per run |
| `PROPOSAL_RUNNER` | writes `[Sample]` sections instantly | real drafts from the org's documents; ~2 min |

The AI summary (dossier), the lead scout and the email agent always use the real
models, so they need `GROQ_API_KEY`. Check your keys with `python manage.py ai_ping`.

Model ids are not in code: `LIGHT_MODEL`, `RESEARCH_MODEL` and `ANSWER_MODEL` in
`.env` set the three tiers, and `apps/ai/config/models.yaml` maps each agent to a
tier.

---

## How the code is organized

```
apps/
  authentication/   users, profiles, email codes, JWT
  organizations/    organizations, profile, preferences, links, documents, members, invitations
  knowledge/        document text extraction, chunks + embeddings, the company dossier
  discovery/        search agents, runs, run events, candidates (+ scheduled runs)
  opportunities/    the feed, triage, reviews, overview and insights
  learning/         decision memory, preference suggestions, evals (run_evals)
  proposals/        the Proposal Writer's proposals, versions, events, DOCX export
  outreach/         contacts, contact searches, outreach emails, deadlines, reminders
  common/           base model (12-char ShortUUID ids), emails, API helpers, `seed`
  ai/               the AI layer (see below)
config/             settings (base / development / production / test), urls, temporal (worker + Schedules)
```

### Rules every app follows

- **`api/views.py`** parses input and returns responses. Nothing else.
- **`services.py`** does **every write**. It locks the organization row, then
  rechecks the caller's role (`organizations.services.lock_organization`).
- **`selectors.py`** does reads, always scoped to the current user or organization.
- **`permissions.require_membership()`** is the only access check.
- **`choices.py`** holds the fixed vocabularies. Extend them instead of accepting
  free text, so the agents can filter reliably.
- Emails and background jobs are queued with `transaction.on_commit`, so they only
  happen once the data is saved.
- Workers claim a job by changing its status under a row lock, so a job runs at
  most once even if it is queued twice.
- Apps talk to each other through signals where it keeps them independent (for
  example, approving an opportunity sends `opportunity_approved`; the Proposal
  Writer listens).

### The AI layer (`apps/ai/`)

```
apps/ai/
  config/      models.yaml (role → tier), sources.yaml (discovery budgets, threshold,
               sources), proposals.yaml, outreach.yaml; validated by loader.py
  prompts/     <agent>/vN.md, versioned; the run records which version it used
  agents/      one module per agent (supervisor, researcher, verifier, judge, drafter,
               lead_scout, emailer, …): builds the prompt, asks for a Pydantic schema
  graphs/      discovery/ and proposal/ (LangGraph), lead_scout.py, runtime + budget
  schemas/     Pydantic structured outputs
  tools/       search (Tavily), fetch (httpx + trafilatura, robots.txt, SSRF guard),
               sources (Grants.gov, World Bank), contacts (Hunter), knowledge search
  llm.py       get_chat_model(role): the only place a chat model is built
  events.py    RunEvent + emitters (the UI timeline)
```

- **Nothing hardcoded:** no model ids, prompts or limits in Python.
- **`apps/ai/` never touches the ORM.** It reads through `apps/*/selectors.py`
  and writes through `apps/*/services.py` (runners get an emitter and a sink).
- **Every run has a budget** (searches, pages, tokens, wall time) and ends as
  `partial`, not a crash, when it runs out.
- **Web pages are untrusted data:** they are escaped inside tags in prompts, and
  instructions in them are ignored. Contact emails are kept only if they are
  written on the page.
- Every run records the prompt versions and models it used.

`CLAUDE.md` has the full list of rules for this repo.

---

## What happens when a user clicks “Search for opportunities”

1. The frontend runs the first agent the user may run (or creates one from the
   organization's preferences): `POST /organizations/<org>/agents/<agent>/run/`.
2. `discovery.services.start_run` checks the setup is complete, that the agent is
   on, and that no other run is active for the organization (one at a time, enforced
   in the database). It creates a `DiscoveryRun` (`queued`). Its settings come from
   the request body if given, else from the agent (sources, types, keywords) or the
   organization's run settings (depth, folders); its budget is that depth's preset in
   `sources.yaml`. Both are frozen on the run. It queues `discovery.tasks.execute_run` after commit. The API
   answers **202**.
3. The worker claims the run (`running`) and calls the runner named by
   `DISCOVERY_RUNNER`. The real one is the LangGraph graph in
   `apps/ai/graphs/discovery/`:
   - **load_context**: profile, preferences and the dossier
   - **plan** (supervisor agent): which sources to search, with which queries
   - **search**: Tavily web search, Grants.gov, World Bank, in parallel
   - **screen** (researcher agent): which results are worth reading
   - **read** (verifier agent, in parallel): opens each page, extracts the facts,
     drops closed, irrelevant or ineligible calls
   - **judge** (judge agent, in parallel): a 0–100 fit score against a written
     rubric, using the org's past decisions on similar opportunities
   - **save**: anything at or above `match_threshold` (55) is added to the feed;
     duplicates are skipped
   - **review**: if fewer than `target_results` were added and budget is left, one
     more round with new search angles
4. Every step writes `RunEvent`s. The frontend polls `GET …/runs/<run>/` every
   1.5 s and shows the steps, sources, pages and scores live.
5. The run ends `succeeded`, `partial` (a budget ran out; results are kept) or
   `failed`. New opportunities appear in the feed as "To review".

Approving one (`POST …/opportunities/<id>/reviews/`) moves it to `pursuing` and
starts the Proposal Writer in the same way (`proposals.tasks.run_proposal_job`).

---

## API map

Full, interactive docs: **http://localhost:8000/api/docs/** (OpenAPI schema at
`/api/schema/`). Everything is under `/api/v1/`; organization endpoints are under
`/organizations/<org>/`.

| Area | Endpoints (under `/organizations/<org>/` unless shown) |
|---|---|
| Auth | `/auth/register/`, `verify-email/`, `login/`, `refresh-token/`, `logout/`, `profile/`, `forgot-password/`, `reset-password/`, `change-password/`, `change-email/` |
| Organizations | `/organizations/`, `<org>/`, `setup/`, `preferences/`, `links/`, `folders/` (+ `<id>/`; only empty folders can be deleted), `documents/` (upload needs `folder_id`; `?folder=`; + `download/`, `reprocess/`), `members/`, `invitations/`, `activity/`, `archive/`, `restore/`, `leave/` |
| Invitations (as invitee) | `/organizations/invitations/preview/`, `accept/`, `decline/`, `received/` |
| Knowledge | `dossier/`, `dossier/regenerate/` |
| Discovery | `agents/` (an agent *adds* sources, types, keywords, folders and website `links` to the org's `run-settings/` and `sources/` and may set its own `depth`; `effective` shows the merged scope with each part's origin) (+ `run/`, optional body `{depth, sources, opportunity_types, folders}` for that run only), `run-settings/` (org defaults, used by every agent; admins `PUT`), `agent-sources/`, `runs/`, `runs/<run>/`, `runs/<run>/events/?after=N` |
| Opportunities | `opportunities/` (filters: `type`, `status`, `agent`, `saved`, `search`, `min_score`, `review`, `sort`), `opportunities/<id>/`, `opportunities/<id>/reviews/`, `rejection-reasons/`, `overview/`, `insights/?days=` |
| Learning | `preference-suggestions/` (+ `accept/`, `dismiss/`) |
| Proposals | `proposals/`, `opportunities/<id>/proposal/`, `proposals/<id>/` (+ `events/`, `versions/`, `versions/<n>/restore/`, `sections/<key>/`, `sections/<key>/rewrite/`, `export/`) |
| Outreach | `opportunities/<id>/contacts/`, `contacts/<id>/`, `opportunities/<id>/contact-search/`, `opportunities/<id>/outreach/`, `outreach/` (+ `<id>/`, `redraft/`, `discard/`, `approve/`), `outreach-purposes/` |
| Deadlines | `deadlines/`, `deadlines/calendar/` (.ics), `opportunities/<id>/calendar/` (.ics), `reminders/` |

Errors are `{"detail": …, "code": …}` (for example **409** `run_in_progress`,
`not_approved`, `busy`, `recently_emailed`) or a field map for **400**. Lists are DRF
pages (`count, next, previous, results`) unless the docs say "plain array".

---

## Background jobs

Every job runs on Temporal (`apps/common/jobs.py`: `@job` + `enqueue`; wiring in
`config/temporal.py`). Timers are Temporal Schedules.

| Job | Started by | What it does |
|---|---|---|
| `knowledge.process_document` | upload / reprocess | extracts text (PDF, DOCX, XLSX, PPTX, TXT/MD/CSV), chunks, embeds |
| `knowledge.refresh_dossier` | profile, preferences or document change (debounced), Regenerate | writes the company dossier |
| `DiscoveryRun` workflow | Run / Search for opportunities | the discovery graph; retried on another worker if one dies, within the budget left |
| `discovery-scheduled-searches` | **Schedule**, hourly | starts daily/weekly agents that are due (a run per agent per slot, enforced by the database) |
| `discovery-recover-runs` | **Schedule**, every 5 minutes | hands over again searches that never reached Temporal (then fails them clearly), fails searches whose worker died |
| `opportunities.embed_review` | each review | decision memory for the judge |
| `opportunities.analyze_opportunity` | each new find | the opportunity brief |
| `learning.refresh_suggestions` | rejections | suggests preference changes |
| `proposals.run_proposal_job` | approval, Write/Redraft, section rewrite | the Proposal Writer |
| `outreach.find_contacts` | Find contacts | the lead scout |
| `outreach.write_email` / `send_email` | AI draft / admin approval | drafts an email; sends an approved one once (never retried) |
| `outreach-deadline-reminders` | **Schedule**, daily 07:00 Nairobi | reminder emails with an .ics |

Jobs are retried when they fail or their worker dies, so each one claims or checks its
own state first. A job that still never finishes is detected as stale after the
`*_STALE_MINUTES` settings and can be started again. In tests, `JOBS_RUN_INLINE` runs
jobs inline on commit, with no Temporal server.

---

## Configuration

Every setting is in **`.env.example`** with a safe local default and a comment.
`python manage.py check` reports broken values (bad model config, throttle rates,
invite links, budgets shorter than stale timeouts, and so on).

The ones you will touch most:

| Setting | Default | Purpose |
|---|---|---|
| `DISCOVERY_RUNNER` / `PROPOSAL_RUNNER` | `fake` | `graph` for the real AI |
| `GROQ_API_KEY`, `LIGHT_MODEL`, `RESEARCH_MODEL`, `ANSWER_MODEL` | – | models |
| `TAVILY_API_KEY` | – | web search for discovery and the lead scout |
| `HUNTER_API_KEY` | – | optional: published emails for a funder's domain |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | off | trace agent runs in LangSmith |
| `DISCOVERY_FAKE_STEP_SECONDS` | `0.8` | speed of fake runs (`0` = instant) |
| `FRONTEND_URL` | `http://localhost:5173` | links in emails and calendar files |
| `SCHEDULED_RUNS_ENABLED` | `True` | let the hourly Schedule start daily/weekly agents |
| `OUTREACH_DAILY_SEND_LIMIT`, `OUTREACH_REPEAT_DAYS` | `20`, `7` | outreach safety limits |
| `DEADLINE_REMINDER_DAYS` | `14,3,1` | default reminder days |

Agent limits (budgets, match threshold, sources, rounds) live in
`apps/ai/config/sources.yaml`, `proposals.yaml` and `outreach.yaml`, not in `.env`.

---

## Tests, linting and conventions

```bash
pytest                 # or: make test   (~480 tests, ~2 min)
ruff check .           # or: make lint
ruff format .          # or: make format
make check             # system checks + missing migrations
```

- **Tests never call real APIs.** Models are faked with
  `apps.ai.testing.use_scripted_models`, and every HTTP call goes to a fake web (the
  root `conftest.py` fails a test that tries to reach the network).
- **Every change ships with tests** in `apps/<app>/tests/`.
- New settings go in `.env.example` with a safe default, are read with
  `decouple.config`, and get a check in the app's `checks.py` if they can be broken.
- Ruff, line length 100, Python 3.12.
- Branch from **`dev`** (never commit to `main`); conventional commits:
  `feat(scope): …`, `fix(scope): …`. Open the PR into `dev`.
- When an API response changes, update the frontend schemas in
  `leadline-ui/app/lib/schema/` in the same piece of work.

---

## Adding a feature

**A new endpoint**
1. Read in `selectors.py`, write in `services.py` (lock + role check, `record_event`
   for the activity log).
2. A thin view in `api/views.py` with `@extend_schema`, a serializer, a URL.
3. Tests through the API (`APIClient`) for the happy path, permissions (member,
   admin, outsider → 404) and error codes.

**A new agent or prompt change**
1. Prompt in `apps/ai/prompts/<agent>/vN.md` (add `v2` rather than editing a used
   version, so runs stay comparable).
2. Output schema in `apps/ai/schemas/`, agent module in `apps/ai/agents/`, role in
   `config/models.yaml`.
3. Test with scripted model replies; for judge or extraction changes, compare
   versions with `python manage.py run_evals judge --prompt judge=1`.

**A new opportunity source**
Add an adapter in `apps/ai/tools/sources/` (see `grants_gov.py`), add its name to
`KNOWN_ADAPTERS` in `registry.py`, and list it under a source category in
`apps/ai/config/sources.yaml`. Its HTTP calls go through `apps/ai/tools/http.py`, so
tests can route them to the fake web.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Search stuck on "Starting", summary stuck on "Updating", upload stuck on "Queued" | the Temporal worker or server is not running (`make dev` starts both); the Temporal UI shows the job waiting |
| Search failed: "could not be started" | it could not be handed to Temporal after `DISCOVERY_DISPATCH_ATTEMPTS` tries (server down) |
| Search adds `[Sample]` items | `DISCOVERY_RUNNER=fake`; set `graph` and restart `runserver` **and** the worker |
| 409 "A search is already running" | one run per organization; wait, or a stuck run is failed automatically after `DISCOVERY_STALE_RUN_MINUTES` |
| 409 `setup_incomplete` | complete the organization's setup (`GET …/setup/`) |
| Real search finds 0 | normal for narrow profiles: closed and vague calls are dropped. Upload documents and regenerate the dossier; check the run's events |
| `type "vector" does not exist` on migrate | install pgvector and run `CREATE EXTENSION vector;` in the database (and `template1` for tests) |
| First document processing is slow | the embedding model (~70 MB) downloads once into `.cache/fastembed` |
| Port 8000 in use | another service (often a Docker container); `runserver 8001` and point the frontend's `API_URL` at it |

### Migration notes

- `authentication.0002_alter_user_id` changed user ids to 12-character ShortUUIDs.
  It works on a fresh database; a database with existing users needs a reset or a
  data migration.
- `knowledge.0001_initial` needs the pgvector extension.
