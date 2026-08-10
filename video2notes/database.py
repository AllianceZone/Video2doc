"""
SQLAlchemy engine + session factory. Works against SQLite (zero-dependency
local dev, and tests) or Postgres (production) via the same DATABASE_URL --
see config.py.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Creates all tables. Fine for dev/SQLite; use Alembic migrations in
    production against Postgres instead of calling this."""
    import models  # noqa: F401 -- ensures all model classes are registered on Base
    Base.metadata.create_all(bind=engine)
