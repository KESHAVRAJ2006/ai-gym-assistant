"""FastAPI dependencies shared by the routers."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise _UNAUTH
    payload = decode_token(creds.credentials)
    if not payload:
        raise _UNAUTH
    user = db.get(User, int(payload.get("sub", 0)))
    if user is None:
        raise _UNAUTH
    return user


def user_from_token_string(token: str, db: Session) -> User | None:
    """WebSockets cannot send Authorization headers, so they pass ?token=..."""
    payload = decode_token(token)
    if not payload:
        return None
    return db.get(User, int(payload.get("sub", 0)))


def is_admin(user: User) -> bool:
    """
    Admin is decided by configuration, not by a database column.

    Keeping it out of the schema means no migration, and no risk of an
    attacker escalating by writing to their own row. If ADMIN_EMAILS is
    unset the first registered account is the admin, which is the sane
    default for a single-operator demo deployment.
    """
    emails = settings.admin_emails
    if emails:
        return user.email.lower() in emails
    return user.id == 1


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return user
