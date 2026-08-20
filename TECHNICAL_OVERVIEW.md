# Halves — Technical Overview

A self-hosted, source-available web app for splitting and tracking shared expenses between roommates (or any group). This document is the full technical picture: architecture, data model, every subsystem, and the reasoning behind the non-obvious decisions. For a lighter-weight intro, see [README.md](README.md); for phase-by-phase history, see [expense-tracker-roadmap.md](expense-tracker-roadmap.md).

**Status:** beta. All five original roadmap phases are done, several with extras beyond original scope.

---

## Design principle

One rule applies to every feature: **keep it dead simple.** Minimal taps to log an expense, the balance front and center, nothing else competing for attention. The project exists because existing shared-expense apps felt over-built for the actual problem.

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python, FastAPI | Chosen over Flask/Django |
| ORM | SQLAlchemy 2.0 (`Mapped`/`mapped_column`) | Typed declarative models |
| Database | SQLite, WAL mode | `DATABASE_URL` is env-configurable — a Postgres swap is a config change, not a rewrite |
| Migrations | Alembic | Auto-runs on startup |
| Auth | JWT bearer tokens (`python-jose`), `bcrypt` hashing | No sessions/cookies |
| Frontend | Vanilla JS SPA, no framework, no build step | Single `app.js`, `state` object, full-`innerHTML` re-render |
| Push | Web Push (VAPID) via `pywebpush` | No SMTP/email dependency |
| Tests | pytest, 44 tests | `backend/tests/` |
| Packaging | Docker + Compose (dev hot-reload), or plain `uvicorn` | Both paths work |
| License | PolyForm Noncommercial 1.0.0 | Free for any noncommercial use; commercial use reserved |

---

## Architecture

### Backend layout

```
backend/
  app/
    main.py          FastAPI app; runs migrations, mounts frontend as static files
    models.py         SQLAlchemy models (10 classes, see Data Model below)
    schemas.py         Pydantic request/response schemas
    config.py           Settings (env-var driven, pydantic-settings)
    database.py          Engine/session setup, WAL pragma
    auth.py                Password hashing (bcrypt) + JWT issuance/decoding
    dependencies.py         Auth dependency chain (see below)
    balances.py               Balance calculation + debt simplification + cache
    push.py                    VAPID key management + Web Push sending
    migrations.py               Runs Alembic on startup
    routers/
      auth.py     signup, login, /me, invite preview/accept
      users.py     list/pending/former members, invite, approve, update, decline
      households.py  list (public), rename
      expenses.py    CRUD + PATCH shares, triggers push + cache invalidation
      balances.py     GET (cached), settlements
      push.py          VAPID public key, subscribe, unsubscribe
  alembic/            Migration scripts
  tests/              pytest suite (44 tests across 6 files)
frontend/
  index.html
  app.js              ~1560 lines: state, render, event handling, API client
  styles.css          ~520 lines
  manifest.json, sw.js, icon-*.png   PWA assets
```

**Auth dependency chain** (`dependencies.py`), each stricter than the last:
```
get_current_user           → valid JWT, not status=removed
  → get_current_approved_user  → status in {approved, moved_out}
    → require_household          → has a household_id
      → get_current_active_user    → status == approved (excludes moved_out)
        → get_current_admin           → role == admin
```
Routes pick whichever level they need — e.g. reading history only needs `require_household` (moved-out members can still see it), but logging a new expense needs `get_current_active_user`.

### Frontend architecture

No build step, on purpose. `app.js` holds one `state` object; every user action mutates `state` then calls `render()`, which rebuilds the relevant screen's HTML from scratch (`buildHtml()` switches on `state.route`) and reassigns `innerHTML`. Event handling is delegated at the `#app` root via `data-action`/`data-field` attributes rather than per-element listeners, so the full-innerHTML-replace pattern doesn't lose event bindings on re-render.

