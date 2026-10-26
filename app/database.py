"""Database connection management using SQLAlchemy 2.x and Psycopg 3."""

import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.base import Base

logger = logging.getLogger(__name__)

# SQLAlchemy 2.x engine using psycopg (Psycopg 3)
engine = create_engine(
    settings.database_url,
    connect_args={"connect_timeout": 10},
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_db_connection() -> bool:
    """Executes SELECT 1 against the configured PostgreSQL database.

    Returns:
        True if the query succeeds.

    Raises:
        Exception: If the connection or query fails (caller handles sanitized response).
    """
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        scalar = result.scalar()
        return scalar == 1
