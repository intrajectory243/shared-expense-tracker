"""Roadmap Phase 8: GET /households/{id}/export and POST .../restore. A
household's Expense/ExpenseParticipant/Settlement/BalanceCache data is a
raw SQLite file (app/household_db.py) -- export streams a WAL-safe
snapshot of it, restore swaps that file back in. The part that isn't just
"copy a file" is user identity: a restored file can reference a user id
this instance doesn't know (an "unclaimed" stub gets created for it,
app/routers/households.py::restore_household), and a later signup with
that person's real email claims the stub in place instead of duplicating
it (app/routers/auth.py::signup)."""

import io
import sqlite3

from app.database import SessionLocal
from app.models import User, UserRole, UserStatus

BACKUP_MEDIA_TYPE = "application/octet-stream"


def signup(client, email, household_name=None, household_id=None, name="Test User", password="password123"):
    payload = {"email": email, "password": password, "name": name}
    if household_name:
        payload["household_name"] = household_name
    if household_id:
        payload["household_id"] = household_id
    resp = client.post("/auth/signup", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(client, email, password="password123"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def setup_household(client, n_members=1, household_name="Roommates", admin_email="admin@example.com"):
    admin = signup(client, admin_email, household_name=household_name, name="Admin One")
    # Only the instance's very first-ever signup auto-bootstraps as an
    # approved admin (auth.py's is_first_user check is instance-wide, not
    # per-household) -- every household after the first one in a test run
    # would otherwise land 'pending' with no admin of its own to approve
    # them. Promote directly, same as a real deploy needs a human to do
    # out-of-band for a second from-scratch household.
    db = SessionLocal()
    try:
        user = db.get(User, admin["id"])
        if user.status != UserStatus.approved:
            user.status = UserStatus.approved
            user.role = UserRole.admin
            db.commit()
    finally:
        db.close()
    admin_token = login(client, admin_email)

    members = []
    for i in range(n_members):
        email = f"{household_name.lower().replace(' ', '-')}-member{i}@example.com"
        resp = signup(client, email, household_id=admin["household_id"], name=f"Member {i}")
        client.patch(f"/users/{resp['id']}/approve", json={"role": "member"}, headers=auth_headers(admin_token))
        members.append({"id": resp["id"], "email": email, "token": login(client, email)})

    return {"household_id": admin["household_id"], "admin_id": admin["id"], "admin_token": admin_token, "members": members}


def add_expense(client, token, amount, description, participant_ids):
    resp = client.post(
        "/expenses",
        json={"amount": amount, "description": description, "participant_ids": participant_ids},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def export_bytes(client, household_id, token):
    resp = client.get(f"/households/{household_id}/export", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == BACKUP_MEDIA_TYPE
    return resp.content


def restore(client, household_id, token, data):
    return client.post(
        f"/households/{household_id}/restore",
        headers=auth_headers(token),
        files={"file": ("backup.db", io.BytesIO(data), BACKUP_MEDIA_TYPE)},
    )


def test_export_snapshot_matches_live_data(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    add_expense(client, hh["admin_token"], 100.0, "Groceries", [hh["admin_id"], member["id"]])

    data = export_bytes(client, hh["household_id"], hh["admin_token"])

    # A real file is easiest to inspect directly with sqlite3.
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkstemp(suffix=".db")[1])
    tmp.write_bytes(data)
    try:
        conn = sqlite3.connect(tmp)
        rows = conn.execute("SELECT description, amount, household_id FROM expenses").fetchall()
        conn.close()
    finally:
        tmp.unlink()

    assert rows == [("Groceries", 100.0, hh["household_id"])]


def test_restore_round_trip_replaces_current_data(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    add_expense(client, hh["admin_token"], 100.0, "Groceries", [hh["admin_id"], member["id"]])
    snapshot = export_bytes(client, hh["household_id"], hh["admin_token"])

    # Diverge from the snapshot after taking it.
    add_expense(client, hh["admin_token"], 999.0, "Should disappear after restore", [hh["admin_id"]])
    expenses_before_restore = client.get("/expenses", headers=auth_headers(hh["admin_token"])).json()
    assert len(expenses_before_restore) == 2

    resp = restore(client, hh["household_id"], hh["admin_token"], snapshot)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"expenses_restored": 1, "settlements_restored": 0, "unclaimed_users_created": 0}

    expenses_after = client.get("/expenses", headers=auth_headers(hh["admin_token"])).json()
    assert len(expenses_after) == 1
    assert expenses_after[0]["description"] == "Groceries"

    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net_by_user = {b["user_id"]: b["net"] for b in balances["balances"]}
    assert net_by_user[hh["admin_id"]] == 50.0


def test_restore_rewrites_household_id_to_the_target_household(client):
    hh_a = setup_household(client, n_members=1, household_name="House A", admin_email="admin-a@example.com")
    member_a = hh_a["members"][0]
    add_expense(client, hh_a["admin_token"], 60.0, "House A groceries", [hh_a["admin_id"], member_a["id"]])
    snapshot_a = export_bytes(client, hh_a["household_id"], hh_a["admin_token"])

    hh_b = setup_household(client, n_members=0, household_name="House B", admin_email="admin-b@example.com")
    resp = restore(client, hh_b["household_id"], hh_b["admin_token"], snapshot_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["expenses_restored"] == 1

    expenses_b = client.get("/expenses", headers=auth_headers(hh_b["admin_token"])).json()
    assert len(expenses_b) == 1
    assert expenses_b[0]["description"] == "House A groceries"

    # household A's own file is untouched.
    expenses_a = client.get("/expenses", headers=auth_headers(hh_a["admin_token"])).json()
    assert len(expenses_a) == 1


def test_restore_creates_unclaimed_stub_for_a_user_this_instance_no_longer_knows(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    add_expense(client, hh["admin_token"], 80.0, "Rent", [hh["admin_id"], member["id"]])
    snapshot = export_bytes(client, hh["household_id"], hh["admin_token"])

    # Simulate the member's account no longer existing on this instance
    # (e.g. a since-purged account, or restoring onto a fresh instance that
    # never had them) by deleting their row directly -- no API for a hard
    # delete of an already-approved member exists, which is itself fine;
    # this is standing in for "restoring onto a machine that never had them".
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == member["id"]).delete()
        db.commit()
    finally:
        db.close()

    resp = restore(client, hh["household_id"], hh["admin_token"], snapshot)
    assert resp.status_code == 200, resp.text
    assert resp.json()["unclaimed_users_created"] == 1

    db = SessionLocal()
    try:
        stub = db.get(User, member["id"])
        assert stub is not None
        assert stub.status == UserStatus.unclaimed
        assert stub.name == "Unknown member"
    finally:
        db.close()

    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    names = {b["user_id"]: b["name"] for b in balances["balances"]}
    assert names[member["id"]] == "Unknown member"

    expenses = client.get("/expenses", headers=auth_headers(hh["admin_token"])).json()
    participant_names = {p["id"]: p["name"] for p in expenses[0]["participants"]}
    assert participant_names[member["id"]] == "Unknown member"

    # GET /users merges unclaimed stubs into the normal member list (the
    # frontend renders them as a member row with a gap, not a separate
    # section) -- see app/routers/users.py::list_household_users.
    users = client.get("/users", headers=auth_headers(hh["admin_token"])).json()
    stub_out = next(u for u in users if u["id"] == member["id"])
    assert stub_out["status"] == "unclaimed"


def test_signup_claims_an_unclaimed_stub_instead_of_duplicating_it(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    add_expense(client, hh["admin_token"], 80.0, "Rent", [hh["admin_id"], member["id"]])
    snapshot = export_bytes(client, hh["household_id"], hh["admin_token"])

    db = SessionLocal()
    try:
        db.query(User).filter(User.id == member["id"]).delete()
        db.commit()
    finally:
        db.close()

    restore(client, hh["household_id"], hh["admin_token"], snapshot)

    # The real person signs back up with their original email -- same
    # uuid5(email) id as the stub restore just created.
    claimed = signup(client, member["email"], household_id=hh["household_id"], name="Real Member Name")
    assert claimed["id"] == member["id"]
    assert claimed["status"] == "pending"  # not the first user on this instance

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.id == member["id"]).count() == 1  # no duplicate row
    finally:
        db.close()

    client.patch(
        f"/users/{member['id']}/approve", json={"role": "member"}, headers=auth_headers(hh["admin_token"])
    )
    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    names = {b["user_id"]: b["name"] for b in balances["balances"]}
    assert names[member["id"]] == "Real Member Name"


def test_restore_rejects_a_non_sqlite_upload(client):
    hh = setup_household(client, n_members=0)
    resp = restore(client, hh["household_id"], hh["admin_token"], b"not a sqlite file at all")
    assert resp.status_code == 400


def test_restore_rejects_a_blank_sqlite_file_with_no_migration_history(client):
    import tempfile
    from pathlib import Path

    hh = setup_household(client, n_members=0)
    tmp = Path(tempfile.mkstemp(suffix=".db")[1])
    conn = sqlite3.connect(tmp)
    conn.close()
    data = tmp.read_bytes()
    tmp.unlink()

    resp = restore(client, hh["household_id"], hh["admin_token"], data)
    assert resp.status_code == 400


def test_export_requires_admin_of_that_household(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    resp = client.get(f"/households/{hh['household_id']}/export", headers=auth_headers(member["token"]))
    assert resp.status_code == 403


def test_export_and_restore_are_scoped_to_your_own_household(client):
    hh_a = setup_household(client, n_members=0, household_name="House A", admin_email="admin-a@example.com")
    hh_b = setup_household(client, n_members=0, household_name="House B", admin_email="admin-b@example.com")

    resp = client.get(f"/households/{hh_b['household_id']}/export", headers=auth_headers(hh_a["admin_token"]))
    assert resp.status_code == 403

    resp = restore(client, hh_b["household_id"], hh_a["admin_token"], b"irrelevant")
    assert resp.status_code == 403
