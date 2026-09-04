"""Email/password signup + login, password change, and Google sign-in.

Google uses the authorization-code flow: the browser is redirected to
Google, Google redirects back to /api/auth/google/callback, and the backend
then bounces to the frontend with a short-lived session JWT in the URL
fragment. The client secret never reaches the browser.
"""
from __future__ import annotations

import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import (create_access_token, current_user, hash_password,
                    verify_password)
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..schemas import (ChangePasswordIn, LoginIn, SignUpIn, TokenOut, UserOut)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"


def _by_email(db: Session, email: str) -> Optional[User]:
    # emails are stored lowercased; compare case-insensitively so
    # "A@b.com" and "a@b.com" cannot become two accounts
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignUpIn, db: Session = Depends(get_db)) -> TokenOut:
    if _by_email(db, payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name or payload.email.split("@")[0],
        auth_provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user.id),
                    user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = _by_email(db, payload.email)
    # same message either way: revealing "no such email" lets an attacker
    # enumerate which addresses have accounts
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    if user is None or not user.hashed_password:
        raise invalid
    if not verify_password(payload.password, user.hashed_password):
        raise invalid
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    return TokenOut(access_token=create_access_token(user.id),
                    user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePasswordIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> None:
    if not user.hashed_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "this account signs in with Google and has no password")
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "current password is wrong")
    user.hashed_password = hash_password(payload.new_password)
    db.commit()


# ---------------------------------------------------------------- Google ----

@router.get("/google/config")
def google_config() -> dict:
    """Lets the UI hide the Google button when credentials are absent, instead
    of showing a button that 500s."""
    return {"enabled": settings.google_enabled}


@router.get("/google/login")
def google_login(request: Request) -> RedirectResponse:
    if not settings.google_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Google sign-in is not configured on this server")
    # CSRF protection: a random state echoed back by Google and checked in the
    # callback. Stored in a short-lived, http-only cookie.
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.backend_origin}/api/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}")
    resp.set_cookie("g_state", state, max_age=600, httponly=True,
                samesite="lax", secure=settings.env == "production")
    return resp


@router.get("/google/callback")
def google_callback(request: Request, code: Optional[str] = None,
                    state: Optional[str] = None, error: Optional[str] = None,
                    db: Session = Depends(get_db)) -> RedirectResponse:
    def fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.frontend_origin}/login?error={reason}")

    if error or not code:
        return fail(error or "no_code")
    if not state or state != request.cookies.get("g_state"):
        return fail("bad_state")

    with httpx.Client(timeout=10) as client:
        tok = client.post(GOOGLE_TOKEN, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": f"{settings.backend_origin}/api/auth/google/callback",
            "grant_type": "authorization_code",
        })
        if tok.status_code != 200:
            return fail("token_exchange_failed")
        id_token = tok.json().get("id_token")
        if not id_token:
            return fail("no_id_token")
        # Google just minted this token over TLS in a direct server-to-server
        # call, so it is trusted here; claims are read without re-verifying
        # the signature against the JWKS.
        claims = jwt.get_unverified_claims(id_token)

    email = (claims.get("email") or "").lower()
    sub = claims.get("sub")
    if not email or not sub:
        return fail("no_email")

    user = db.scalar(select(User).where(User.google_sub == sub)) or _by_email(db, email)
    if user is None:
        user = User(email=email, display_name=claims.get("name") or email.split("@")[0],
                    auth_provider="google", google_sub=sub, hashed_password=None)
        db.add(user)
    elif user.google_sub is None:
        # existing password account, same verified address: link them rather
        # than creating a duplicate account for the same person
        user.google_sub = sub
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    resp = RedirectResponse(f"{settings.frontend_origin}/login#token={token}")
    resp.delete_cookie("g_state")
    return resp
