# Shared Expense Tracker — Roadmap

A self-hosted, open-source web app for splitting and tracking shared expenses between roommates (or any group). Built as a web app instead of native apps to avoid iOS/Android fragmentation.

**Design principle (applies to every phase):** keep it dead simple — minimal taps to log an expense, balance front and center, no clutter. This is the whole reason the project exists (existing apps/services felt too complicated).

---

## Phase 1 — Data Model

Core object: **Expense Record**

- `amount`
- `payer` (who paid)
- `description`
- `date`
- `category` (rent, groceries, utilities, etc.)
- `participants` — list of users this expense is shared between

Note: not a simple "shared vs. personal" flag. Each expense has its **own participant list**, so costs only split among whoever is actually tagged on that specific item (e.g. milk bought for the whole house vs. milk bought for just two people).

---

## Phase 2 — Backend

- **Hosting model:** Self-hosted (not managed/serverless) — important since the goal is to open-source this for others to run themselves.
- **Language/Framework:** Python — Flask or FastAPI as contenders (Django considered but too heavyweight for this scope).
- **Database:** SQLite for now (single-file, zero-config, easy for others to self-host). Can migrate to Postgres later if usage grows (more users, heavier concurrent writes) — ORM makes that swap relatively painless.
- **Caching:** Not needed now, but noted as an independent upgrade path later (e.g. Redis or in-memory) if reads become a bottleneck — separate concern from which database is used.
- **Auth:**
  - Email + password, hashed storage.
  - New sign-ups are **pending** until admin approves them.
  - **Role-based access** — admin role has full control; regular user roles are scoped (e.g. to their own household), rather than blanket full access after approval.
- **Domain:** User will provide their own domain; independent of hosting/backend choice — works with any of the options.

---

## Phase 3 — Core Frontend

Screens:
- Login page
- Dashboard — current balance (who owes who, how much) front and center
- Add expense form
- History/list view of past expenses

Guiding constraint: launch-and-go simplicity, not feature-heavy.

---

## Phase 4 — Calculation Logic

- For each expense, split the cost **only among its tagged participants** (not a blanket shared pool).
- Sum every user's net position across all expenses to determine final balances (who owes who, how much).
- **Settle-up action** to log real-world repayment and zero out the balance.
- **Future consideration (3+ people):** if the group grows beyond two, the math is no longer a simple two-way difference — will need a more general debt-simplification algorithm (net positive/negative per person, minimum number of payments to settle everyone).

---

## Phase 5 — Deployment & Polish

- Packaged for self-hosting — Docker container as the current pick (guarantees consistency across systems), though a plain `requirements.txt` + setup instructions was discussed as a lighter-weight alternative.
- PWA installability (installable on phones like a native app).
- Possible future notifications (new expense added, approval requests, etc.)

---

*Status: planning stage — roadmap only, no code written yet. Phases are expected to flex as implementation starts.*
