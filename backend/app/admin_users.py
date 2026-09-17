from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_admin
from .config import settings
from .database import get_db
from .email_delivery import (
    EmailDeliveryError,
    email_delivery_configured,
    send_account_invitation,
)
from .models import AccountInvitation, AuthSession, User, utc_now
from .security import (
    hash_invitation_token,
    hash_password,
    new_invitation_token,
    normalize_email,
)


router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class AdminUserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: Literal["client", "admin"]
    is_active: bool
    email_verified_at: datetime | None
    invitation_sent_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime


class AdminUserCreateRequest(BaseModel):
    email: EmailStr


class AdminUserUpdateRequest(BaseModel):
    email: EmailStr
    role: Literal["client", "admin"]
    is_active: bool


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=256)


def admin_user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        email_verified_at=user.email_verified_at,
        invitation_sent_at=user.invitation_sent_at,
        deleted_at=user.deleted_at,
        created_at=user.created_at,
    )


def user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return user


def ensure_an_active_admin_remains(db: Session, user: User, role: str, is_active: bool) -> None:
    if user.role != "admin" or (role == "admin" and is_active):
        return
    other_active_admins = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == "admin", User.is_active.is_(True), User.id != user.id)
    )
    if not other_active_admins:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="At least one active administrator is required",
        )


def require_email_delivery() -> None:
    if not email_delivery_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le service d’envoi d’e-mails doit être configuré avant de créer un compte.",
        )


def issue_invitation(db: Session, user: User, admin_id: str) -> None:
    now = utc_now()
    db.execute(
        update(AccountInvitation)
        .where(AccountInvitation.user_id == user.id, AccountInvitation.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_invitation_token()
    db.add(
        AccountInvitation(
            user_id=user.id,
            token_hash=hash_invitation_token(token),
            expires_at=now + timedelta(seconds=settings.invitation_ttl_seconds),
            created_by_user_id=admin_id,
        )
    )
    user.invitation_sent_at = None
    db.commit()
    try:
        send_account_invitation(user.email, token)
    except EmailDeliveryError:
        raise
    user.invitation_sent_at = utc_now()
    db.commit()


@router.get("/email-status")
def email_status(_admin: User = Depends(require_admin)) -> dict:
    return {
        "configured": email_delivery_configured(),
        "sender": settings.email_from or "",
    }


@router.get("", response_model=list[AdminUserResponse])
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminUserResponse]:
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.email)).all()
    return [admin_user_response(user) for user in users]


@router.post("", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    require_email_delivery()
    user = User(
        email=normalize_email(str(payload.email)),
        display_name="Nom à définir",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="client",
        is_active=False,
        email_verified_at=None,
    )
    db.add(user)
    try:
        db.flush()
        issue_invitation(db, user, admin.id)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email address",
        ) from exc
    except EmailDeliveryError as exc:
        db.delete(user)
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.refresh(user)
    return admin_user_response(user)


@router.put("/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: str,
    payload: AdminUserUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    user = user_or_404(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=409, detail="A deleted account cannot be modified")
    normalized_email = normalize_email(str(payload.email))
    email_changed = normalized_email != user.email
    if user.id == admin.id and (
        payload.role != "admin" or not payload.is_active or email_changed
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot remove your own administrator access",
        )
    if email_changed:
        require_email_delivery()
    if user.email_verified_at is None and payload.is_active and not email_changed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The email address must be verified before activating this account",
        )
    requested_active = False if email_changed else payload.is_active
    ensure_an_active_admin_remains(db, user, payload.role, requested_active)
    user.email = normalized_email
    user.role = payload.role
    user.is_active = requested_active
    if email_changed:
        user.email_verified_at = None
        user.invitation_sent_at = None
    if not user.is_active:
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email address",
        ) from exc
    if email_changed:
        try:
            issue_invitation(db, user, admin.id)
        except EmailDeliveryError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.refresh(user)
    return admin_user_response(user)


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: str,
    payload: AdminPasswordResetRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user = user_or_404(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=409, detail="A deleted account cannot be modified")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use your security settings to change your own password",
        )
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account must verify its email address first",
        )
    user.password_hash = hash_password(payload.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/invitation", response_model=AdminUserResponse)
def resend_invitation(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    require_email_delivery()
    user = user_or_404(db, user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=409, detail="A deleted account cannot be modified")
    if user.email_verified_at is not None:
        raise HTTPException(status_code=409, detail="This email address is already verified")
    try:
        issue_invitation(db, user, admin.id)
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    db.refresh(user)
    return admin_user_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    user = user_or_404(db, user_id)
    if user.deleted_at is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete your own administrator account",
        )
    ensure_an_active_admin_remains(db, user, "client", False)
    user.email = f"deleted-{user.id}@dars-manager.com"
    user.display_name = f"Compte supprimé ({user.id[:8]})"
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.is_active = False
    user.deleted_at = utc_now()
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.execute(
        update(AccountInvitation)
        .where(AccountInvitation.user_id == user.id, AccountInvitation.used_at.is_(None))
        .values(used_at=utc_now())
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
