"""Roadmap Phase 9: household-owned expense categories. Categories are
seeded lazily the first time GET /categories is read for a household
(app/routers/categories.py::_seed_if_empty) -- there's no migration-time
backfill, mirroring how the household file itself is only ever created on
first per-household access."""


def signup(client, email, household_name=None, household_id=None, name="Test User", password="password123", language="en"):
    payload = {"email": email, "password": password, "name": name, "language": language}
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


def setup_household(client, household_name="Roommates", language="en"):
    admin = signup(client, "admin@example.com", household_name=household_name, language=language)
    token = login(client, "admin@example.com")
    return {"household_id": admin["household_id"], "admin_id": admin["id"], "token": token}


def test_categories_seed_on_first_read(client):
    hh = setup_household(client)
    resp = client.get("/categories", headers=auth_headers(hh["token"]))
    assert resp.status_code == 200, resp.text
    names = [c["name"] for c in resp.json()]
    assert names == ["Rent", "Groceries", "Utilities", "Household", "Eating out", "Transport", "Other"]
    assert all(c["usage"] == 0 for c in resp.json())


def test_categories_seed_in_the_requesting_users_language(client):
    hh = setup_household(client, language="fa")
    resp = client.get("/categories", headers=auth_headers(hh["token"]))
    names = [c["name"] for c in resp.json()]
    assert names == ["اجاره", "خواروبار", "آب و برق", "خانه", "رستوران", "حمل‌ونقل", "سایر"]


def test_seeding_only_happens_once(client):
    hh = setup_household(client)
    client.get("/categories", headers=auth_headers(hh["token"]))
    resp = client.post("/categories", json={"name": "Fun money"}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 201, resp.text
    resp = client.get("/categories", headers=auth_headers(hh["token"]))
    assert len(resp.json()) == 8  # 7 defaults + the one just added, not re-seeded to 14


def test_add_category(client):
    hh = setup_household(client)
    resp = client.post("/categories", json={"name": "Fun money"}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Fun money"
    assert resp.json()["usage"] == 0


def test_add_category_rejects_case_insensitive_duplicate(client):
    hh = setup_household(client)
    client.get("/categories", headers=auth_headers(hh["token"]))  # seed
    resp = client.post("/categories", json={"name": "rent"}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_add_category_rejects_empty_name(client):
    hh = setup_household(client)
    resp = client.post("/categories", json={"name": "   "}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 400
    assert "Name the category" in resp.json()["detail"]


def test_rename_category_cascades_to_expenses(client):
    hh = setup_household(client)
    client.get("/categories", headers=auth_headers(hh["token"]))  # seed
    expense = client.post(
        "/expenses",
        json={"amount": 50.0, "description": "Water bill", "category": "Utilities", "participant_ids": [hh["admin_id"]]},
        headers=auth_headers(hh["token"]),
    )
    assert expense.status_code == 201, expense.text
    expense_id = expense.json()["id"]
    categories = client.get("/categories", headers=auth_headers(hh["token"])).json()
    utilities = next(c for c in categories if c["name"] == "Utilities")
    assert utilities["usage"] == 1

    resp = client.patch(f"/categories/{utilities['id']}", json={"name": "Bills"}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Bills"
    assert resp.json()["usage"] == 1

    updated_expense = client.get(f"/expenses/{expense_id}", headers=auth_headers(hh["token"])).json()
    assert updated_expense["category"] == "Bills"


def test_rename_category_rejects_duplicate(client):
    hh = setup_household(client)
    categories = client.get("/categories", headers=auth_headers(hh["token"])).json()
    rent = next(c for c in categories if c["name"] == "Rent")
    resp = client.patch(f"/categories/{rent['id']}", json={"name": "groceries"}, headers=auth_headers(hh["token"]))
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_delete_category_blocked_when_in_use(client):
    hh = setup_household(client)
    client.get("/categories", headers=auth_headers(hh["token"]))  # seed
    client.post(
        "/expenses",
        json={"amount": 50.0, "description": "Water bill", "category": "Utilities", "participant_ids": [hh["admin_id"]]},
        headers=auth_headers(hh["token"]),
    )
    categories = client.get("/categories", headers=auth_headers(hh["token"])).json()
    utilities = next(c for c in categories if c["name"] == "Utilities")

    resp = client.delete(f"/categories/{utilities['id']}", headers=auth_headers(hh["token"]))
    assert resp.status_code == 400
    assert "still on 1 expense" in resp.json()["detail"]


def test_delete_category_succeeds_when_unused(client):
    hh = setup_household(client)
    categories = client.get("/categories", headers=auth_headers(hh["token"])).json()
    other = next(c for c in categories if c["name"] == "Other")

    resp = client.delete(f"/categories/{other['id']}", headers=auth_headers(hh["token"]))
    assert resp.status_code == 204

    remaining = client.get("/categories", headers=auth_headers(hh["token"])).json()
    assert "Other" not in [c["name"] for c in remaining]


def test_delete_category_blocked_as_last_one(client):
    hh = setup_household(client)
    categories = client.get("/categories", headers=auth_headers(hh["token"])).json()
    for c in categories[:-1]:
        resp = client.delete(f"/categories/{c['id']}", headers=auth_headers(hh["token"]))
        assert resp.status_code == 204

    last = client.get("/categories", headers=auth_headers(hh["token"])).json()
    assert len(last) == 1

    resp = client.delete(f"/categories/{last[0]['id']}", headers=auth_headers(hh["token"]))
    assert resp.status_code == 400
    assert "Keep at least one" in resp.json()["detail"]
