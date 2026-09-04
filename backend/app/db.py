from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

# check_same_thread=False is required for SQLite under a threaded ASGI server;
# it is ignored by other drivers, so this stays correct after a move to Postgres.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
# pool_pre_ping: Neon suspends an idle database after a few minutes, which
# silently kills the connections sitting in the pool. Without this, the first
# request after a quiet spell fails with "server closed the connection". The
# ping is a sub-millisecond round trip and a no-op on SQLite.
engine = create_engine(settings.database_url, connect_args=connect_args,
                       future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
