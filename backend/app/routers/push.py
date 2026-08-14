from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import PushSubscription, User
from app.push import get_public_key
from app.schemas import PushSubscriptionIn, PushUnsubscribe, VapidKeyOut

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidKeyOut)
def vapid_public_key(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return VapidKeyOut(public_key=get_public_key(db))


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def subscribe(payload: PushSubscriptionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Re-subscribing an endpoint already on file just re-homes it -- covers a
    # browser handing back the same endpoint after a logout/login as someone else.
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint).first()
    if existing:
        existing.user_id = user.id
        existing.p256dh = payload.keys.get("p256dh", "")
        existing.auth = payload.keys.get("auth", "")
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=payload.endpoint,
                p256dh=payload.keys.get("p256dh", ""),
                auth=payload.keys.get("auth", ""),
            )
        )
    db.commit()


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(payload: PushUnsubscribe, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == payload.endpoint, PushSubscription.user_id == user.id
    ).delete()
    db.commit()
