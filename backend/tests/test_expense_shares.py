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


def test_expense_created_via_api_defaults_to_equal_shares(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    expense = client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    shares = {s["user_id"]: s["share"] for s in expense.json()["shares"]}
    assert shares == {hh["admin_id"]: 1.0, member["id"]: 1.0}


def test_admin_can_reweight_an_expense_split(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    expense = client.post(
        "/expenses",
        json={"amount": 90.0, "description": "Rent top-up", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    # Admin gets the bigger room -- weight 2 vs 1, so they carry 2/3 of it.
    edit = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": hh["admin_id"], "share": 2}, {"user_id": member["id"], "share": 1}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert edit.status_code == 200, edit.text
    shares = {s["user_id"]: s["share"] for s in edit.json()["shares"]}
    assert shares == {hh["admin_id"]: 2.0, member["id"]: 1.0}

    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net_by_user = {b["user_id"]: b["net"] for b in balances["balances"]}
    # Admin paid 90, owes 2/3 * 90 = 60 -> net +30. Member owes 1/3 * 90 = 30 -> net -30.
    assert net_by_user[hh["admin_id"]] == 30.0
    assert net_by_user[member["id"]] == -30.0


def test_member_cannot_reweight_an_expense_split(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    expense = client.post(
        "/expenses",
        json={"amount": 50.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    forbidden = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": hh["admin_id"], "share": 1}, {"user_id": member["id"], "share": 3}]},
        headers=auth_headers(member["token"]),
    )
    assert forbidden.status_code == 403


def test_reweighting_can_add_or_drop_participants(client):
    hh = setup_household(client, n_members=2)
    m0, m1 = hh["members"]

    expense = client.post(
        "/expenses",
        json={"amount": 60.0, "description": "Pizza", "participant_ids": [hh["admin_id"], m0["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    # Swap m0 out for m1, drop the admin from the split entirely.
    edit = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": m1["id"], "share": 1}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert edit.status_code == 200, edit.text
    shares = {s["user_id"]: s["share"] for s in edit.json()["shares"]}
    assert shares == {m1["id"]: 1.0}


def test_reweighting_rejects_duplicate_participant(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    resp = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": member["id"], "share": 1}, {"user_id": member["id"], "share": 2}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert resp.status_code == 400


def test_reweighting_rejects_user_outside_household(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    outsider_resp = signup(client, "outsider@example.com", household_name="Other House")
    outsider_id = outsider_resp.json()["id"]

    resp = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": outsider_id, "share": 1}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert resp.status_code == 400


def test_reweighting_rejects_nonpositive_share(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    expense_id = expense.json()["id"]

    resp = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": member["id"], "share": 0}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert resp.status_code == 422
