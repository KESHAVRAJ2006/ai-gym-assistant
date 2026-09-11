"""
SQLAlchemy engine + session plumbing for PostgreSQL (SQLite locally).

`get_db` is the FastAPI dependency: it hands a route one session and always
closes it, even if the route raises.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_url = settings.sqlalchemy_url
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(
    _url,
    echo=False,
    future=True,
    pool_pre_ping=True,          # silently reconnects after Render idles the DB
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Parent class of every ORM model."""


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
