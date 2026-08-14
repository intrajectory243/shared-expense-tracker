"""Web Push sending. VAPID keys are generated once on first use and stored
in app_settings so a self-hosted install needs zero push-related config --
same "usable immediately" philosophy as bootstrap_admin."""

import base64
import json
import logging

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.models import AppSetting, PushSubscription

logger = logging.getLogger(__name__)

_PRIVATE_KEY_SETTING = "vapid_private_key_pem"
_PUBLIC_KEY_SETTING = "vapid_public_key"
_VAPID_CLAIMS = {"sub": "mailto:push@halves.local"}

_vapid_cache: tuple[Vapid01, str] | None = None


def _get_or_create_vapid(db: Session) -> tuple[Vapid01, str]:
    # Cache the (Vapid object, public key string) pair together so a warm
    # cache never needs another DB round trip -- a version that re-fetched
    # just the public key row on every call would break the moment that row
    # is gone (e.g. tests resetting the schema between runs, or a restore
    # from an older backup) even though the private key is still valid.
    global _vapid_cache
    if _vapid_cache is not None:
        return _vapid_cache

    private_row = db.get(AppSetting, _PRIVATE_KEY_SETTING)
    public_row = db.get(AppSetting, _PUBLIC_KEY_SETTING)
    if private_row is not None and public_row is not None:
        _vapid_cache = (Vapid01.from_pem(private_row.value.encode()), public_row.value)
        return _vapid_cache

    vapid = Vapid01()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    public_b64 = (
        base64.urlsafe_b64encode(
            vapid.public_key.public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        .decode()
        .rstrip("=")
    )
    db.merge(AppSetting(key=_PRIVATE_KEY_SETTING, value=private_pem))
    db.merge(AppSetting(key=_PUBLIC_KEY_SETTING, value=public_b64))
    db.commit()
    _vapid_cache = (vapid, public_b64)
    return _vapid_cache


def get_public_key(db: Session) -> str:
    _, public_b64 = _get_or_create_vapid(db)
    return public_b64


def send_to_users(db: Session, user_ids: list[int], title: str, body: str, url: str = "/") -> None:
    """Best-effort: a push failure never surfaces to the caller. Subscriptions
    the browser has since revoked (404/410) are pruned as they're found."""
    if not user_ids:
        return
    subs = db.query(PushSubscription).filter(PushSubscription.user_id.in_(user_ids)).all()
    if not subs:
        return

    vapid, _ = _get_or_create_vapid(db)
    payload = json.dumps({"title": title, "body": body, "url": url})
    dead_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=vapid,
                vapid_claims=dict(_VAPID_CLAIMS),
                # pywebpush forwards this straight to requests.post(timeout=...),
                # which takes seconds -- without it, a slow/unreachable push
                # endpoint could block this request (called synchronously from
                # inside e.g. create_expense) for pywebpush's own default of
                # 10000, which is *seconds*, not ms.
                timeout=5,
            )
        except WebPushException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                dead_ids.append(sub.id)
            else:
                logger.warning("Push to subscription %s failed: %s", sub.id, exc)
        except requests.exceptions.RequestException as exc:
            # Network-level failure (timeout, connection refused, DNS, ...) --
            # pywebpush doesn't wrap these as WebPushException, so they'd
            # otherwise propagate out of this best-effort call and fail the
            # request that triggered it (e.g. an already-committed expense).
            logger.warning("Push to subscription %s failed: %s", sub.id, exc)

    if dead_ids:
        db.query(PushSubscription).filter(PushSubscription.id.in_(dead_ids)).delete(synchronize_session=False)
        db.commit()
