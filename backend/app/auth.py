from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuthSession, User
from .security import (
    DUMMY_PASSWORD_HASH,
    LoginThrottle,
    hash_password,
    hash_session_token,
    is_expired,
    new_session_token,
    normalize_email,
    session_expiration,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])
account_login_throttle = LoginThrottle(max_failures=5)
address_login_throttle = LoginThrottle(max_failures=20)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


def require_user(
    token: Annotated[str | None, Cookie(alias=settings.session_cookie)] = None,
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    )
    if auth_session is None or is_expired(auth_session.expires_at):
        if auth_session is not None:
            db.delete(auth_session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive account")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def require_client(user: User = Depends(require_user)) -> User:
    if user.role != "client":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client workspace access required",
        )
    return user


@router.post("/login", response_model=UserResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    normalized_email = normalize_email(str(payload.email))
    client_host = request.client.host if request.client else "unknown"
    account_key = f"account:{normalized_email}"
    address_key = f"address:{client_host}"
    retry_after = max(
        account_login_throttle.retry_after(account_key),
        address_login_throttle.retry_after(address_key),
    )
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    user = db.scalar(select(User).where(User.email == normalized_email))
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_valid = verify_password(payload.password, password_hash)
    if user is None or not password_valid or not user.is_active:
        account_login_throttle.failure(account_key)
        address_login_throttle.failure(address_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    account_login_throttle.clear(account_key)
    address_login_throttle.clear(address_key)

    token = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=session_expiration(settings.session_ttl_seconds),
        )
    )
    db.commit()
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return user_response(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(require_user)) -> UserResponse:
    return user_response(user)


@router.put("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is invalid",
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The new password must be different",
        )
    user.password_hash = hash_password(payload.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    response.delete_cookie(settings.session_cookie, path="/")


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    token: Annotated[str | None, Cookie(alias=settings.session_cookie)] = None,
    db: Session = Depends(get_db),
) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_session_token(token)))
        db.commit()
    response.delete_cookie(settings.session_cookie, path="/")
