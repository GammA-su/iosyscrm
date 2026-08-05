"""Engine SQLAlchemy synchrone et fabrique de sessions.

Le code est volontairement synchrone (section 1.2). L'engine est créé
paresseusement pour qu'importer ce module n'ouvre aucune connexion.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine unique du processus."""
    settings: Settings = get_settings()
    return create_engine(
        settings.DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        pool_pre_ping=True,
        echo=settings.DATABASE_ECHO,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Fabrique de sessions liée à l'engine du processus."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session transactionnelle : commit en sortie normale, rollback sur exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
