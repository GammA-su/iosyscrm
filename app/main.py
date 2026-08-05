"""Création de l'application FastAPI.

Les endpoints sont volontairement synchrones (`def`, pas `async def`) :
FastAPI les exécute dans un pool de threads (section 1.2).
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from app import __version__
from app.config import get_settings
from app.deps import get_db
from app.exceptions import register_exception_handlers
from app.logging import configure_logging, get_logger

STATIC_DIR = Path(__file__).parent / "static"

DbSession = Annotated[Session, Depends(get_db)]

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Construit l'application FastAPI."""
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="IOSYS Prospect CRM",
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    register_exception_handlers(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    def health(db: DbSession, response: Response) -> dict[str, str]:
        """Sonde de vivacité : vérifie la connexion PostgreSQL."""
        db_state = "ok"
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            db_state = "error"
            logger.warning("health.db_unreachable", error=str(exc))

        status = "ok" if db_state == "ok" else "degraded"
        if status != "ok":
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return {"status": status, "db": db_state, "version": __version__}

    return app
