# Deploying Leadline

```
browser ──► leadline.digitalappex.co.ke        Vercel (leadline-ui, region fra1)
   │               │ every page and /api/* call, server-side
   │               ▼
   └─files──► leadline-api.digitalappex.co.ke   the server: Caddy (/srv/edge) ─► api
                                               api, worker, temporal, postgres, redis
```

- **The UI** runs on Vercel. Its server calls the API for every visitor, adding the
  token from the session cookie; the browser never holds one. Functions run in `fra1`
  (Frankfurt), next to the server (4 ms apart).
- **Files** go straight between the browser and the API with a short-lived ticket
  (`apps/common/api/tickets.py`): Vercel caps a request at 4.5 MB.
- **The backend** is this repo's `docker-compose.yml`, in `/srv/leadline` on the
  server. Only the shared Caddy (`/srv/edge`, also serving the other apps) is open to
  the internet; the API joins its `edge` network as `leadline-api`.

## Deploy a new version

Commit, then:

```bash
DEPLOY_HOST=root@<server-ip> scripts/deploy.sh
```

It copies the committed code to `/srv/leadline/releases/<commit>`, backs up the
database, builds, starts, waits for the health checks (the API migrates as it starts;
the worker waits for it), and installs the Caddy site if it changed (checked first;
a broken one is put back). Then push to GitHub; Vercel deploys the UI on its own.

Roll back: `ln -sfn /srv/leadline/releases/<older> /srv/leadline/backend`, then
`docker compose build api && docker compose up -d --wait` in `/srv/leadline`.
Migrations don't roll back by themselves; restore the pre-deploy backup if needed.

## On the server

```bash
cd /srv/leadline
docker compose ps                      # everything up and healthy?
docker compose logs -f api worker      # what it's doing
docker compose exec api python manage.py createsuperuser
docker compose exec api python manage.py check --deploy
ssh -L 8233:localhost:8233 root@<server-ip>   # then http://localhost:8233: Temporal's UI
```

Settings live in `/srv/leadline/.env` (from `env.production.example`; chmod 600).
After changing it: `docker compose up -d --wait` (recreates what changed).

## Backups

`/srv/leadline/backup.sh` runs daily at 02:30 (`/etc/cron.d/leadline-backup`) and
before every deploy: the database (`pg_dump --format=custom`) and the uploaded
documents, into `/srv/leadline/backups`, kept 14 days. They are on the same disk:
copy them off the server regularly.

Restore the database:

```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/db-<stamp>.dump
```

## First setup (done once)

1. **DNS (Cloudflare, digitalappex.co.ke).**
   - `A leadline-api → <server-ip>`, **DNS only** (grey cloud): Caddy gets its own
     certificate from Let's Encrypt, and sees visitors' real addresses.
   - `CNAME leadline → <the value Vercel shows>`, **DNS only** (Vercel's advice: it
     serves its own certificate and CDN).
2. **Server.** `mkdir /srv/leadline`, write `/srv/leadline/.env`, run the deploy.
3. **Vercel.** Import the `leadline-ui` GitHub repo (framework: React Router). Settings:
   - Environment variables (Production):
     `API_URL=https://leadline-api.digitalappex.co.ke/api/v1`,
     `SESSION_SECRET=<openssl rand -base64 32>`,
     `UI_PROXY_SECRET=<the same as in /srv/leadline/.env>`,
     `VITE_SITE_URL=https://leadline.digitalappex.co.ke`.
   - Domains: add `leadline.digitalappex.co.ke`.
   - The region comes from `vercel.json` (`fra1`).

## Why it is built this way

- **One Temporal dev server, its history in a SQLite file** (`temporal` volume): one
  process, ~90 MB, enough for one server's traffic. With more traffic, move Temporal
  to Postgres (`temporalio/server` with its own schema) without changing the app.
- **Redis** is only the shared cache: the throttles and the search rate limit must
  count across the API's processes and the worker.
- **The visitor's address.** All traffic reaches Django from Vercel, so the UI sends
  each visitor's address with a shared secret (`UI_PROXY_SECRET`), and Django believes
  it only with the secret (`apps/common/middleware.py`). Without that, every visitor
  would share one sign-in limit.
- **The embedding model is in the image**, so the server never downloads it.
