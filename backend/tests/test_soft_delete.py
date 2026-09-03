"""Soft-delete + trash + opportunistic purge for expenses and settlements.

A household admin (or the payer / a settlement party) deletes an entry; it
lingers in the trash, out of history and the balance math, until an admin
restores it or an opportunistic purge removes it once it's past the
retention window.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.household_db import household_session
from app.models import Expense, Settlement


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


def setup_household(client, n_members=1):
    admin_resp = signup(client, "admin@example.com", household_name="Roommates", name="Admin One")
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


def make_expense(client, hh, amount=100.0, token=None):
    member = hh["members"][0]
    resp = client.post(
        "/expenses",
        json={"amount": amount, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(token or hh["admin_token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def net_by_user(client, hh):
    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    return {b["user_id"]: b["net"] for b in balances["balances"]}


def age_trash(household_id, days):
    """Backdate every trashed row's deleted_at so an opportunistic purge sees it as expired."""
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    db = household_session(household_id)
    try:
        for model in (Expense, Settlement):
            for row in db.query(model).filter(model.deleted_at.is_not(None)).all():
                row.deleted_at = old
        db.commit()
    finally:
        db.close()


def test_soft_deleted_expense_drops_out_of_balances_and_history(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh, amount=100.0)
    assert net_by_user(client, hh)[hh["admin_id"]] == 50.0

    resp = client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))
    assert resp.status_code == 204

    assert net_by_user(client, hh) == {}
    live = client.get("/expenses", headers=auth_headers(hh["admin_token"])).json()
    assert live == []


def test_admin_sees_trash_only_with_include_deleted(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh)
    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))

    trash = client.get("/expenses?include_deleted=true", headers=auth_headers(hh["admin_token"])).json()
    assert [e["id"] for e in trash] == [expense_id]
    assert trash[0]["deleted_at"] is not None
    assert trash[0]["deleted_by_id"] == hh["admin_id"]

    # A member can't see the trash even by asking.
    member_trash = client.get(
        "/expenses?include_deleted=true", headers=auth_headers(hh["members"][0]["token"])
    ).json()
    assert member_trash == []


def test_restore_brings_the_expense_back_exactly(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh, amount=100.0)
    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))
    assert net_by_user(client, hh) == {}

    resp = client.post(f"/expenses/{expense_id}/restore", headers=auth_headers(hh["admin_token"]))
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is None
    assert net_by_user(client, hh)[hh["admin_id"]] == 50.0


def test_only_payer_or_admin_can_delete_an_expense(client):
    hh = setup_household(client, n_members=2)
    other = hh["members"][1]
    # admin is the payer here; a non-payer, non-admin member can't delete it
    expense_id = make_expense(client, hh)
    resp = client.delete(f"/expenses/{expense_id}", headers=auth_headers(other["token"]))
    assert resp.status_code == 403
    assert net_by_user(client, hh)[hh["admin_id"]] == 50.0


def test_member_can_delete_own_expense_but_not_restore_it(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense_id = make_expense(client, hh, token=member["token"])  # member is payer

    assert client.delete(f"/expenses/{expense_id}", headers=auth_headers(member["token"])).status_code == 204
    # restore is admin-only
    assert client.post(f"/expenses/{expense_id}/restore", headers=auth_headers(member["token"])).status_code == 403
    assert client.post(f"/expenses/{expense_id}/restore", headers=auth_headers(hh["admin_token"])).status_code == 200


def test_double_delete_conflicts(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh)
    assert client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"])).status_code == 204
    assert client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"])).status_code == 409


def test_shares_and_get_treat_a_trashed_expense_as_gone(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    expense_id = make_expense(client, hh)
    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))

    assert client.get(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"])).status_code == 404
    resp = client.patch(
        f"/expenses/{expense_id}/shares",
        json={"participants": [{"user_id": hh["admin_id"], "share": 2}, {"user_id": member["id"], "share": 1}]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert resp.status_code == 404


def test_expired_trash_is_purged_on_next_balance_read(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh)
    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))

    age_trash(hh["household_id"], days=settings.trash_retention_days + 1)
    client.get("/balances", headers=auth_headers(hh["admin_token"]))  # triggers purge

    trash = client.get("/expenses?include_deleted=true", headers=auth_headers(hh["admin_token"])).json()
    assert trash == []
    assert client.post(f"/expenses/{expense_id}/restore", headers=auth_headers(hh["admin_token"])).status_code == 404


def test_recent_trash_survives_a_balance_read(client):
    hh = setup_household(client, n_members=1)
    expense_id = make_expense(client, hh)
    client.delete(f"/expenses/{expense_id}", headers=auth_headers(hh["admin_token"]))

    age_trash(hh["household_id"], days=1)  # well within the window
    client.get("/balances", headers=auth_headers(hh["admin_token"]))

    trash = client.get("/expenses?include_deleted=true", headers=auth_headers(hh["admin_token"])).json()
    assert [e["id"] for e in trash] == [expense_id]


# ---- settlements ----

def make_settlement(client, hh, amount=20.0):
    member = hh["members"][0]
    resp = client.post(
        "/settlements",
        json={"from_user_id": member["id"], "to_user_id": hh["admin_id"], "amount": amount},
        headers=auth_headers(member["token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_settlement_soft_delete_and_restore_move_the_balance(client):
    hh = setup_household(client, n_members=1)
    make_expense(client, hh, amount=100.0)  # member owes admin 50
    assert net_by_user(client, hh)[hh["members"][0]["id"]] == -50.0

    settlement_id = make_settlement(client, hh, amount=50.0)  # squares it
    assert net_by_user(client, hh) == {}

    resp = client.delete(f"/settlements/{settlement_id}", headers=auth_headers(hh["admin_token"]))
    assert resp.status_code == 204
    assert net_by_user(client, hh)[hh["members"][0]["id"]] == -50.0

    assert client.post(
        f"/settlements/{settlement_id}/restore", headers=auth_headers(hh["admin_token"])
    ).status_code == 200
    assert net_by_user(client, hh) == {}


def test_settlement_party_can_delete_third_party_cannot(client):
    hh = setup_household(client, n_members=2)
    settlement_id = make_settlement(client, hh)
    outsider = hh["members"][1]

    assert client.delete(
        f"/settlements/{settlement_id}", headers=auth_headers(outsider["token"])
    ).status_code == 403
    # a party to it can
    assert client.delete(
        f"/settlements/{settlement_id}", headers=auth_headers(hh["members"][0]["token"])
    ).status_code == 204


def test_deleted_settlement_hidden_from_the_default_list(client):
    hh = setup_household(client, n_members=1)
    settlement_id = make_settlement(client, hh)
    client.delete(f"/settlements/{settlement_id}", headers=auth_headers(hh["admin_token"]))

    assert client.get("/settlements", headers=auth_headers(hh["admin_token"])).json() == []
    trash = client.get("/settlements?include_deleted=true", headers=auth_headers(hh["admin_token"])).json()
    assert [s["id"] for s in trash] == [settlement_id]
