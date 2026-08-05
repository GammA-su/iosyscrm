"""Accès aux sessions authentifiées.

Les relations sont en `lazy="raise"` : l'utilisateur porté par une session est
donc chargé explicitement par `selectinload`.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Delete

from app.models.user import UserSession


def _execute_delete(db: Session, statement: Delete) -> int:
    """Exécute un DELETE et renvoie le nombre de lignes supprimées.

    `Session.execute` est typé `Result`, mais un DML renvoie toujours un
    `CursorResult` : le `cast` sert uniquement à le dire à mypy.
    """
    result = cast("CursorResult[Any]", db.execute(statement))
    return result.rowcount


def get_by_token_hash(db: Session, token_hash: str) -> UserSession | None:
    """Session par empreinte de jeton, utilisateur chargé, ou `None`."""
    statement = (
        select(UserSession)
        .where(UserSession.token_hash == token_hash)
        .options(selectinload(UserSession.user))
    )
    return db.execute(statement).scalar_one_or_none()


def add(db: Session, session: UserSession) -> UserSession:
    """Persiste une nouvelle session."""
    db.add(session)
    db.flush()
    return session


def delete_by_token_hash(db: Session, token_hash: str) -> int:
    """Supprime la session portant cette empreinte. Renvoie le nombre de lignes."""
    return _execute_delete(db, delete(UserSession).where(UserSession.token_hash == token_hash))


def delete_for_user(db: Session, user_id: int) -> int:
    """Supprime toutes les sessions d'un utilisateur. Renvoie le nombre de lignes."""
    return _execute_delete(db, delete(UserSession).where(UserSession.user_id == user_id))
