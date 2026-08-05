"""Écran de connexion et déconnexion (section 11.1)."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Query
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_401_UNAUTHORIZED,
    HTTP_429_TOO_MANY_REQUESTS,
)

from app.api.web import templates
from app.config import get_settings
from app.deps import DbSession, OptionalUser
from app.exceptions import AuthenticationError, RateLimitedError
from app.services import auth

router = APIRouter(tags=["web-auth"])

DEFAULT_REDIRECT = "/"
SESSION_MAX_AGE_SECONDS = int(auth.SESSION_TTL.total_seconds())


def safe_next(candidate: str | None) -> str:
    """Filtre la cible de redirection : uniquement une URL relative du site.

    Tout ce qui porte un schéma, un hôte, ou commence par `//` ou `/\\` est
    écarté — c'est la parade à la redirection ouverte, un `next=` étant par
    nature fourni par l'appelant.
    """
    if not candidate:
        return DEFAULT_REDIRECT
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return DEFAULT_REDIRECT
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return DEFAULT_REDIRECT
    return candidate


def _render_login(
    request: Request,
    *,
    next_url: str,
    email: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "pages/login.html",
        {"next_url": next_url, "email": email, "error": error, "user": None},
        status_code=status_code,
    )


@router.get("/login")
def login_page(
    request: Request,
    user: OptionalUser,
    next_url: Annotated[str | None, Query(alias="next")] = None,
) -> Response:
    """Page de connexion. Redirige si une session est déjà ouverte."""
    target = safe_next(next_url)
    if user is not None:
        return RedirectResponse(target, status_code=HTTP_303_SEE_OTHER)
    return _render_login(request, next_url=target)


@router.post("/login")
def login_submit(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    """Ouvre une session et pose le cookie."""
    target = safe_next(next_url)
    try:
        user = auth.authenticate(db, email, password)
    except RateLimitedError as exc:
        return _render_login(
            request,
            next_url=target,
            email=email,
            error=exc.detail,
            status_code=HTTP_429_TOO_MANY_REQUESTS,
        )
    except AuthenticationError as exc:
        return _render_login(
            request,
            next_url=target,
            email=email,
            error=exc.detail,
            status_code=HTTP_401_UNAUTHORIZED,
        )

    token = auth.create_session(
        db,
        user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    response = RedirectResponse(target, status_code=HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().is_production,
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, db: DbSession) -> Response:
    """Révoque la session courante et efface le cookie."""
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if token:
        auth.revoke_session(db, token)

    response = RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
    return response
