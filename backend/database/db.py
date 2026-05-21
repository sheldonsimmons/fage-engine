"""
db.py — SQLAlchemy database engine and session factory.

The database file (fage.db) is created automatically in the backend/ directory
the first time the app starts or seed.py is run.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite file stored alongside the backend code — no installation required
DATABASE_URL = "sqlite:///./fage.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# All ORM models inherit from this base
Base = declarative_base()


def get_db():
    """
    FastAPI dependency that yields a database session and closes it afterwards.
    Usage in a route: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