Two things this pattern had to specifically guard against, both hit as real bugs this session:
- **Animation replay on every re-render** — recreating the DOM node every render also restarts any CSS entrance animation. Fixed by tracking `lastRenderedSheet` and suppressing animation (`style.animation = 'none'`) on renders that aren't opening a new sheet.
- **CSS animations silently drop static `transform`** — a `@keyframes` block animating `transform` fully replaces the base rule's `transform` for the animation's duration, not composes with it. A sheet-centering rule (`translateX(-50%)`) that wasn't repeated inside the keyframes disappeared for the whole entrance animation.

---

## Data model

Split across two SQLite files since Phase 7 (roadmap) — see "Multi-database
architecture" below for why and how. Logically the shape is unchanged; the
diagram below shows the conceptual relationships, but `Expense`/
`ExpenseParticipant`/`Settlement`/`BalanceCache` live in a separate
per-household file from `User`/`Household`, so anything crossing that
boundary (dashed below) is a plain int column resolved in application code,
not a SQLAlchemy `relationship()` or an enforced `FOREIGN KEY`.

```
Household 1───* User                          } shared file
Household 1┄┄┄* Expense                        } per-household file,
Household 1┄┄┄* Settlement                     } one per household_id
Household 1┄┄┄1 BalanceCache                   }
Expense   1───* ExpenseParticipant ┄┄┄1 User    (Expense↔Participant: same file. ↔User: crosses)
Settlement  ┄┄┄1 User (from)
Settlement  ┄┄┄1 User (to)
User        ┄┄┄1 User (payer / created_by on Expense)
PushSubscription 1───* User                     } shared file
```

| Table | Lives in | Key columns | Notes |
|---|---|---|---|
| `households` | shared | `id`, `name` | Root of all scoping — every query filters by `household_id` |
| `users` | shared | `email` (unique), `password_hash`, `role` (admin/member), `status` (pending/approved/moved_out/removed), `household_id`, `invite_token` | |
| `expenses` | per-household | `payer_id`, `created_by_id`, `amount`, `description`, `category`, `date` | `payer` and `created_by` are separate — any member can log an expense on someone else's behalf. `household_id`/`payer_id`/`created_by_id` are plain ints, not FKs (see below) |
| `expense_participants` | per-household | `expense_id`, `user_id`, `share` (float, default 1.0) | Association **object** (not a plain `secondary=` table) specifically because `share` needs to live on the join row; composite PK on `(expense_id, user_id)`. `expense_id` stays a real FK (same file); `user_id` is a plain int (User is in the shared file) |
| `settlements` | per-household | `from_user_id`, `to_user_id`, `amount`, `date` | Real-world repayment record, separate from `Expense` |
| `balance_cache` | per-household | `household_id` (PK), `payload` (JSON text), `computed_at` | One row per household; see Caching below |
| `app_settings` | shared | `key` (PK), `value` | Generic instance-level KV store; currently holds the auto-generated VAPID keypair |
| `push_subscriptions` | shared | `user_id`, `endpoint` (unique), `p256dh`, `auth` | One row per browser/device subscribed to push |

