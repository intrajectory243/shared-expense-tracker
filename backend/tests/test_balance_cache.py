from app.database import SessionLocal
from app.models import BalanceCache


def signup(client, email, household_name=None, household_id=None, name="Test User", password="password123"):
    payload = {"email": email, "password": password, "name": name}
    if household_name:
        payload["household_name"] = household_name
    if household_id:
        payload["household_id"] = household_id
    return client.post("/auth/signup", json=payload)


def login(client, email, password="password123"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def setup_household(client, n_members=1, household_name="Roommates"):
    admin_resp = signup(client, "admin@example.com", household_name=household_name, name="Admin One")
    household_id = admin_resp.json()["household_id"]
    admin_id = admin_resp.json()["id"]
    admin_token = login(client, "admin@example.com")

    members = []
    for i in range(n_members):
        email = f"member{i}@example.com"
        resp = signup(client, email, household_id=household_id, name=f"Member {i}")
        member_id = resp.json()["id"]
        client.patch(f"/users/{member_id}/approve", json={"role": "member"}, headers=auth_headers(admin_token))
        members.append({"id": member_id, "email": email, "token": login(client, email)})

    return {"household_id": household_id, "admin_id": admin_id, "admin_token": admin_token, "members": members}


def cache_rows(household_id):
    db = SessionLocal()
    try:
        return db.query(BalanceCache).filter(BalanceCache.household_id == household_id).all()
    finally:
        db.close()


def test_balance_read_populates_a_cache_row(client):
    hh = setup_household(client, n_members=1)
    assert cache_rows(hh["household_id"]) == []

    resp = client.get("/balances", headers=auth_headers(hh["admin_token"]))
    assert resp.status_code == 200

    rows = cache_rows(hh["household_id"])
    assert len(rows) == 1


def test_cache_hit_matches_what_a_fresh_read_would_return(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )

    first = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    assert len(cache_rows(hh["household_id"])) == 1
    second = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()  # served from cache
    assert first == second


def test_new_expense_invalidates_cache(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    empty = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    assert empty["balances"] == []

    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    # The write must not leave a stale (pre-expense) cache row behind.
    after = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net_by_user = {b["user_id"]: b["net"] for b in after["balances"]}
    assert net_by_user[hh["admin_id"]] == 50.0
    assert net_by_user[member["id"]] == -50.0


def test_share_edit_invalidates_cache(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense = client.post(
        "/expenses",
        json={"amount": 90.0, "description": "Rent top-up", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    equal_split = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net = {b["user_id"]: b["net"] for b in equal_split["balances"]}
    assert net[hh["admin_id"]] == 45.0

    client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": hh["admin_id"], "share": 2}, {"user_id": member["id"], "share": 1}]},
        headers=auth_headers(hh["admin_token"]),
    )

    reweighted = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net_after = {b["user_id"]: b["net"] for b in reweighted["balances"]}
    assert net_after[hh["admin_id"]] == 30.0
    assert net_after[member["id"]] == -30.0


def test_deleting_an_expense_invalidates_cache(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense = client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    with_expense = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    assert with_expense["balances"] != []

    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))

    after_delete = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    assert after_delete["balances"] == []


def test_settlement_invalidates_cache(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    client.get("/balances", headers=auth_headers(hh["admin_token"]))  # populate cache with the pre-settlement state

    client.post(
        "/settlements",
        json={"to_user_id": hh["admin_id"], "amount": 50.0},
        headers=auth_headers(hh["members"][0]["token"]),
    )

    after_settle = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    assert after_settle["balances"] == []
