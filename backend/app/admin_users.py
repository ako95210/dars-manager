from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_admin
from .database import get_db
from .models import AuthSession, User, utc_now
from .security import hash_password, normalize_email


router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class AdminUserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: Literal["client", "admin"]
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime


class AdminUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=256)
    role: Literal["client", "admin"] = "client"

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return value.strip()


class AdminUserUpdateRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["client", "admin"]
    is_active: bool

    @field_validator("display_name", mode="before")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return value.strip()


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=256)


def admin_user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
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
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    user = User(
        email=normalize_email(str(payload.email)),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for this email address",
        ) from exc
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
    if user.id == admin.id and (payload.role != "admin" or not payload.is_active):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot remove your own administrator access",
        )
    ensure_an_active_admin_remains(db, user, payload.role, payload.is_active)
    user.email = normalize_email(str(payload.email))
    user.display_name = payload.display_name
    user.role = payload.role
    user.is_active = payload.is_active
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
    user.password_hash = hash_password(payload.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
