"""
Password hashing (bcrypt) and JWT issue/verify (PyJWT).

We call bcrypt directly rather than through passlib: passlib 1.7.4 crashes
against bcrypt 4.x with an AttributeError about a missing dunder-about
attribute, and this project does not need passlib multi-scheme support.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt hard-truncates at 72 bytes; do it explicitly so behaviour is obvious.
_MAX_PW_BYTES = 72


def hash_password(plain: str) -> str:
    pw = plain.encode("utf-8")[:_MAX_PW_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:_MAX_PW_BYTES], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, email: str) -> tuple[str, int]:
    """Returns (jwt_string, seconds_until_expiry)."""
    expire_seconds = settings.JWT_EXPIRE_MINUTES * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expire_seconds


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
