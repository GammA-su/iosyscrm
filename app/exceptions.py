"""Exceptions métier et gestionnaires FastAPI.

Toutes les erreurs sont rendues au format `{"detail": str, "code": str}`
(section 10).
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
)


class AppError(Exception):
    """Erreur applicative portant un code stable et un statut HTTP."""

    code: str = "app_error"
    http_status: int = HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class NotFoundError(AppError):
    """Ressource inexistante."""

    code = "not_found"
    http_status = HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """État incompatible avec l'opération demandée."""

    code = "conflict"
    http_status = HTTP_409_CONFLICT


class ValidationError(AppError):
    """Donnée métier invalide."""

    code = "validation_error"
    http_status = HTTP_422_UNPROCESSABLE_CONTENT


class AuthenticationError(AppError):
    """Session absente, invalide, expirée, ou compte désactivé (section 9)."""

    code = "unauthenticated"
    http_status = HTTP_401_UNAUTHORIZED


class PermissionDeniedError(AppError):
    """Rôle insuffisant pour l'opération demandée (section 9)."""

    code = "forbidden"
    http_status = HTTP_403_FORBIDDEN


class RateLimitedError(AppError):
    """Trop de tentatives de connexion échouées (section 9)."""

    code = "too_many_attempts"
    http_status = HTTP_429_TOO_MANY_REQUESTS


class ExternalServiceError(AppError):
    """Échec d'un service tiers (SIRENE, societeinfo, SMTP…)."""

    code = "external_service_error"
    http_status = HTTP_502_BAD_GATEWAY


_HTTP_ERROR_CODES: dict[int, str] = {
    HTTP_400_BAD_REQUEST: "bad_request",
    HTTP_401_UNAUTHORIZED: "unauthenticated",
    HTTP_403_FORBIDDEN: "forbidden",
    HTTP_404_NOT_FOUND: "not_found",
    HTTP_429_TOO_MANY_REQUESTS: "too_many_attempts",
    HTTP_409_CONFLICT: "conflict",
    HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
    HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
}


def app_error_handler(_request: Request, exc: Exception) -> Response:
    """Rend une `AppError` au format d'erreur du projet."""
    if not isinstance(exc, AppError):  # pragma: no cover - garde de typage
        raise exc
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": exc.detail, "code": exc.code},
    )


def http_exception_handler(_request: Request, exc: Exception) -> Response:
    """Aligne les `HTTPException` de Starlette sur le même format."""
    if not isinstance(exc, HTTPException):  # pragma: no cover - garde de typage
        raise exc
    code = _HTTP_ERROR_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": code},
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Enregistre les gestionnaires d'exception sur l'application."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