**Membership lifecycle** is more than pending/approved: **moved_out** keeps sign-in but blocks logging new expenses (history/settle-up still work — their existing balance doesn't just disappear); **removed** blocks sign-in outright, even on an already-issued token (checked in `get_current_user`, not just at login). Both are reversible. A household always keeps at least one admin — enforced as a guard rail on every role/status change. The `User` hard-delete path (`DELETE /users/{id}`) only ever applies to still-pending requests, which by definition have no expense rows yet — the only case where losing the old cross-file `ON DELETE CASCADE` into `expense_participants` would otherwise matter.

---

## Core features

### Expenses & weighted shares
Each expense has its own participant list — costs split only among whoever is actually tagged on that item, not a blanket household-wide split. `share` is a weight, not a currency amount: a participant with `share=2` owes twice what a `share=1` participant owes on the same expense. Equal split is just the default of every tagged participant at `share=1`. Admins can re-weight an existing expense's split from the History screen (`PATCH /expenses/{id}/shares`).

### Balances & debt simplification
Net position per user = what they paid − their own weighted share of every expense they're tagged on, adjusted by settlements already logged. For 3+ people, a raw pairwise "who owes who" table isn't well-defined, so `simplify_debts()` runs a **greedy min-cash-flow** algorithm: repeatedly pair the largest creditor with the largest debtor and settle as much of that pair as possible, until every net balance is zero. This minimizes the number of payments needed to settle the whole household, not just lists every pairwise debt.

### Settlements
Logs a real repayment and zeroes out the corresponding balance. Either side of the debt can log it (not just the person who owes).

### Households & membership
First-ever signup on a fresh instance auto-bootstraps as an approved admin (`BOOTSTRAP_ADMIN` setting) — a fresh self-hosted install is usable immediately, no manual DB edit required. Beyond that, new signups are **pending** until an admin approves them, or they can join via a direct invite link (`POST /users/invite`) that skips the queue entirely.

---

## Performance engineering

### N+1 query fix
`compute_net_balances()` and the expense list/detail endpoints originally lazy-loaded `participant_shares` per expense — fine at a handful of expenses, but each additional expense meant one more round trip. Fixed with `selectinload(Expense.participant_shares)`, collapsing that part into a fixed small number of queries regardless of expense count. Resolving each participant's `User` (needed for `ExpenseOut.participants`) is a separate single batched query against the shared file — `_stitch_expense_users()` in `routers/expenses.py` collects every needed user id across the whole result set first, then does one `User.id.in_(...)` lookup, not one per expense (see "Multi-database architecture" below for why this became a manual step instead of a relationship).

### Balance cache — invalidate-on-write
`balance_cache` holds one row per household: the JSON-serialized output of the last `get_balance_summary()` call. Any write that could change a balance (new expense, share edit, deleted expense, settlement) deletes that household's row **before** committing, in the same transaction — so invalidation can never succeed or fail independently of the write that made it necessary. A cache miss just recomputes and refills.

**Why this can't drift:** a cache row is only ever populated by a real `get_balance_summary()` call — there is no separate "update the cache" code path that could compute a different answer than a fresh read would. The only two states are "absent" (falls through to a real computation) or "exactly what a fresh computation produced." No incremental/delta update path exists to get out of sync.

### WAL mode
`PRAGMA journal_mode=WAL` (set on every connection in `database.py`) lets reads proceed without blocking on a concurrent writer and vice versa, replacing SQLite's default rollback-journal locking. Verified via stress test to comfortably cover 1000 households × 5 members (write volume stays well under 1/sec averaged, bursts clear in well under 100ms) with no further DB work needed at that scale.

### Multi-database architecture (roadmap Phase 7)
One SQLite file per household for `expenses`/`expense_participants`/`settlements`/`balance_cache`, separate from the shared file holding `users`/`households`/`app_settings`/`push_subscriptions` (see the Data model table above for which table lives where). Originally scoped as a scaling option gated on write volume; actually built as a prerequisite for admin backup/restore (roadmap Phase 8) instead — with each household's data already isolated to its own file, "back up a household" is just "copy that file," with no risk of one household's backup leaking another's data the way a raw copy of one shared file would.

- **`app/database.py`** — `SharedBase`/`HouseholdBase`, two separate declarative bases (two separate `.metadata`s) so the ORM keeps a single ordinary look while ~~one~~ two engines back it.
- **`app/household_db.py`** — an LRU-capped registry (`household_id -> Engine`, cap 128) that lazily creates and migrates a household's file the first time anything asks for it, and disposes evicted engines safely (an in-flight request's checked-out connection still finishes normally — `Engine.dispose()` doesn't sever it).
- **Two independent Alembic streams** — `alembic/shared/` and `alembic/household/`, each with its own `env.py`/`versions/`, both still using `render_as_batch=True`. `app/migrations.py` runs the shared stream once on startup (as before) and walks any existing household files to catch them up; a brand-new household file gets migrated to head at creation time instead.
- **No cross-file relationships or FKs** — SQLite can't join or enforce a `FOREIGN KEY` across two files, and a SQLAlchemy `relationship()` can't span two engines. Every id column that used to cross the boundary (`Expense.payer_id`, `ExpenseParticipant.user_id`, `Settlement.from_user_id`/`to_user_id`) is a plain int now; router code resolves the `User` side itself in one batched shared-DB query and stitches it on as a plain instance attribute before handing the object to its Pydantic response model (`app/balances.py::get_balance_summary()` was already doing exactly this for balance names before the split — it's the template the rest followed).
- **One-time cutover** — `backend/scripts/split_to_sharded_dbs.py`, hand-run once per install upgrading from the single-file layout: backs up the original file, copies each household's rows into its own new file, verifies row counts match, and only then drops the moved tables from the shared file (deliberately raw SQL there, not a migration, so a routine `alembic upgrade head` can never do that drop by accident before the copy step has actually run).

### What's deliberately *not* built yet
- **Postgres migration** — `DATABASE_URL` is already a config swap, and Alembic's migrations run against Postgres the same way (SQLite just needs `render_as_batch` for its limited `ALTER TABLE` support, already enabled). Not worth doing until SQLite's single-writer model is an actual bottleneck. (Per-household sharding above is SQLite-specific — a Postgres migration would need its own multi-tenancy story if it ever happens.)

---

## PWA & offline support

`frontend/manifest.json` + `frontend/sw.js` make the app installable on desktop and mobile. The service worker caches the static shell only (`/`, `styles.css`, `app.js`, icons) — API responses (`/auth`, `/users`, `/households`, `/expenses`, `/balances`, `/settlements`, `/health`) are explicitly excluded from the cache, so balance/expense data always comes from the network, never stale. Icon set includes transparent-background icons for "any" purpose/favicon and solid-background variants where platforms require them (maskable icons, `apple-touch-icon`).

---

## Push notifications

**Architecture decision:** Web Push (VAPID), not email — builds directly on the service worker already shipped for PWA support, and needs zero SMTP/API-key configuration from a self-hoster, matching the "usable immediately after fresh install" philosophy.

- `app/push.py` generates a VAPID keypair on first use and persists it in `app_settings` (PEM private key + raw public key) — the process-local cache holds the `(Vapid object, public key string)` pair together so a warm cache never needs a DB round trip.
- `PushSubscription` rows are keyed by browser endpoint (unique); re-subscribing the same endpoint under a different logged-in user re-homes it rather than duplicating.
- `send_to_users()` is best-effort: a `WebPushException` with a 404/410 response prunes the dead subscription; any other failure (including raw network errors like timeouts, which `pywebpush` does *not* wrap as `WebPushException`) is logged and swallowed, never propagated to the caller. An explicit `timeout=5` is passed to every `webpush()` call — without it, `pywebpush`'s own default (`10000`) is fed straight to `requests` as **seconds**, effectively no timeout at all.
- Triggers: **new expense** → all other approved household members; **new join request** → all household admins.

Frontend: `sw.js` handles `push` (shows a notification) and `notificationclick` (focuses or opens the app). `app.js` exposes an On/Off toggle in the menu sheet that walks through `Notification.requestPermission()` → fetch the VAPID public key → `pushManager.subscribe()` → `POST /push/subscribe`.

---

## Database migrations

Originally the schema was managed with `Base.metadata.create_all()` at startup — which only ever creates *missing* tables, never alters existing ones. This bit for real: the weighted-shares `share` column added mid-beta needed a hand-written `ALTER TABLE` run directly against the live database, which doesn't scale to anyone else self-hosting the app.

Replaced with **Alembic**:
- Migrations run automatically on startup (`app/migrations.py` → `command.upgrade(cfg, "head")`), so upgrading a self-hosted instance is just `git pull` + restart.
- `render_as_batch=True` is enabled (SQLite can't do most `ALTER TABLE` operations directly — Alembic recreates the table under the hood instead).
- An initial baseline migration (`alembic/versions/f60859b057c9_initial_schema.py`) captures the full current schema exactly.
- A persistent test (`tests/test_migrations.py`) builds the schema two ways — via `alembic upgrade head` and via `Base.metadata.create_all()` — and asserts they're column-for-column identical, so a future migration/model drift breaks the test suite immediately instead of silently diverging.

Generating a new migration after a model change:
```bash
cd backend && alembic revision --autogenerate -m "describe the change"
```

---

## Testing

44 tests, `backend/tests/`:

| File | Tests | Covers |
|---|---|---|
| `test_app.py` | 8 | Core auth/expense/balance flow |
| `test_membership.py` | 12 | Roles, approve/decline, moved-out/removed lifecycle, invites |
| `test_expense_shares.py` | 7 | Weighted-share creation and editing |
| `test_balance_cache.py` | 6 | Cache population, hits, invalidation on every write type |
| `test_push.py` | 9 | Subscribe/unsubscribe ownership, both triggers, dead-subscription pruning, network-failure isolation |
| `test_migrations.py` | 2 | Migrated schema vs. ORM-model schema equivalence, migration idempotency |

Run with `pytest` from `backend/`, or `docker compose exec app bash -c "cd /app/backend && pytest"` against the dev container.

---

## Deployment

**Docker (recommended):** `docker compose up --build` — app at `http://localhost:8130`, SQLite file in a named volume (`db-data`) that survives restarts. The dev compose file bind-mounts `backend/app`, `frontend`, `backend/tests`, `backend/alembic` read-only (writable for `alembic/` itself, to allow `alembic revision --autogenerate`) with `--reload`, so local edits apply without a rebuild — only new pip dependencies need `docker compose build`.

**Without Docker:** plain `venv` + `pip install -r requirements.txt` + `uvicorn app.main:app --reload`, app at `http://localhost:8000`.

---

## Configuration reference

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./expense_tracker.db` | Swap for Postgres later — config change, not a rewrite |
| `SECRET_KEY` | `dev-secret-key-change-me` | **Change for any real deployment** — signs auth tokens |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 days) | Login session length |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `BOOTSTRAP_ADMIN` | `true` | Whether the first-ever signup auto-approves as admin |

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — chosen over a Creative Commons license because CC explicitly advises against using CC licenses for software (no handling of source vs. compiled forms, no patent terms). PolyForm is the modern, purpose-built equivalent: free to use, self-host, modify, and redistribute for any noncommercial purpose; commercial use requires the author's permission. The author retains full commercial rights as copyright holder regardless — the license only restricts what's granted to everyone else. "Noncommercial" is drawn at *commercial advantage*, not just resale — a company using it internally without ever reselling it still needs permission.

---

## Project status

All five roadmap phases complete:

| Phase | Status |
|---|---|
| 1 — Data model | ✅ Done, with extras (weighted shares, `Household` entity, `payer`/`created_by` split, `Settlement` type) |
| 2 — Backend | ✅ Done, with extras (bootstrap admin, invite links, moved_out/removed lifecycle) |
| 3 — Core frontend | ✅ Done (login, dashboard, add expense, history, + Household admin screen) |
| 4 — Calculation logic | ✅ Done (weighted splits, min-cash-flow debt simplification) |
| 5 — Deployment & polish | ✅ Done, with extras (Docker + manual, WAL mode, PWA, push notifications, Alembic migrations) |

**Deliberately deferred, not blockers:** Postgres migration, per-household DB sharding — both discussed above, neither needed at current or realistically-projected scale.

## Commit history

```
c667b84  v0: roadmap + backend foundation (data model, auth, expenses, balances)
7b094fd  v0: Phase 3 frontend + admin household screen
afc8186  beta: member lifecycle, weighted expense shares, Docker dev setup
0292b97  beta: README, N+1 fix on balances, invalidate-on-write balance cache
43ec573  beta: WAL mode, PWA installability, sheet-animation centering fix
3dbca11  beta: web push notifications for new expenses and join requests
a7b2bab  chore: stop tracking the local design-reference folder
7f08b6c  beta: add PolyForm Noncommercial license, README cleanup for going public
```
