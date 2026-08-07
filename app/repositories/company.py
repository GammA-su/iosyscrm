"""Accès au référentiel SIRENE, sans règle de collecte ni d'enrichissement."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import Boolean, Select, exists, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.enrichment import EnrichmentFact
from app.models.pipeline import PipelineStage
from app.models.prospect import Prospect

if TYPE_CHECKING:
    from app.services.sirene.parser import CompanyPayload

UPSERT_BATCH_SIZE: Final = 500


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """Nombre d'entreprises créées et mises à jour par un upsert."""

    inserted: int = 0
    updated: int = 0

    def __add__(self, other: UpsertResult) -> UpsertResult:
        return UpsertResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
        )


def _chunks(payloads: list[CompanyPayload]) -> Iterator[list[CompanyPayload]]:
    """Découpe les payloads selon la taille de lot imposée par la section 5.2."""
    for start in range(0, len(payloads), UPSERT_BATCH_SIZE):
        yield payloads[start : start + UPSERT_BATCH_SIZE]


def _upsert_batch(db: Session, payloads: list[CompanyPayload]) -> UpsertResult:
    """Exécute un unique INSERT PostgreSQL pour un lot non vide."""
    values: list[dict[str, Any]] = [asdict(payload) for payload in payloads]
    statement = insert(Company).values(values)
    mutable_fields = (field_name for field_name in values[0] if field_name != "siren")
    update_values: dict[str, Any] = {
        field_name: getattr(statement.excluded, field_name) for field_name in mutable_fields
    }
    update_values["last_synced_at"] = func.now()

    upsert_statement = statement.on_conflict_do_update(
        index_elements=[Company.siren],
        set_=update_values,
    ).returning(literal_column("xmax = 0", Boolean).label("inserted"))

    flags = [bool(row.inserted) for row in db.execute(upsert_statement)]
    inserted = sum(flags)
    return UpsertResult(inserted=inserted, updated=len(flags) - inserted)


def upsert_many(db: Session, payloads: list[CompanyPayload]) -> UpsertResult:
    """Insère ou actualise des entreprises par lots PostgreSQL de 500.

    PostgreSQL expose `xmax = 0` dans `RETURNING` pour distinguer les lignes
    insérées de celles passées par la branche `DO UPDATE`.
    """
    result = UpsertResult()
    for batch in _chunks(payloads):
        result += _upsert_batch(db, batch)
    return result


def get_by_siren(db: Session, siren: str) -> Company | None:
    """Entreprise par SIREN exact, ou `None`."""
    return db.execute(select(Company).where(Company.siren == siren)).scalar_one_or_none()


def list_for_enrichment(db: Session, limit: int = 50) -> list[Company]:
    """Entreprises prospectables candidates à l'enrichissement automatique.

    L'exclusion des unités en `statut_diffusion = 'P'` n'est PAS une
    optimisation : l'article R123-232-1 du code de commerce interdit
    l'utilisation des unités non diffusibles à des fins de prospection. Ce
    filtre ne doit être ni retiré ni contourné, quelles que soient les
    évolutions de la priorisation.

    Le prédicat et l'ordre reprennent exactement ceux de l'index partiel
    `idx_companies_prospectables`, pour qu'il soit effectivement utilisé.

    La priorisation par présence et expiration des faits appartient au lot T5.
    """
    return list(db.execute(_prospectable().limit(limit)).scalars())


def _prospectable() -> Select[tuple[Company]]:
    """Socle commun des sélections d'enrichissement, aligné sur l'index partiel."""
    return (
        select(Company)
        .where(Company.etat_administratif == "A", Company.statut_diffusion == "O")
        .order_by(Company.date_creation.desc())
    )


def list_without_facts(db: Session, limit: int) -> list[Company]:
    """Entreprises prospectables ne portant aucun fait (priorité 1 de 6.6)."""
    never_enriched = ~exists().where(EnrichmentFact.company_id == Company.id)
    return list(db.execute(_prospectable().where(never_enriched).limit(limit)).scalars())


def list_with_expired_facts(
    db: Session,
    limit: int,
    *,
    active_prospects_only: bool,
) -> list[Company]:
    """Entreprises prospectables portant au moins un fait expiré (6.6, 2 et 3).

    Un prospect est « actif » tant que son étape n'est ni gagnée ni perdue :
    rafraîchir les données d'une affaire close n'a aucune valeur commerciale.
    """
    has_expired_fact = (
        exists()
        .where(EnrichmentFact.company_id == Company.id)
        .where(EnrichmentFact.expires_at <= func.now())
    )
    statement = _prospectable().where(has_expired_fact)
    if active_prospects_only:
        active_prospect = (
            exists()
            .where(Prospect.company_id == Company.id)
            .where(Prospect.stage_id == PipelineStage.id)
            .where(PipelineStage.is_won.is_(False))
            .where(PipelineStage.is_lost.is_(False))
        )
        statement = statement.where(active_prospect)
    return list(db.execute(statement.limit(limit)).scalars())
