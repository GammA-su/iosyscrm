"""Dépendances FastAPI.

Pour l'instant, uniquement la session de base de données. Les dépendances
d'authentification (`current_user`, `require_role`) arrivent en T2.
"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database.engine import get_session_factory


def get_db() -> Iterator[Session]:
    """Fournit une session SQLAlchemy le temps d'une requête, puis la ferme."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
