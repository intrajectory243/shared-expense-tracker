# Shared Expense Tracker — Roadmap (now shipping as "Halves")

A self-hosted, open-source web app for splitting and tracking shared expenses between roommates (or any group). Built as a web app instead of native apps to avoid iOS/Android fragmentation.

**Design principle (applies to every phase):** keep it dead simple — minimal taps to log an expense, balance front and center, no clutter. This is the whole reason the project exists (existing apps/services felt too complicated).

---

## Phase 1 — Data Model ✅ Done (with extras)

Core object: **Expense Record**

- `amount`
- `payer` (who paid)
- `description`
- `date`
- `category` (rent, groceries, utilities, etc.)
- `participants` — list of users this expense is shared between

Note: not a simple "shared vs. personal" flag. Each expense has its **own participant list**, so costs only split among whoever is actually tagged on that specific item (e.g. milk bought for the whole house vs. milk bought for just two people).

**Extras beyond original scope:**
- Participants aren't just a list — each has a `share` (weight), so a split can be re-weighted (e.g. 2 shares vs 1) instead of always equal. Equal split is just everyone defaulting to share=1.
- `payer` and `created_by` are separate fields — any member can log an expense on someone else's behalf, and the record keeps both.
- A `Household` entity wraps everything (not in the original data model) — expenses, users, and settlements are all scoped to one.
- A `Settlement` record type exists (from/to/amount/date) to log real repayments, separate from `Expense`.

---

## Phase 2 — Backend ✅ Done (with extras)

- **Hosting model:** Self-hosted (not managed/serverless) — important since the goal is to open-source this for others to run themselves.
- **Language/Framework:** FastAPI (decided over Flask/Django) + SQLAlchemy 2.0.
- **Database:** SQLite, as planned — `DATABASE_URL` is env-configurable so a Postgres swap later stays a config change, not a rewrite. Postgres migration itself hasn't happened (not needed yet).
- **Caching:** Implemented already, ahead of the original schedule — not Redis, but an in-database `BalanceCache` table: one cached balance-summary row per household, invalidated (deleted) by any write that could change it. A miss just recomputes and refills, so it can never serve a stale value.
- **Auth:**
  - Email + password, `bcrypt` hashed, JWT bearer tokens (`python-jose`).
  - New sign-ups are **pending** until admin approves them — as planned.
  - **Role-based access** — admin vs. member, scoped to household — as planned.
- **Domain:** Unchanged — user provides their own; independent of the stack.

