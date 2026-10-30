from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Headroom sized for a redeploy reconnect burst: the whole frontend (plus its
# polling) reconnects at once against a cold pool, and a couple of slow endpoints
# can each hold a connection for a while. The stock 5+10 pool exhausted during
# exactly that herd and stalled every request for 30s. 10+20 = 30 connections is
# still well under Postgres' default max_connections (100). pool_recycle drops
# sockets older than 30 min so a stale connection is replaced, not handed out;
# pool_timeout fails a starved request in 8s instead of hanging for 30.
_engine_kwargs: dict = {
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20,
    "pool_recycle": 1800,
    "pool_timeout": 8,
}
if settings.database_url.startswith("sqlite"):
    # SQLite (test harness): allow cross-thread use and keep a single shared
    # connection so an in-memory/temp DB is consistent across requests.
    _engine_kwargs = {"connect_args": {"check_same_thread": False}}

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
