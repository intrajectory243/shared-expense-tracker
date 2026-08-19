def signup(client, email, household_name=None, household_id=None, name="Test User", password="password123", **extra):
    payload = {"email": email, "password": password, "name": name, **extra}
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


# ---- Signup defaults ----

def test_signup_defaults_to_english_when_language_omitted(client):
    resp = signup(client, "admin@example.com", household_name="Roommates")
    assert resp.status_code == 201, resp.text
    assert resp.json()["language"] == "en"


def test_signup_accepts_explicit_language(client):
    resp = signup(client, "admin@example.com", household_name="Roommates", language="fa")
    assert resp.status_code == 201, resp.text
    assert resp.json()["language"] == "fa"


def test_new_household_defaults_to_toman_when_currency_omitted(client):
    signup(client, "admin@example.com", household_name="Roommates")
    admin_token = login(client, "admin@example.com")
    households = client.get("/households", headers=auth_headers(admin_token)).json()
    assert households[0]["currency"] == "toman"


def test_new_household_accepts_explicit_currency(client):
    signup(client, "admin@example.com", household_name="Roommates", household_currency="usd")
    households = client.get("/households").json()
    assert households[0]["currency"] == "usd"


def test_joining_an_existing_household_ignores_household_currency(client):
    """household_currency is only meaningful when creating a household -- a
    joiner inherits whatever the household is already set to, not their own
    (irrelevant, ignored) preference."""
    admin_resp = signup(client, "admin@example.com", household_name="Roommates", household_currency="usd")
    household_id = admin_resp.json()["household_id"]

    signup(client, "member@example.com", household_id=household_id, household_currency="eur")
    households = client.get("/households").json()
    assert households[0]["currency"] == "usd"


# ---- Self-service language ----

def test_pending_user_can_change_own_language(client):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates")
    household_id = admin_resp.json()["household_id"]

    # A non-bootstrap signup into an existing household lands as pending.
    member_resp = signup(client, "member@example.com", household_id=household_id)
    assert member_resp.json()["status"] == "pending"
    member_token = login(client, "member@example.com")

    resp = client.patch("/users/me/language", json={"language": "fa"}, headers=auth_headers(member_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["language"] == "fa"
    assert resp.json()["status"] == "pending"


def test_approved_user_can_change_own_language(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    resp = client.patch("/users/me/language", json={"language": "fa"}, headers=auth_headers(member["token"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["language"] == "fa"


# ---- Household currency ----

def test_household_currency_change_is_admin_only(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    forbidden = client.patch(
        f"/households/{hh['household_id']}", json={"currency": "usd"}, headers=auth_headers(member["token"])
    )
    assert forbidden.status_code == 403

    ok = client.patch(
        f"/households/{hh['household_id']}", json={"currency": "usd"}, headers=auth_headers(hh["admin_token"])
    )
    assert ok.status_code == 200
    assert ok.json()["currency"] == "usd"


def test_currency_only_patch_leaves_name_unchanged(client):
    hh = setup_household(client, n_members=0, household_name="Original Name")

    resp = client.patch(
        f"/households/{hh['household_id']}", json={"currency": "eur"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Original Name"
    assert resp.json()["currency"] == "eur"


def test_name_only_patch_leaves_currency_unchanged(client):
    hh = setup_household(client, n_members=0)

    client.patch(
        f"/households/{hh['household_id']}", json={"currency": "aed"}, headers=auth_headers(hh["admin_token"])
    )
    resp = client.patch(
        f"/households/{hh['household_id']}", json={"name": "Renamed"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["currency"] == "aed"
