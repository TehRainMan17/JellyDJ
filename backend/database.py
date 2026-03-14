"""
JellyDJ — SQLAlchemy database setup.

Uses SQLite by default (stored at /config/jellydj.db inside the container).
The /config path is a Docker named volume so data persists across restarts
and container rebuilds.

To use a different database (e.g. PostgreSQL), set DATABASE_URL in .env:
  DATABASE_URL=postgresql://user:password@host:5432/jellydj

SQLAlchemy will detect the dialect from the URL and adjust accordingly,
though only SQLite has been tested in production.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Read the connection string from the environment.
# Default points to the Docker volume mount inside the container.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////config/jellydj.db")

# check_same_thread=False is required for SQLite when the same connection is
# used across multiple threads (FastAPI runs request handlers in a thread pool).
# This is safe here because SQLAlchemy manages connection pooling itself.
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        # Without a timeout, concurrent writers (e.g. the 5-worker popularity
        # cache refresh) immediately get "database is locked" on host-filesystem
        # bind mounts. Named Docker volumes handle this more gracefully but
        # bind mounts use strict OS file locking. 30 seconds gives all workers
        # time to queue up and write sequentially rather than failing instantly.
        "timeout": 30,
    } if "sqlite" in DATABASE_URL else {},
)

# Enable WAL (Write-Ahead Logging) for SQLite.
# Default journal mode is DELETE which serialises ALL readers and writers —
# any write blocks every read until the transaction commits. WAL allows
# concurrent reads while a write is in progress, and serialises multiple
# writers gracefully via the timeout above instead of immediately erroring.
# This is safe and recommended for any multi-threaded SQLite usage.
# It's a no-op on non-SQLite databases.
if "sqlite" in DATABASE_URL:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, faster than FULL
        cursor.close()

# Session factory — use get_db() as a FastAPI dependency to get a session
# that is automatically closed after the request completes.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all ORM models — imported in models.py
Base = declarative_base()


def get_db():
    """
    FastAPI dependency that provides a database session for a single request.

    Usage in a route:
        @router.get("/something")
        def my_route(db: Session = Depends(get_db)):
            ...

    The session is always closed in the finally block, even if the route
    raises an exception, preventing connection leaks.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
