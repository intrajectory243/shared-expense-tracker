# Deploying Halves

Self-hosted, single Docker container, SQLite in a named volume. These notes
are for running a real instance (e.g. halves.ir); for local hacking see the
"Getting started" section of the README.

## Layout

- `docker-compose.yml` — **production** shape: runs the code baked into the
  image, `restart: unless-stopped`, no `--reload`, only the `db-data`
  volume mounted.
- `docker-compose.override.yml` — development only. `docker compose` loads
  it **automatically**; production avoids it by passing `-f
  docker-compose.yml` explicitly.

So on the server, every compose command is:

```bash
docker compose -f docker-compose.yml <...>
```

Consider a shell alias: `alias dc='docker compose -f docker-compose.yml'`.

## First-time setup

```bash
git clone https://github.com/intrajectory243/shared-expense-tracker.git /opt/halves
cd /opt/halves

# Secret used to sign auth tokens. MUST be set for a real instance —
# the app's built-in default is well-known.
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" > backend/.env

docker compose -f docker-compose.yml up -d --build
```

`backend/.env` is git-ignored and read into the container (see `env_file`
in `docker-compose.yml`). Other optional settings (`ACCESS_TOKEN_EXPIRE_MINUTES`,
`BOOTSTRAP_ADMIN`, …) go in the same file — see the README config table.

Put a TLS-terminating reverse proxy (Caddy, nginx, Traefik) in front of
port 8130.

## Updating

```bash
cd /opt/halves
git fetch --tags
git checkout v0.9.4          # or: git pull   (to track main)
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml exec app curl -s localhost:8000/health
```

Alembic migrations (both the shared and per-household streams) run
automatically on container start — no manual step.

### Rolling back

```bash
git checkout v0.9.3
docker compose -f docker-compose.yml up -d --build
```

Safe as long as the older version's schema is a subset of what's on disk.
Alembic does not auto-downgrade, so a rollback that spans a migration
needs `alembic ... downgrade` run by hand first — avoid by testing
upgrades on a copy of the volume.

## Backups

Everything lives in the `db-data` volume: the shared DB
(`expense_tracker.db`) and one file per household under `households/`.

```bash
# Snapshot the whole volume
docker run --rm -v halves_db-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/halves-db-$(date +%F).tar.gz -C /data .
```

Admins can also self-serve a per-household export from **Menu → Backup**
inside the app.

## Health

```bash
docker compose -f docker-compose.yml exec app curl -s localhost:8000/health
# {"status":"ok","version":"0.9.4"}
docker compose -f docker-compose.yml logs -f --tail 50
```
