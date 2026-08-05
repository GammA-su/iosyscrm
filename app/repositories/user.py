"""Accès aux utilisateurs. Aucune règle métier : ni hachage, ni contrôle de rôle."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_id(db: Session, user_id: int) -> User | None:
    """Utilisateur par identifiant, ou `None`."""
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    """Utilisateur par adresse email exacte, ou `None`."""
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def list_all(db: Session) -> list[User]:
    """Tous les utilisateurs, triés par email."""
    return list(db.execute(select(User).order_by(User.email)).scalars())


def add(db: Session, user: User) -> User:
    """Persiste un nouvel utilisateur et renvoie l'instance rafraîchie."""
    db.add(user)
    db.flush()
    return user
