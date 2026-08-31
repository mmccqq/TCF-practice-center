"""Password hashing, session JWTs, and the current-user dependency."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

settings = get_settings()
ALGORITHM = "HS256"
# auto_error=False so optional-auth routes can see "no token" as None rather
# than a 403 raised before our own code runs
bearer = HTTPBearer(auto_error=False)


# passlib is unmaintained (2020) and breaks against bcrypt >= 4.1, so the
# bcrypt library is used directly rather than pinning to an old version.
def hash_password(raw: str) -> str:
    # bcrypt hard-limits the secret to 72 bytes; rejecting is better than a
    # password that appears to work but silently ignores its tail
    if len(raw.encode()) > 72:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "password must be at most 72 bytes")
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except ValueError:
        return False       # malformed stored hash, or secret over the limit


def create_access_token(user_id: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + dt.timedelta(days=settings.access_token_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _user_from_token(token: str, db: Session) -> Optional[User]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    sub = payload.get("sub")
    if not sub or not str(sub).isdigit():
        return None
    return db.get(User, int(sub))


def current_user_optional(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if creds is None:
        return None
    user = _user_from_token(creds.credentials, db)
    return user if (user and user.is_active) else None


def current_user(
    user: Optional[User] = Depends(current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user
