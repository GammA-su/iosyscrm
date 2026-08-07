"""Persistance des watermarks et exécutions du collecteur SIRENE."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.collector import CollectorRun, CollectorWatermark


def get_watermark(db: Session, key: str) -> str | None:
    """Valeur d'un watermark, ou `None` s'il n'existe pas."""
    return db.execute(
        select(CollectorWatermark.value).where(CollectorWatermark.key == key)
    ).scalar_one_or_none()


def set_watermark(db: Session, key: str, value: str) -> None:
    """Crée ou remplace un watermark dans la transaction courante."""
    statement = insert(CollectorWatermark).values(key=key, value=value)
    statement = statement.on_conflict_do_update(
        index_elements=[CollectorWatermark.key],
        set_={"value": statement.excluded.value, "updated_at": func.now()},
    )
    db.execute(statement)


def create_run(
    db: Session,
    *,
    kind: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> CollectorRun:
    """Ouvre un run en statut `running`."""
    run = CollectorRun(
        kind=kind,
        status="running",
        window_start=window_start,
        window_end=window_end,
    )
    db.add(run)
    db.flush()
    return run


def finish_run(
    db: Session,
    run: CollectorRun,
    *,
    status: str,
    records_seen: int = 0,
    records_new: int = 0,
    records_updated: int = 0,
    records_rejected: int = 0,
    api_calls: int = 0,
    error: str | None = None,
    finished_at: datetime | None = None,
) -> CollectorRun:
    """Clôture un run et fixe ses compteurs dans la transaction courante."""
    run.status = status
    run.records_seen = records_seen
    run.records_new = records_new
    run.records_updated = records_updated
    run.records_rejected = records_rejected
    run.api_calls = api_calls
    run.error = error
    run.finished_at = finished_at or datetime.now(tz=UTC)
    db.flush()
    return run


def get_run(db: Session, run_id: int) -> CollectorRun | None:
    """Run par identifiant, ou `None`."""
    return db.get(CollectorRun, run_id)


def list_runs(db: Session, limit: int = 10) -> list[CollectorRun]:
    """Derniers runs, du plus récent au plus ancien."""
    statement = select(CollectorRun).order_by(
        CollectorRun.started_at.desc(), CollectorRun.id.desc()
    )
    return list(db.execute(statement.limit(limit)).scalars())
