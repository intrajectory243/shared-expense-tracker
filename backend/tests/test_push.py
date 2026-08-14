import requests
from pywebpush import WebPushException

import app.push as push_module
from app.database import SessionLocal
from app.models import AppSetting, PushSubscription


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


def subscribe(client, token, endpoint, p256dh="BItest", auth="authtest"):
    resp = client.post(
        "/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
        headers=auth_headers(token),
    )
    assert resp.status_code == 204, resp.text


def subscription_rows():
    db = SessionLocal()
    try:
        return db.query(PushSubscription).all()
    finally:
        db.close()


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


# ---- vapid key endpoint ----

def test_vapid_public_key_requires_auth(client):
    resp = client.get("/push/vapid-public-key")
    assert resp.status_code == 401


def test_vapid_public_key_is_generated_once_and_stable(client):
    hh = setup_household(client, n_members=0)
    first = client.get("/push/vapid-public-key", headers=auth_headers(hh["admin_token"])).json()
    second = client.get("/push/vapid-public-key", headers=auth_headers(hh["admin_token"])).json()
    assert first["public_key"] == second["public_key"]
    assert len(first["public_key"]) > 0

    db = SessionLocal()
    try:
        assert db.get(AppSetting, "vapid_private_key_pem") is not None
        assert db.get(AppSetting, "vapid_public_key") is not None
    finally:
        db.close()


# ---- subscribe / unsubscribe ----

def test_subscribe_creates_a_row_owned_by_the_caller(client):
    hh = setup_household(client, n_members=0)
    subscribe(client, hh["admin_token"], "https://push.example/ep-1")

    rows = subscription_rows()
    assert len(rows) == 1
    assert rows[0].user_id == hh["admin_id"]
    assert rows[0].endpoint == "https://push.example/ep-1"


def test_resubscribing_same_endpoint_moves_it_instead_of_duplicating(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    endpoint = "https://push.example/shared-ep"

    subscribe(client, hh["admin_token"], endpoint)
    subscribe(client, member["token"], endpoint)  # same browser, now logged in as the other user

    rows = subscription_rows()
    assert len(rows) == 1
    assert rows[0].user_id == member["id"]


def test_unsubscribe_only_removes_the_caller_s_own_subscription(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    subscribe(client, hh["admin_token"], "https://push.example/admin-ep")
    subscribe(client, member["token"], "https://push.example/member-ep")

    # Admin tries to unsubscribe the member's endpoint -- not theirs, no-op.
    resp = client.post(
        "/push/unsubscribe", json={"endpoint": "https://push.example/member-ep"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 204
    assert len(subscription_rows()) == 2

    resp = client.post(
        "/push/unsubscribe", json={"endpoint": "https://push.example/admin-ep"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 204
    remaining = subscription_rows()
    assert len(remaining) == 1
    assert remaining[0].endpoint == "https://push.example/member-ep"


# ---- triggers (webpush itself is monkeypatched -- no real network calls in tests) ----

def test_new_expense_pushes_to_other_members_but_not_the_creator(client, monkeypatch):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    subscribe(client, hh["admin_token"], "https://push.example/admin-ep")
    subscribe(client, member["token"], "https://push.example/member-ep")

    calls = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw["subscription_info"]["endpoint"]))

    resp = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(member["token"]),  # member creates it
    )
    assert resp.status_code == 201
    assert calls == ["https://push.example/admin-ep"]  # not the creator's own subscription


def test_join_request_pushes_to_admins_only(client, monkeypatch):
    hh = setup_household(client, n_members=0)
    subscribe(client, hh["admin_token"], "https://push.example/admin-ep")

    calls = []
    monkeypatch.setattr(push_module, "webpush", lambda **kw: calls.append(kw["subscription_info"]["endpoint"]))

    resp = signup(client, "newcomer@example.com", household_id=hh["household_id"], name="Newcomer")
    assert resp.status_code == 201
    assert calls == ["https://push.example/admin-ep"]


def test_push_network_failure_does_not_fail_the_triggering_request(client, monkeypatch):
    """A slow/unreachable subscription must never surface as a 500 on an
    otherwise-successful write -- the expense is already committed by the
    time push is attempted."""
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    subscribe(client, hh["admin_token"], "https://push.example/admin-ep")

    def boom(**kw):
        raise requests.exceptions.ConnectionError("simulated network failure")

    monkeypatch.setattr(push_module, "webpush", boom)

    resp = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(member["token"]),
    )
    assert resp.status_code == 201
    assert len(subscription_rows()) == 1  # a transient network error isn't proof the subscription is dead


def test_dead_subscription_is_pruned_on_410(client, monkeypatch):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    subscribe(client, hh["admin_token"], "https://push.example/gone-ep")

    def gone(**kw):
        raise WebPushException("gone", response=FakeResponse(410))

    monkeypatch.setattr(push_module, "webpush", gone)

    resp = client.post(
        "/expenses",
        json={"amount": 40.0, "description": "Snacks", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(member["token"]),
    )
    assert resp.status_code == 201
    assert subscription_rows() == []
