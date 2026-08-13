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


def test_first_user_becomes_approved_admin(client):
    resp = signup(client, "admin@example.com", household_name="Roommates")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "admin"
    assert body["status"] == "approved"
    assert body["household_id"] is not None


def test_duplicate_email_rejected(client):
    signup(client, "admin@example.com", household_name="Roommates")
    resp = signup(client, "admin@example.com", household_name="Other House")
    assert resp.status_code == 400


def test_wrong_password_rejected(client):
    signup(client, "admin@example.com", household_name="Roommates")
    resp = client.post("/auth/login", data={"username": "admin@example.com", "password": "wrong-password"})
    assert resp.status_code == 401


def test_second_user_is_pending_and_blocked_until_approved(client):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates")
    household_id = admin_resp.json()["household_id"]

    member_resp = signup(client, "member@example.com", household_id=household_id)
    assert member_resp.json()["status"] == "pending"

    member_token = login(client, "member@example.com")
    blocked = client.get("/expenses", headers=auth_headers(member_token))
    assert blocked.status_code == 403

    admin_token = login(client, "admin@example.com")
    approve = client.patch(
        f"/users/{member_resp.json()['id']}/approve",
        json={"role": "member"},
        headers=auth_headers(admin_token),
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    allowed = client.get("/expenses", headers=auth_headers(member_token))
    assert allowed.status_code == 200
    assert allowed.json() == []


def test_expense_split_and_balance_calculation(client):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates")
    household_id = admin_resp.json()["household_id"]
    admin_id = admin_resp.json()["id"]

    member_resp = signup(client, "member@example.com", household_id=household_id)
    member_id = member_resp.json()["id"]

    admin_token = login(client, "admin@example.com")
    client.patch(
        f"/users/{member_id}/approve", json={"role": "member"}, headers=auth_headers(admin_token)
    )
    member_token = login(client, "member@example.com")

    # Admin pays $100 for groceries, split evenly between both of them.
    expense = client.post(
        "/expenses",
        json={
            "amount": 100.0,
            "description": "Groceries",
            "category": "groceries",
            "participant_ids": [admin_id, member_id],
        },
        headers=auth_headers(admin_token),
    )
    assert expense.status_code == 201, expense.text

    balances = client.get("/balances", headers=auth_headers(member_token)).json()
    net_by_user = {b["user_id"]: b["net"] for b in balances["balances"]}
    assert net_by_user[admin_id] == 50.0
    assert net_by_user[member_id] == -50.0
    assert balances["settlements_to_make"] == [
        {
            "from_user_id": member_id,
            "from_name": "Test User",
            "to_user_id": admin_id,
            "to_name": "Test User",
            "amount": 50.0,
        }
    ]

    # Member settles up; balance should zero out.
    settle = client.post(
        "/settlements",
        json={"to_user_id": admin_id, "amount": 50.0},
        headers=auth_headers(member_token),
    )
    assert settle.status_code == 201, settle.text

    balances_after = client.get("/balances", headers=auth_headers(member_token)).json()
    assert balances_after["balances"] == []
    assert balances_after["settlements_to_make"] == []


def test_expense_only_splits_among_tagged_participants(client):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates")
    household_id = admin_resp.json()["household_id"]
    admin_id = admin_resp.json()["id"]

    member_resp = signup(client, "member@example.com", household_id=household_id)
    member_id = member_resp.json()["id"]
    admin_token = login(client, "admin@example.com")
    client.patch(f"/users/{member_id}/approve", json={"role": "member"}, headers=auth_headers(admin_token))

    # Admin buys something just for themselves -- member is untouched.
    client.post(
        "/expenses",
        json={"amount": 30.0, "description": "Solo snack", "participant_ids": [admin_id]},
        headers=auth_headers(admin_token),
    )

    balances = client.get("/balances", headers=auth_headers(admin_token)).json()
    assert balances["balances"] == []
    assert balances["settlements_to_make"] == []


def test_settlement_can_be_logged_by_either_party(client):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates")
    household_id = admin_resp.json()["household_id"]
    admin_id = admin_resp.json()["id"]

    member_resp = signup(client, "member@example.com", household_id=household_id)
    member_id = member_resp.json()["id"]
    admin_token = login(client, "admin@example.com")
    client.patch(f"/users/{member_id}/approve", json={"role": "member"}, headers=auth_headers(admin_token))

    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [admin_id, member_id]},
        headers=auth_headers(admin_token),
    )

    # Admin is owed by member; admin (the recipient) logs that member paid them back.
    settle = client.post(
        "/settlements",
        json={"from_user_id": member_id, "to_user_id": admin_id, "amount": 50.0},
        headers=auth_headers(admin_token),
    )
    assert settle.status_code == 201, settle.text
    assert settle.json()["from_user_id"] == member_id
    assert settle.json()["to_user_id"] == admin_id

    balances_after = client.get("/balances", headers=auth_headers(admin_token)).json()
    assert balances_after["balances"] == []

    # A third party can't log a settlement between two other people.
    outsider_resp = signup(client, "outsider@example.com", household_id=household_id)
    outsider_id = outsider_resp.json()["id"]
    client.patch(f"/users/{outsider_id}/approve", json={"role": "member"}, headers=auth_headers(admin_token))
    outsider_token = login(client, "outsider@example.com")
    forbidden = client.post(
        "/settlements",
        json={"from_user_id": member_id, "to_user_id": admin_id, "amount": 10.0},
        headers=auth_headers(outsider_token),
    )
    assert forbidden.status_code == 403
