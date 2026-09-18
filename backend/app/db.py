from __future__ import annotations

from sqlalchemy import create_engine, event
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

# SQLite ignores foreign keys unless asked, per connection. Without this a bad
# f_id inserts happily on the laptop and is rejected by Neon - the backfills
# would be tested against a database that does not enforce the constraints
# they depend on. Postgres needs nothing; the listener is only registered for
# SQLite so it cannot fire against it.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_enforce_foreign_keys(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
