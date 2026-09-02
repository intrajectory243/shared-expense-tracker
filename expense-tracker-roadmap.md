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

## Phase 6 — Internationalization ✅ Done

- **Per-user language**, not per-household: English and Persian (Farsi) only for this pass — the design mockups also sketch Arabic, but that's explicitly out of scope until a language actually gets added. Chosen at signup (inline chips), changeable anytime from Menu → Language, self-service even for a still-pending user.
- **Per-household display currency**, admin-controlled: Toman, Rial, USD, EUR, AED. Purely cosmetic relabeling — the stored `amount` is never converted, only re-formatted. A live preview in the Currency sheet shows exactly what will change before saving.
- **Backend:** `Language`/`Currency` enums + columns (`User.language`, `Household.currency`), an Alembic migration with safe defaults for existing rows, a self-service `PATCH /users/me/language` endpoint, and `PATCH /households/{id}` extended to take `currency`. 10 new tests, all passing alongside the existing 44.
- **Frontend architecture:** flat dotted-key dictionary (`en`/`fa`) behind a single `t(key, params)` lookup, `state.lang` synced from the account on login/signup, `document.documentElement.lang`/`dir` driven centrally, and locale-aware number/date formatting (Persian digits + Jalali calendar via `Intl`'s `-u-ca-persian-nu-arabext`).
- **RTL isolated by construction, not by convention:** almost every rule uses CSS logical properties (`margin-inline-*`, `text-align: start`, etc.), so it's direction-agnostic automatically. The two things that can't be expressed that way (the CSS-border-triangle chevrons, the Persian font swap) are consolidated into one bannered block at the end of `styles.css` — the single place anyone ever needs to touch for a language/direction change.
- **Full copy pass, not just the mocked screens:** every screen and sheet (signup, home, history, add/settle/edit-shares sheets, household admin, member sheet, invite, rename, menu, accept-invite, the new Language/Currency sheets) and every toast/error message routes through `t()` — roughly 240 keys, in parity across both languages.
- **Language switch on both entry points, shipped:** the chip row was only on sign-up, leaving a returning Persian speaker stranded on an English sign-in with no way out until after login. Now on sign-in too — same `LANG_OPTIONS` chip row, same position under the tagline, its own pre-auth `login.pickLang` action (sets `state.lang`/`localStorage` directly, same as `signup.pickLang` — no sheet to close, no authenticated endpoint to call, unlike the in-app Menu → Language switch).
- **Persian screen set**: every screen Phase 6 already put through `t()` was cross-checked against the handoff's `-fa` reference screens (`login-fa`, `pending-fa`, `menu-fa`, `settle-fa`, `cats-fa`, `household-fa`) for copy drift — no structural gaps found; the household screen's unclaimed-member Persian strings (new from Phase 8's frontend) were written against the same reference.
- **Verified live**, not just statically: drove the running app through a real signup → approval-pending → admin login → add expense → switch language mid-session → history → currency change flow in a headless browser, in both languages, with zero console errors.

---

## Phase 7 — Per-Household DB Sharding ✅ Done

Originally sketched in Phase 5 as a future option gated on write volume or fault-isolation needs — scheduled ahead of Phase 8 as its prerequisite, not for scaling. Doing it first turns Phase 8's hardest problem (scoping a backup so it can't leak another household's data) into a non-issue: one SQLite file per household means there's nothing else *in* the file to leak.

- **Two SQLite files per deployment instead of one**: a shared file (`User`/`Household`/`AppSetting`/`PushSubscription`) plus one file per `household_id` (`Expense`/`ExpenseParticipant`/`Settlement`/`BalanceCache`), created lazily on first per-household access — never at signup time.
- **Two declarative bases** (`SharedBase`/`HouseholdBase` in `app/database.py`/`app/models.py`), since SQLAlchemy `relationship()` can't span two engines and SQLite can't join across separate files. Cross-boundary foreign keys became plain columns (SQLite wasn't enforcing them anyway); cross-boundary relationships were replaced by a manual fetch-and-stitch — the pattern `app/balances.py::get_balance_summary()` already used pre-split, now applied consistently in `expenses.py`/`balances.py` via `_stitch_expense_users()`.
- **LRU-capped engine registry** (`app/household_db.py`, cap 128 per the original Phase 5 sizing note) resolves or lazily creates a household's engine, applies the same WAL pragma as the shared DB, and safely evicts idle engines (`Engine.dispose()` doesn't sever an in-flight checked-out connection).
- **Two independent Alembic streams** (`alembic/shared/`, `alembic/household/`), each with its own `env.py`/`versions/`. `run_migrations()` runs the shared stream once, then walks every existing household file and migrates it to head; a new household file is migrated at creation time via the same household-stream config.
- **One-time cutover script** (`backend/scripts/split_to_sharded_dbs.py`) for existing installs: backs up the original file, copies each household's rows into its own new file, verifies row counts match before touching anything, and only then drops the moved tables from the shared file via raw SQL — deliberately *not* a routine Alembic migration, since `run_migrations()` runs automatically on every startup and could otherwise destroy live data on a deploy that predates the split.
- **Verified live**: 59 tests passing (3 new, covering on-disk file isolation, genuine cross-household isolation via a direct second connection, and that the manual name-stitch still reflects live renames); the cutover script exercised against realistic synthetic multi-household data with byte-level row-count verification; a full container restart confirmed both the shared file and per-household files survive with `PRAGMA integrity_check` clean.

---

## Phase 8 — Admin Backup & Restore ✅ Done

Requested by the admin persona: a way to export a household's data and bring it back later (self-host migration, accidental-deletion recovery, or just peace of mind).

Landing after Phase 7 (household sharding) and the user-UUID migration changed the shape of the feature entirely: with a household already isolated to its own SQLite file and user ids collision-safe across instances, backup is literally "download that file" and restore is "upload a file back into that slot" — no bespoke JSON export/import format, no filtering logic, no cross-household leak risk to design around.

- **`GET /households/{id}/export`** — admin-only, own household only. A plain file copy would risk missing rows still sitting in the WAL sidecar (household files run in WAL mode), so this uses SQLite's online backup API to produce a point-in-time-consistent snapshot, streamed back and self-deleted after the response.
- **`POST /households/{id}/restore`** — admin-only, own household only. Validates the upload (integrity check, must already carry household-stream migration history — a blank or unrelated SQLite file is rejected, not silently accepted), upgrades it to the current schema if it's an older export, rewrites every row's `household_id` to the target household (this is what makes "restore my own backup" and "migrate this household to a new instance" the same code path), backs up the current file first unconditionally, evicts the household's LRU-registry engine before the atomic swap, and clears the stale WAL sidecars + cached balance afterward.
- **Unclaimed-stub identity model** (the piece that needed real design, captured in a project memory before implementation): a restored file can reference a user id this instance doesn't know — restore never force-creates a real account for them. It creates an `unclaimed` stub (placeholder name, unusable password) instead, which renders correctly everywhere by construction (balances, expense history) since the name-stitch pattern already reads whatever user row it finds. If that person later signs up here with the email that hashes to the same id, `POST /auth/signup` **claims** the stub in place — same row, no duplicate, every prior expense/settlement picks up their real name automatically.
- **68 tests passing** (9 new, on top of Phase 7's 59), plus a live round-trip smoke test in the Docker dev container: export → mutate → restore → confirm the mutation is gone and the exported state is back, WAL/shm sidecars and the `.pre-restore-backup` file left in the expected state, `PRAGMA integrity_check` clean.
- **Frontend built** from the design handoff (`Phone.dc.html`/`PhoneIntl.dc.html`), pixel-for-pixel where the backend could support it exactly, with two disclosed trims:
  - **Lives in Menu → Backup**, next to Language and Currency, admin-only — not on the Household screen.
  - **Export is one dark button** ("Download a copy") with a quiet last-copy line under it (session-only, resets on reload — not a tracked metric).
  - **Restore is a hold-to-confirm**, not a typed confirmation: file picker → a card showing the picked filename → hold ~600ms (+8% every 45ms) to fire, releasing early cancels instantly.
  - **File states trimmed from 4 to 3** (none/picked/rejected, dropping the mock's "older export" sage-note state) — the backend's `RestoreSummary` response has no way to signal "this was upgraded from an older schema" before/during upload, so that distinction isn't surfaced; the upgrade itself still happens correctly and silently either way.
  - **Result is a full-screen summary, not a toast:** "Back as it was." with expense/settlement/people counts and a dashed callout naming anyone unclaimed.
  - **Unclaimed stubs are tagged inline in the member list** (`GET /users` now includes them alongside approved members) — normal row, `unclaimed` tag where the role chip sits, "no account on this instance yet" in place of the email, excluded from every place that builds a list of taggable/payable people (`activeMembers()` in `app.js`).
  - Provenance line ("From X, taken Y") and per-unclaimed-person naming in the unclaimed callout are simplified versions of the mock — the frontend doesn't retain the original export's filename/timestamp once picked, and "which users are newly unclaimed" is inferred from the response count rather than tracked individually.
- **Explicitly out of scope for this pass:** a full-instance restore (every household at once) is a different, higher-privilege operation belonging to the self-hoster, not any household admin.

---

## Phase 9 — Editable Categories ✅ Done

Categories were a hard-coded list (rent, groceries, utilities, household, eating out, transport, other) since Phase 1. Every household that isn't three roommates in Tehran wants different ones — and the list was the one piece of shared vocabulary in the app users couldn't touch.

- **`Category` table lives in the household file** (`HouseholdBase`, not shared) — a deliberate call beyond the original sketch: it means a household's own categories travel with it for free on export/restore (Phase 8), the same way its expenses do, with zero extra work in either endpoint.
- **Seeded lazily on first `GET /categories`**, not at household creation or via migration backfill — same "nothing until it's actually needed" approach the household file itself already uses. Seeded in whichever user's request happens to trigger it, since language is per-user in this app's data model, not per-household (the original design note assumed a single household language, which doesn't exist here — resolved in conversation before building).
- **`Expense.category` stayed a plain string**, not a FK to `Category.id` — a rename is one bulk `UPDATE` of matching `Expense.category` rows in the same transaction, not a join everywhere a category displays.
- **Reached from where it's used:** an `edit` chip at the end of the Add Expense sheet's category row, not buried in Household admin — editing is a member action, not an admin-only one (`get_current_active_user`, same gate as logging an expense).
- **Rename cascades**, guarded against duplicates (case-insensitive); **remove is blocked by usage** (a category still on any expense can't be removed, server-side — the API is the real guard, not just a client-side toast) **and by being the last one left**; **add** rejects empty/duplicate names, auto-selects the new category on the expense being logged.
- **11 new backend tests** (68 → 79 total: categories CRUD, seeding in both languages, rename-cascade, both usage/last-category delete guards, duplicate rejection). Live-verified end to end via the API: seed → add → rename → delete.
- i18n: the 7 seeded defaults are real household data from the moment they're seeded, not a translated lookup — `CAT_LABELS` (frontend) now only translates the synthetic "Settled" history tag, nothing else; a category keeps exactly the spelling it was seeded or renamed to, in any UI language.

---

## Phase 10 — Live deployment & post-launch fixes 🔵 In progress

Phases 1–9 (plus the i18n completion pass — sign-in language switcher, Persian screen-set — shipped alongside Phase 8's frontend) took the app to feature-complete. On **2026-09-01** it went to a first real public instance at **halves.ir** (self-hosted on a VPS, single Docker container, TLS via a reverse proxy in front of port 8130). Everything before this was local / Docker-dev only.

Going live also started standard **SemVer** versioning. The app stays on the `0.x` line — `0.x` *is* the "beta" signal; `1.0.0` is reserved for a deliberate stability call. Every change ships as `0.9.z` with a `v0.9.z` tag; `beta:` commit prefix.

### Shipped (all tagged and deployed to halves.ir)

- **`0.9.0` — versioning infrastructure.** Root `VERSION` file as the single source of truth, mirrored into `app.__version__` (fed to `FastAPI(version=)` and `GET /health`), the service-worker cache name, and the PWA manifest.
- **`0.9.1` — per-household admin bootstrap.** Signup auto-approved an admin only for the *first user on the whole instance* (`is_first_user`), so on a multi-tenant instance the 2nd+ person to create their own household landed `pending` in a household with no admin — unusable, nobody could approve them. Replaced with `auth._initial_role_and_status(db, household)`: whoever founds a household (any household with no approved admin) is auto-approved as its admin; joiners of a household that already has one still wait for approval. `BOOTSTRAP_ADMIN` broadened from "first user ever" to "first user of each household". Does **not** retroactively fix households orphaned before the fix — those need a manual promote.
- **`0.9.2` — Persian amount fields only accepted one digit.** `parseAmount()` kept only `[0-9]`, but `fmt()` re-emits amounts as Persian digits under `fa` and the amount fields reformat on every keystroke — so each keystroke discarded the digits already there. Added `toLatinDigits()` (Persian U+06F0–U+06F9 + Arabic-Indic U+0660–U+0669 → ASCII).
- **`0.9.3` — real expense-date picker + record timestamp.** The "Today" pill was a hardcoded `disabled` button; the expense date was always the server's (UTC) `date.today()`, so a user east of UTC logging after midnight got yesterday's date. Now a real date control: `state.draft.date` defaults to the client's *local* today and is sent on create; backend `ExpenseCreate.date` / `SettlementCreate.date` share a `RecordDate` type that rejects dates more than a day in the future. `SettlementOut` now also returns `created_at` (ExpenseOut already did). History rows show a faint "logged {when}" line only when a record was backdated (its `created_at` local date ≠ its `date`).
- **`0.9.4` — date picker cross-browser rework.** Two problems with the 0.9.3 control: it was routed only through the delegated `input` event (date inputs don't reliably fire `input` on selection — Safari/mobile emit only `change`), and it was an `opacity:0` `<input type="date">` stretched over a `<label>` (WebKit clamps date inputs to intrinsic size, so taps on the label text missed). Now the visible pill is a plain `<button>` that calls `input.showPicker()` (fallback `.click()`) on an off-screen real input; `change` is routed through `handleFieldInput`. **Known gap:** `showPicker()` is absent on iOS < 16.4 and `.click()` is unreliable there — see issue #2.
- **Deploy tooling.** `docker-compose.yml` is now production-shaped (code runs from the built image, `restart: unless-stopped`, no `--reload`, only the `db-data` volume, `env_file: backend/.env`). Dev bind-mounts + `--reload` moved to `docker-compose.override.yml`, which `docker compose` loads automatically for local dev but a `-f docker-compose.yml` production command skips. `DEPLOY.md` covers first-run, updates, rollback, and volume backups.
- **`0.9.5` — spending breakdown.** New read-only "Breakdown" screen off the History topbar: household spend **per category** and each member's *weighted share* **per person, per category** (people totals sum to the grand total; settlements excluded), over a day-accurate date range (from/to pills reusing the 0.9.4 picker + This month / Last month / All presets, default this month). Frontend-only — pure arithmetic over the already-loaded `state.expenses`; `GET /expenses` stays unbounded, so no aggregation endpoint.

**Test count: 82** (79 after Phase 9, + 1 for the per-household admin fix, + 2 for the date/timestamp behaviour; dead test-helper workarounds were removed with the 0.9.1 fix but no test functions were dropped). Two `test_backup_restore` failures and two `test_migrations` teardown errors show up on Windows only — `PermissionError [WinError 32]` releasing a temp `.db`, an environment quirk; all green in the Linux/Docker container.

**Production hardening done at launch:** `SECRET_KEY` was still the built-in `dev-secret-key-change-me` on the live instance (forgeable auth tokens) — replaced with a real 48-byte key in `backend/.env`; existing sessions invalidated once.

### Open (tracked as GitHub issues, deferred — not blockers)

- **#2 — expense date picker may not open on iOS < 16.4.** Fix = native `<input type="date">` as the pill, or a `'showPicker' in HTMLInputElement.prototype` feature-detect branch. Android and iOS 16.4+ are expected to work but are unverified on real devices.
- **#3 — no password management.** The app has no change-password screen and no reset flow (it uses Web Push, not email, so no mail channel for a reset link). Surfaced when a real user forgot their password; stopgap is a manual `hash_password` update in the shared DB. Planned: `PATCH /users/me/password` + a Menu → Account screen, plus admin-generated reset links reusing the existing invite-token mechanism. Email-based self-service reset is out of scope (would force SMTP config on self-hosters).

---

## Deliberately deferred, not blockers

1. **Postgres migration** — `DATABASE_URL` is already a config swap, and Alembic's migrations run against Postgres the same way they do SQLite (SQLite just needs `render_as_batch` for its limited `ALTER TABLE` support, already enabled). Not needed until SQLite's single-writer model actually becomes the bottleneck.
2. **Jalali date picker** — the native `<input type="date">` picker is Gregorian even under `fa`. A calendar-aware picker is separate, larger work; the *displayed* dates elsewhere already render Jalali via `Intl`.

*Phases 1–9 originally read "planning stage, no code written yet"; those sections now reflect the actual implementation, checked against `backend/` and `frontend/`. Phase 10 tracks the live instance.*