**Extras beyond original scope:**
- First user on a fresh instance auto-bootstraps as an approved admin (`BOOTSTRAP_ADMIN` setting), so a fresh self-hosted install is usable immediately without a manual DB edit.
- Direct invite links (`/users/invite`) — an admin-generated link that skips the pending-approval queue entirely, on top of the plain sign-up-and-wait flow.
- Membership lifecycle goes beyond pending/approved: **moved_out** (can still sign in, view history, settle up, but can't log new expenses) and **removed** (sign-in refused outright) — both reversible, and a household always keeps at least one admin as a guard rail.
- 44 pytest tests covering auth, expense splitting, balance math, membership lifecycle, weighted shares, the balance cache, push notifications, and schema migrations — not mentioned in the original roadmap at all.

---

## Phase 3 — Core Frontend ✅ Done

Screens:
- Login page
- Dashboard — current balance (who owes who, how much) front and center
- Add expense form
- History/list view of past expenses

Guiding constraint: launch-and-go simplicity, not feature-heavy.

Built as a single-file vanilla-JS SPA (`app.js`, no framework, no build step), served as static files by the same FastAPI process. Branded **"Halves"**, implemented from a Claude Design mockup bundle (`Halves - Screens.dc.html`) that was handed off and built pixel-for-pixel. A Household screen was added beyond the original 4 screens, to handle member approval, invites, and role management from Phase 2's extras.

---

## Phase 4 — Calculation Logic ✅ Done (future consideration already resolved)

- For each expense, split the cost **only among its tagged participants** (not a blanket shared pool) — weighted by share, not just an even split.
- Sum every user's net position across all expenses to determine final balances (who owes who, how much).
- **Settle-up action** to log real-world repayment and zero out the balance — can be logged by either side of the payment.
- **3+ people case — resolved:** the greedy min-cash-flow debt-simplification the roadmap flagged as a future need is implemented (`simplify_debts` in `balances.py`) — repeatedly matches the largest creditor with the largest debtor until every net balance is zero, minimizing the number of payments.

---

## Phase 5 — Deployment & Polish ✅ Done (with extras)

- ✅ Packaged for self-hosting — Docker (with Compose, bind-mounted for dev hot-reload) **and** the plain `requirements.txt` + `uvicorn` path both work, so both options from the original discussion shipped, not just one.
- ✅ PWA installability — web app manifest, service worker (static shell cached, API responses always hit the network), generated icon set. Installs on desktop and mobile.
- ✅ Notifications — Web Push (VAPID), not email: no SMTP/API-key config needed from a self-hoster, keys auto-generate on first use and persist in the DB. Fires on new expenses (to other household members) and new join requests (to admins).
- ✅ **DB efficiency at scale:**
  - SQLite **WAL mode** enabled (`PRAGMA journal_mode=WAL` in `database.py`) — lets reads and writes stop blocking each other. This alone comfortably covers realistic usage even at a stress-tested scale of 1000 households × 5 members (write volume stays well under 1/sec averaged, bursts clear in well under 100ms) — no further DB work needed at that scale.
  - **Future option, not being built now:** per-household DB sharding (one SQLite file per household instead of one shared file) — viable *if* write volume ever grows ~2 orders of magnitude beyond the above, or if per-household fault isolation becomes a goal. Estimated a day or two of focused work: split schema (users/households/invites stay shared; expenses/participants/settlements/balance-cache move per-household), add an LRU-capped engine registry keyed by `household_id`, rewire `get_db()` and routers to use it, migrations become per-file. Rough water mark at 1000 households: ~300–750MB RAM with a sane LRU cap (~100–150 concurrently-open DBs), multiplied by worker-process count if running multiple uvicorn workers.
  - **What keeps this option open (do this now, at zero cost):** don't write any query that joins or reads across households. Every current query already filters by `household_id` — this invariant is what makes sharding possible later, and it's free to preserve, just easy to accidentally break with a careless future feature.
- ✅ **Schema migrations (new item, found the hard way):** `Base.metadata.create_all()` only ever creates *missing* tables — it doesn't alter existing ones. The weighted-shares column added mid-beta needed a hand-written `ALTER TABLE` against the live database because of this; that doesn't scale to other people self-hosting the app. Replaced with [Alembic](https://alembic.sqlalchemy.org/): migrations run automatically on startup (`app/migrations.py`), so upgrading is just `git pull` + restart, and every future schema change ships as a reviewable script in `alembic/versions/` instead of a one-off manual fix. An initial baseline migration captures the full current schema; a persistent test (`tests/test_migrations.py`) asserts the migrated schema and the ORM models never drift apart.

---

## Status

**Beta — all five phases done, with several extras beyond original scope.**

Deliberately deferred, not blockers:
1. **Postgres migration** — `DATABASE_URL` is already a config swap, and Alembic's migrations run against Postgres the same way they do SQLite (SQLite just needs `render_as_batch` for its limited `ALTER TABLE` support, already enabled). Not needed until SQLite's single-writer model actually becomes the bottleneck.
2. **Per-household DB sharding** — future option only, see Phase 5 notes; not being built until write volume actually demands it.

*Originally: planning stage, no code written yet. This section now reflects the actual implementation, checked against `backend/` and `frontend/` in the `shared-expense-tracker-main` upload.*
