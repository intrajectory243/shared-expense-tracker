"""Deterministic user identity: a user's id is uuid5(APP_NAMESPACE, email),
not an autoincrement integer -- computed the same way on any machine, so a
restored per-household file (roadmap Phase 8) references the same id a
person's account will have wherever they end up signing in, with no
integer-collision risk across instances. See the Phase 8 design memory for
the full rationale (restore creates an 'unclaimed' stub User for any id it
doesn't recognize yet; a real signup with the same email lands on that same
id and claims it automatically).
"""

import uuid

# Generated once (uuid.uuid4()) and hardcoded forever -- it's just a salt
# that scopes this app's uuid5 space away from anyone else's. Changing it
# would make every previously-issued user id stop matching its email.
APP_NAMESPACE = uuid.UUID("1f2f6a3e-6b0a-4b8a-9b0a-2f6a3e6b0a4b")


def user_uuid(email: str) -> str:
    return str(uuid.uuid5(APP_NAMESPACE, email.strip().lower()))
