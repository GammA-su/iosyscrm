"""Configuration des logs (structlog).

Console lisible en développement, JSON en production. Un contextvar `run_id`
est injecté dans tous les événements : c'est ce qui permet de corréler les
lignes d'une même collecte ou d'un même job.
"""

import logging
from contextvars import ContextVar, Token
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from app.config import Settings, get_settings

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


def bind_run_id(run_id: str) -> Token[str | None]:
    """Associe un `run_id` au contexte courant. Le jeton permet de le restaurer."""
    return run_id_var.set(run_id)


def reset_run_id(token: Token[str | None]) -> None:
    """Restaure la valeur précédente du `run_id`."""
    run_id_var.reset(token)


def add_run_id(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processeur structlog : injecte le `run_id` courant s'il est défini."""
    run_id = run_id_var.get()
    if run_id is not None:
        event_dict["run_id"] = run_id
    return event_dict


def _resolve_level(name: str) -> int:
    level = logging.getLevelNamesMapping().get(name.strip().upper())
    return level if level is not None else logging.INFO


def configure_logging(settings: Settings | None = None) -> None:
    """Configure structlog et la journalisation standard."""
    settings = settings or get_settings()
    level = _resolve_level(settings.APP_LOG_LEVEL)

    logging.basicConfig(format="%(message)s", level=level, force=True)

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_run_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Retourne un logger structlog lié."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name, **initial_values)
    return logger
