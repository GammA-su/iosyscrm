"""Accès aux faits d'enrichissement et à leurs exécutions.

Aucune règle métier : ni TTL, ni vocabulaire, ni ordre des fournisseurs.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.enrichment import EnrichmentFact, EnrichmentRun


def upsert_fact(
    db: Session,
    *,
    company_id: int,
    field: str,
    value: str | None,
    value_json: Any | None,
    source: str,
    confidence: float,
    collected_at: datetime,
    expires_at: datetime,
) -> None:
    """Crée ou remplace le fait `(company_id, field, source)`."""
    statement = insert(EnrichmentFact).values(
        company_id=company_id,
        field=field,
        value=value,
        value_json=value_json,
        source=source,
        confidence=confidence,
        collected_at=collected_at,
        expires_at=expires_at,
    )
    db.execute(
        statement.on_conflict_do_update(
            constraint="uq_fact",
            set_={
                "value": statement.excluded.value,
                "value_json": statement.excluded.value_json,
                "confidence": statement.excluded.confidence,
                "collected_at": statement.excluded.collected_at,
                "expires_at": statement.excluded.expires_at,
            },
        )
    )


def list_facts(db: Session, company_id: int) -> list[EnrichmentFact]:
    """Tous les faits d'une entreprise, expirés compris, du plus récent."""
    statement = (
        select(EnrichmentFact)
        .where(EnrichmentFact.company_id == company_id)
        .order_by(EnrichmentFact.field, EnrichmentFact.confidence.desc())
    )
    return list(db.execute(statement).scalars())


def get_valid_fact(db: Session, company_id: int, field: str) -> EnrichmentFact | None:
    """Fait valide de plus haute confiance pour ce champ, ou `None`.

    Même règle de départage que la vue `company_facts` : confiance d'abord,
    fraîcheur ensuite.
    """
    statement = (
        select(EnrichmentFact)
        .where(
            EnrichmentFact.company_id == company_id,
            EnrichmentFact.field == field,
            EnrichmentFact.expires_at > func.now(),
        )
        .order_by(EnrichmentFact.confidence.desc(), EnrichmentFact.collected_at.desc())
        .limit(1)
    )
    return db.execute(statement).scalars().first()


def create_run(db: Session, *, company_id: int, provider: str) -> EnrichmentRun:
    """Ouvre une exécution de fournisseur en statut `running`."""
    run = EnrichmentRun(company_id=company_id, provider=provider, status="running")
    db.add(run)
    db.flush()
    return run


def finish_run(
    db: Session,
    run: EnrichmentRun,
    *,
    status: str,
    facts_written: int = 0,
    error: str | None = None,
    finished_at: datetime,
) -> EnrichmentRun:
    """Clôture une exécution et fixe ses compteurs."""
    run.status = status
    run.facts_written = facts_written
    run.error = error
    run.finished_at = finished_at
    db.flush()
    return run


def list_runs(db: Session, *, limit: int = 10) -> list[EnrichmentRun]:
    """Dernières exécutions, du plus récent au plus ancien."""
    statement = (
        select(EnrichmentRun)
        .order_by(EnrichmentRun.started_at.desc(), EnrichmentRun.id.desc())
        .limit(limit)
    )
    return list(db.execute(statement).scalars())


def count_facts_by_field(db: Session) -> list[tuple[str, int]]:
    """Nombre de faits valides par champ."""
    statement: Select[tuple[str, int]] = (
        select(EnrichmentFact.field, func.count())
        .where(EnrichmentFact.expires_at > func.now())
        .group_by(EnrichmentFact.field)
        .order_by(EnrichmentFact.field)
    )
    return [(field, count) for field, count in db.execute(statement)]


def count_facts_by_source(db: Session) -> list[tuple[str, int]]:
    """Nombre de faits valides par fournisseur."""
    statement: Select[tuple[str, int]] = (
        select(EnrichmentFact.source, func.count())
        .where(EnrichmentFact.expires_at > func.now())
        .group_by(EnrichmentFact.source)
        .order_by(EnrichmentFact.source)
    )
    return [(source, count) for source, count in db.execute(statement)]


def count_expired_facts(db: Session) -> int:
    """Nombre de faits arrivés à expiration."""
    statement = (
        select(func.count())
        .select_from(EnrichmentFact)
        .where(EnrichmentFact.expires_at <= func.now())
    )
    return db.execute(statement).scalar_one()
