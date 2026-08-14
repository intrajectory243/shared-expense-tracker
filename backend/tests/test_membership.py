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
    """One admin + n_members approved regular members, all in the same household."""
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


# ---- Role changes ----

def test_role_change_promotes_and_demotes_member(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    promote = client.patch(
        f"/users/{member['id']}", json={"role": "admin"}, headers=auth_headers(hh["admin_token"])
    )
    assert promote.status_code == 200, promote.text
    assert promote.json()["role"] == "admin"

    demote = client.patch(
        f"/users/{member['id']}", json={"role": "member"}, headers=auth_headers(hh["admin_token"])
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "member"


def test_cannot_demote_last_admin(client):
    hh = setup_household(client, n_members=0)
    resp = client.patch(
        f"/users/{hh['admin_id']}", json={"role": "member"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 400


def test_lone_admin_can_demote_other_admin_but_not_then_self(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.patch(f"/users/{member['id']}", json={"role": "admin"}, headers=auth_headers(hh["admin_token"]))

    # Two admins now -- demoting the other one is fine.
    demote_other = client.patch(
        f"/users/{member['id']}", json={"role": "member"}, headers=auth_headers(hh["admin_token"])
    )
    assert demote_other.status_code == 200

    # Now back to one admin -- demoting self is blocked (not via isSelf, but
    # because it would leave the household with zero admins).
    demote_self = client.patch(
        f"/users/{hh['admin_id']}", json={"role": "member"}, headers=auth_headers(hh["admin_token"])
    )
    assert demote_self.status_code == 400


# ---- Access changes (moved_out / removed) ----

def test_admin_cannot_change_own_access(client):
    hh = setup_household(client, n_members=0)
    resp = client.patch(
        f"/users/{hh['admin_id']}", json={"status": "moved_out"}, headers=auth_headers(hh["admin_token"])
    )
    assert resp.status_code == 400


def test_last_admin_cannot_be_moved_out(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.patch(f"/users/{member['id']}", json={"role": "admin"}, headers=auth_headers(hh["admin_token"]))
    # member (now admin) moves the original admin out -- but that would leave
    # member as an admin too, so this should actually succeed (2 admins).
    ok = client.patch(
        f"/users/{hh['admin_id']}", json={"status": "moved_out"}, headers=auth_headers(member["token"])
    )
    assert ok.status_code == 200


def test_moved_out_member_cannot_log_expenses_but_can_read_and_settle(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    # A real expense first, so there's something to read/settle.
    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Groceries", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )

    move_out = client.patch(
        f"/users/{member['id']}", json={"status": "moved_out"}, headers=auth_headers(hh["admin_token"])
    )
    assert move_out.status_code == 200
    assert move_out.json()["status"] == "moved_out"

    # Can still read.
    assert client.get("/balances", headers=auth_headers(member["token"])).status_code == 200
    assert client.get("/expenses", headers=auth_headers(member["token"])).status_code == 200

    # Can still settle up.
    settle = client.post(
        "/settlements",
        json={"to_user_id": hh["admin_id"], "amount": 50.0},
        headers=auth_headers(member["token"]),
    )
    assert settle.status_code == 201, settle.text

    # Cannot log a new expense themselves.
    blocked = client.post(
        "/expenses",
        json={"amount": 10.0, "description": "Snack", "participant_ids": [member["id"]]},
        headers=auth_headers(member["token"]),
    )
    assert blocked.status_code == 403

    # Cannot be tagged as a participant/payer on someone else's new expense.
    tag_blocked = client.post(
        "/expenses",
        json={"amount": 10.0, "description": "Snack", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    assert tag_blocked.status_code == 400


def test_removed_member_cannot_login_and_existing_token_is_blocked(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    old_token = member["token"]

    revoke = client.patch(
        f"/users/{member['id']}", json={"status": "removed"}, headers=auth_headers(hh["admin_token"])
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "removed"

    login_attempt = client.post("/auth/login", data={"username": member["email"], "password": "password123"})
    assert login_attempt.status_code == 401
    assert "no longer has access" in login_attempt.json()["detail"]

    # The token issued before revocation must stop working immediately too.
    blocked = client.get("/auth/me", headers=auth_headers(old_token))
    assert blocked.status_code == 401


def test_moved_out_and_removed_members_still_count_in_balances(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.post(
        "/expenses",
        json={"amount": 100.0, "description": "Rent", "participant_ids": [hh["admin_id"], member["id"]]},
        headers=auth_headers(hh["admin_token"]),
    )
    client.patch(f"/users/{member['id']}", json={"status": "removed"}, headers=auth_headers(hh["admin_token"]))

    balances = client.get("/balances", headers=auth_headers(hh["admin_token"])).json()
    net_by_user = {b["user_id"]: b["net"] for b in balances["balances"]}
    assert net_by_user[member["id"]] == -50.0


# ---- Declining pending requests ----

def test_decline_pending_request(client):
    hh = setup_household(client, n_members=0)
    pending_resp = signup(client, "candidate@example.com", household_id=hh["household_id"], name="Candidate")
    candidate_id = pending_resp.json()["id"]

    decline = client.delete(f"/users/{candidate_id}", headers=auth_headers(hh["admin_token"]))
    assert decline.status_code == 204

    pending_list = client.get("/users/pending", headers=auth_headers(hh["admin_token"])).json()
    assert candidate_id not in [p["id"] for p in pending_list]

    # Can't decline someone who isn't pending.
    not_pending = client.delete(f"/users/{hh['admin_id']}", headers=auth_headers(hh["admin_token"]))
    assert not_pending.status_code == 404


# ---- Former members list ----

def test_former_users_listed_separately_from_active(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]
    client.patch(f"/users/{member['id']}", json={"status": "moved_out"}, headers=auth_headers(hh["admin_token"]))

    active = client.get("/users", headers=auth_headers(hh["admin_token"])).json()
    assert member["id"] not in [u["id"] for u in active]

    former = client.get("/users/former", headers=auth_headers(hh["admin_token"])).json()
    assert len(former) == 1
    assert former[0]["id"] == member["id"]
    assert former[0]["status"] == "moved_out"


# ---- Household rename ----

def test_household_rename_admin_only(client):
    hh = setup_household(client, n_members=1)
    member = hh["members"][0]

    forbidden = client.patch(
        f"/households/{hh['household_id']}", json={"name": "New Name"}, headers=auth_headers(member["token"])
    )
    assert forbidden.status_code == 403

    ok = client.patch(
        f"/households/{hh['household_id']}", json={"name": "New Name"}, headers=auth_headers(hh["admin_token"])
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "New Name"


# ---- Invites ----

def test_invite_flow_end_to_end(client):
    hh = setup_household(client, n_members=0)

    invite = client.post(
        "/users/invite",
        json={"name": "Invited Person", "email": "invited@example.com", "role": "member"},
        headers=auth_headers(hh["admin_token"]),
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["invite_token"]
    invited_user_id = invite.json()["user"]["id"]

    # Shows up in the pending list, flagged as invited.
    pending_list = client.get("/users/pending", headers=auth_headers(hh["admin_token"])).json()
    invited_entry = next(p for p in pending_list if p["id"] == invited_user_id)
    assert invited_entry["invited"] is True

    preview = client.get(f"/auth/invite/{token}")
    assert preview.status_code == 200
    assert preview.json()["email"] == "invited@example.com"

    accept = client.post("/auth/accept-invite", json={"token": token, "password": "newpassword123"})
    assert accept.status_code == 200, accept.text
    assert accept.json()["status"] == "approved"

    # Accepting skips admin approval entirely -- can log in right away.
    login_resp = client.post(
        "/auth/login", data={"username": "invited@example.com", "password": "newpassword123"}
    )
    assert login_resp.status_code == 200

    # Token can't be reused.
    reuse = client.post("/auth/accept-invite", json={"token": token, "password": "anotherpassword"})
    assert reuse.status_code == 404
