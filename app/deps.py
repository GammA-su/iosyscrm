"""Dépendances FastAPI : session de base de données et utilisateur courant."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database.engine import get_session_factory
from app.exceptions import AuthenticationError, PermissionDeniedError
from app.models.user import User
from app.services import auth


def get_db() -> Iterator[Session]:
    """Fournit une session SQLAlchemy le temps d'une requête, puis la ferme."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def _user_from_request(request: Request, db: Session) -> User | None:
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not token:
        return None
    return auth.resolve_session(db, token)


def get_optional_user(request: Request, db: DbSession) -> User | None:
    """Utilisateur courant s'il y a une session valide, sinon `None`."""
    return _user_from_request(request, db)


def get_current_user(request: Request, db: DbSession) -> User:
    """Utilisateur courant. Lève 401 sans session valide."""
    user = _user_from_request(request, db)
    if user is None:
        raise AuthenticationError("Authentification requise.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    """Fabrique une dépendance qui exige l'un des rôles donnés, sinon 403."""

    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise PermissionDeniedError("Votre rôle ne permet pas cette opération.")
        return user

    return dependency
