from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.apps.api.deps import get_db
from src.apps.api.security import (
    CONSOLE_SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_console_session_token,
    verify_password,
)
from src.packages.core.db.models import UserORM


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    account: str
    password: str
    remember_me: bool = True

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("Enter a valid email address.")
        return normalized


class LoginResponse(BaseModel):
    account: str
    display_name: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    account = payload.account.lower()
    user = db.scalar(select(UserORM).where(UserORM.email == account))
    if user is None or not user.enabled or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid account or password.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    cookie_options = {
        "key": CONSOLE_SESSION_COOKIE,
        "value": create_console_session_token(account),
        "httponly": True,
        "samesite": "lax",
        "path": "/console",
    }
    if payload.remember_me:
        cookie_options["max_age"] = SESSION_TTL_SECONDS
    response.set_cookie(**cookie_options)
    return LoginResponse(account=account, display_name=user.display_name)
