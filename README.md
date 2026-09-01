# Halves

A self-hosted, source-available expense tracker for splitting shared costs between roommates (or any group) — one running balance, minimal taps to log an expense, no clutter.

**Status: `0.9.4` — beta.** Live at [halves.ir](https://halves.ir). Core flows are built and tested end-to-end; the `0.x` version line means the app is still beta and the 1.0 stability commitment hasn't been made yet.

Created by [intrajectory243](https://github.com/intrajectory243), built collaboratively with [Claude Code](https://claude.com/claude-code).

---

## Why this exists

Most shared-expense apps try to do too much. Halves is built around one design principle that applies to every feature: **keep it dead simple.** Log an expense in a few taps, see the balance front and center, and don't make people think about anything else. It's a web app rather than separate iOS/Android apps specifically to avoid platform fragmentation — one codebase, works on any phone or laptop with a browser.

It's self-hosted by design: you run your own instance (Docker or a plain Python process), pointed at your own domain if you want one, with your own SQLite file as the source of truth. No accounts on a third-party service, no data leaving your server.

## Features

**Expenses**
- Each expense has its own participant list — costs split only among whoever's actually tagged on that item, not a blanket "shared" flag. Milk for the whole house and milk for just two people are tracked differently.
- Equal split by default. Admins can re-weight any expense afterwards (e.g. 2 shares vs 1 share instead of a straight 50/50) from the History screen.
- Every expense records who paid *and*, separately, who actually logged it — since any member can log an expense on someone else's behalf.

**Balances & settling up**
- One net number per person: what they're owed or what they owe.
- For groups of 3+, a greedy min-cash-flow algorithm suggests the minimum number of payments needed to settle everyone up, not just a raw pairwise table.
- Settle-up action logs a real repayment and zeroes out the balance; it can be logged by either side of the payment.

**Households & membership**
- Sign up to create a new household or join an existing one. Whoever creates a household is auto-approved as its admin, so it's usable immediately; joins are pending until one of that household's admins approves them.
- Admins can promote/demote roles, rename the household, invite people directly (shareable link, skips the approval queue), and decline pending requests.
- Leaving is a status change, never a deletion. **Moved out** keeps someone's sign-in but restricts it (they can still see history and settle up, just can't log new expenses). **Revoked** removes sign-in entirely. Either way, their past expenses, shares, and any outstanding balance stay in the books, and both states can be reversed.

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy 2.0, SQLite, JWT bearer auth (`python-jose`), `bcrypt` for password hashing.
- **Frontend:** Vanilla JS single-page app — no build step, no framework. A single `app.js` re-renders from a plain state object; FastAPI serves it as static files from the same process.
- **Tests:** pytest, 79 backend tests covering auth, expense splitting, balance math, membership lifecycle, weighted shares, the balance cache, push notifications, schema migrations, per-household DB isolation, backup/restore, and editable categories.

## Getting started

### Option A — Docker (recommended)

Requires [Docker](https://docs.docker.com/get-docker/) with the Compose plugin.

```bash
git clone https://github.com/intrajectory243/shared-expense-tracker.git
cd shared-expense-tracker
docker compose up --build
```

The app will be available at **http://localhost:8130**. The SQLite database lives in a named Docker volume (`db-data`), so it survives container restarts — `docker compose down -v` if you ever want to wipe it and start fresh.

`docker compose up` also loads `docker-compose.override.yml`, which bind-mounts `backend/app` and `frontend` into the container and runs the server with `--reload`, so local edits apply immediately (a browser refresh for frontend changes, automatic for backend changes) without rebuilding the image.

For a real deployment (auto-restart, code baked into the image, no `--reload`), use the base file only — `docker compose -f docker-compose.yml up -d --build` — and see [DEPLOY.md](DEPLOY.md).

### Option B — Run locally without Docker

Requires Python 3.12+.

```bash
git clone https://github.com/intrajectory243/shared-expense-tracker.git
cd shared-expense-tracker/backend
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The app (frontend included) will be available at **http://localhost:8000**.

### First run

Sign up on the login screen. Whoever **creates a household** is automatically approved as its admin — that's true for the first account on a fresh database and for anyone starting a new household on a shared instance. People who sign up to **join an existing household** land in a pending state until one of that household's admins approves them from the Household screen (or they use a direct invite link, which skips the queue).

## Configuration

Settings are read from environment variables (or a `.env` file in `backend/`), all optional with sensible defaults for local use:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./expense_tracker.db` | Swap for a Postgres URL later if you outgrow SQLite — the ORM makes that migration painless. |
| `SECRET_KEY` | `dev-secret-key-change-me` | **Change this** for any real deployment — it signs auth tokens. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 days) | How long a login session lasts. |
| `ALGORITHM` | `HS256` | JWT signing algorithm. |
| `BOOTSTRAP_ADMIN` | `true` | Whether the founder of a household (any household with no approved admin) is auto-approved as its admin. Set `false` to opt the whole instance out and assign admins out-of-band. |

## Database migrations

Schema changes are managed with [Alembic](https://alembic.sqlalchemy.org/), not `create_all()` — that only creates missing tables, it doesn't alter existing ones, so an early beta relied on hand-editing the live database whenever a column changed. Migrations run automatically on every startup (`app/migrations.py`, called from `app/main.py`), so upgrading is just `git pull` and restart — no manual command required for a normal update.

If you're changing `app/models.py`, generate the migration that goes with it:

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
```

Review the generated file in `alembic/versions/` before committing — autogenerate is reliable for additive changes (new tables/columns) but won't always guess renames or data backfills correctly.

## Running tests

```bash
cd backend
pytest
```

(Or, if the Docker container is already running: `docker compose exec app bash -c "cd /app/backend && pytest"`.)

## Project structure

```
backend/
  app/
    main.py          FastAPI app, mounts the frontend as static files
    models.py         SQLAlchemy models
    schemas.py        Pydantic request/response schemas
    dependencies.py   Auth + authorization dependency chain
    balances.py        Balance & debt-simplification calculation
    migrations.py      Runs Alembic migrations on startup
    routers/            auth, users, households, expenses, balances, push
  alembic/             Migration scripts (see "Database migrations" above)
  tests/               pytest suite
frontend/
  index.html
  app.js               Single-file SPA: state, render, event handling
  styles.css
expense-tracker-roadmap.md   Original phased roadmap this was built from
```

## Roadmap

Built in phases: data model → backend → core frontend → calculation logic → deployment & polish (see `expense-tracker-roadmap.md`). Docker packaging, PWA installability, and push notifications are done; a Postgres migration path and DB sharding are deliberately deferred until they're actually needed.

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — free to use, self-host, modify, and share for any noncommercial purpose. Commercial use isn't permitted without the author's permission. See the [LICENSE](LICENSE) file for the full terms.
