"""Entrypoint du conteneur `worker`.

Stub de T0 : le conteneur démarre, journalise et attend. L'ordonnanceur réel
(APScheduler `BlockingScheduler`, jobs de la section 12, verrous consultatifs)
est implémenté en T11.
"""

import time

from app.config import get_settings
from app.logging import configure_logging, get_logger

HEARTBEAT_SECONDS = 60

logger = get_logger(__name__)


def run() -> None:
    """Boucle d'attente en attendant l'implémentation de T11."""
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "scheduler.stub_started",
        enabled=settings.SCHEDULER_ENABLED,
        timezone=settings.SCHEDULER_TIMEZONE,
    )
    try:
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            logger.debug("scheduler.stub_heartbeat")
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("scheduler.stub_stopped")


if __name__ == "__main__":  # pragma: no cover
    run()
