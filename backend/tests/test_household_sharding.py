"""Roadmap Phase 7: each household's Expense/ExpenseParticipant/Settlement/
BalanceCache now live in their own SQLite file instead of one shared file.
These tests guard the two things that actually matter about that split:
genuine isolation (household A's file never holds household B's data,
verified by opening B's file directly -- not an indirect proxy), and that
the manual User-name stitch (app/routers/expenses.py::_stitch_expense_users)
didn't regress the "always shows the current name" behavior the removed
ORM relationship used to give for free."""

from app.database import SessionLocal
from app.household_db import household_db_path, household_session
from app.models import Expense, User


def signup(client, email, household_name=None, name="Test User", password="password123"):
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": password, "name": name, "household_name": household_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(client, email, password="password123"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _new_household(client, email, household_name):
    """Founding a household makes you its admin (auth._initial_role_and_status),
    so every household in a test run gets its own approved admin with no
    out-of-band promotion."""
    created = signup(client, email, household_name=household_name)
    assert created["status"] == "approved" and created["role"] == "admin"
    return {
        "household_id": created["household_id"],
        "user_id": created["id"],
        "token": login(client, email),
    }


def test_each_household_gets_its_own_file_on_disk(client):
    a = _new_household(client, "a-admin@example.com", "Household A")
    b = _new_household(client, "b-admin@example.com", "Household B")

    # A household's file is created lazily on first per-household access --
    # here, logging its first expense.
    client.post(
        "/expenses",
        json={"amount": 10.0, "description": "A's thing", "participant_ids": [a["user_id"]]},
        headers=auth_headers(a["token"]),
    )
    client.post(
        "/expenses",
        json={"amount": 20.0, "description": "B's thing", "participant_ids": [b["user_id"]]},
        headers=auth_headers(b["token"]),
    )

    path_a = household_db_path(a["household_id"])
    path_b = household_db_path(b["household_id"])
    assert path_a.exists()
    assert path_b.exists()
    assert path_a != path_b


def test_households_are_genuinely_isolated(client):
    a = _new_household(client, "a-admin@example.com", "Household A")
    b = _new_household(client, "b-admin@example.com", "Household B")

    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "A's private expense", "participant_ids": [a["user_id"]]},
        headers=auth_headers(a["token"]),
    )
    client.post(
        "/expenses",
        json={"amount": 200.0, "description": "B's private expense", "participant_ids": [b["user_id"]]},
        headers=auth_headers(b["token"]),
    )

    # Open each household's file directly -- bypassing the app/API
    # entirely -- and confirm it holds only its own rows, not a proxy check
    # like a row count or mtime.
    db_b = household_session(b["household_id"])
    try:
        expenses_in_b = db_b.query(Expense).all()
        assert len(expenses_in_b) == 1
        assert expenses_in_b[0].household_id == b["household_id"]
        assert expenses_in_b[0].description == "B's private expense"
    finally:
        db_b.close()

    db_a = household_session(a["household_id"])
    try:
        expenses_in_a = db_a.query(Expense).all()
        assert len(expenses_in_a) == 1
        assert expenses_in_a[0].household_id == a["household_id"]
        assert expenses_in_a[0].description == "A's private expense"
    finally:
        db_a.close()


def test_renamed_user_shows_current_name_on_existing_expenses(client):
    hh = _new_household(client, "admin@example.com", "Roommates")
    client.post(
        "/expenses",
        json={"amount": 50.0, "description": "Groceries", "participant_ids": [hh["user_id"]]},
        headers=auth_headers(hh["token"]),
    )

    # No rename endpoint exists yet -- mutate the shared User row directly
    # to prove the stitch (app/routers/expenses.py::_stitch_expense_users)
    # reads the name live from the shared file at request time, rather than
    # having captured/duplicated it into the household file at write time.
    db = SessionLocal()
    try:
        user = db.get(User, hh["user_id"])
        user.name = "New Name"
        db.commit()
    finally:
        db.close()

    listed = client.get("/expenses", headers=auth_headers(hh["token"])).json()
    assert len(listed) == 1
    assert listed[0]["payer"]["name"] == "New Name"
    assert listed[0]["created_by"]["name"] == "New Name"
    assert listed[0]["participants"][0]["name"] == "New Name"
