"""Orchestration de l'enrichissement — sections 6.1 et 6.6.

Les fournisseurs sont exécutés dans l'ordre du tableau 6.1 et sont isolés les
uns des autres : l'échec de l'un est tracé dans `enrichment_runs` et n'empêche
pas les suivants de s'exécuter.
"""

import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database.engine import get_session_factory
from app.logging import get_logger
from app.models.company import Company
from app.models.enrichment import EnrichmentRun
from app.repositories import company as company_repo
from app.repositories import contact as contact_repo
from app.repositories import enrichment as enrichment_repo
from app.services.enrichment.fields import EnrichmentField, is_known_field
from app.services.enrichment.providers.base import (
    ContactCandidate,
    EnrichmentProvider,
    FactCandidate,
    ProviderContext,
)
from app.services.enrichment.providers.contact_extract import ContactExtractProvider
from app.services.enrichment.providers.societeinfo import SocieteInfoProvider
from app.services.enrichment.providers.website_probe import WebsiteProbeProvider
from app.services.scoring import rescore_company

logger = get_logger(__name__)

RUN_STATUS_SUCCESS: Final = "success"
RUN_STATUS_FAILED: Final = "failed"

ProviderFactory = Callable[[], Sequence[EnrichmentProvider]]


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """Bilan d'un lot d'enrichissement."""

    selected: int = 0
    succeeded: int = 0
    failed: int = 0
    runs: list[EnrichmentRun] = field(default_factory=list)


def default_provider_factory(settings: Settings) -> ProviderFactory:
    """Fabrique les trois fournisseurs de la section 6.1, dans l'ordre.

    Une instance neuve par entreprise : les fournisseurs portent des caches
    (robots.txt, cadencement par domaine) qui n'ont pas à être partagés entre
    threads.
    """

    def build() -> Sequence[EnrichmentProvider]:
        return (
            SocieteInfoProvider(settings),
            WebsiteProbeProvider(settings),
            ContactExtractProvider(settings),
        )

    return build


class EnrichmentOrchestrator:
    """Enchaîne les fournisseurs et persiste faits et contacts."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider_factory: ProviderFactory | None = None,
        session_factory: sessionmaker[Session] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._settings = settings or get_settings()
        self._provider_factory = provider_factory or default_provider_factory(self._settings)
        self._session_factory = session_factory
        self._now = now

    # --- Écriture ------------------------------------------------------

    def write_fact(
        self,
        db: Session,
        *,
        company_id: int,
        candidate: FactCandidate,
        source: str,
        confidence: float,
    ) -> None:
        """Écrit un fait, après validation du vocabulaire de la section 3.4.

        La colonne `field` ne porte aucune contrainte SQL : sans ce contrôle,
        une faute de frappe d'un fournisseur créerait un champ fantôme que
        plus personne ne relirait jamais.
        """
        if not is_known_field(candidate.field):
            raise ValueError(
                f"Champ d'enrichissement inconnu : {candidate.field!r}. "
                f"Vocabulaire autorisé : {', '.join(sorted(EnrichmentField))}."
            )

        collected_at = self._now()
        enrichment_repo.upsert_fact(
            db,
            company_id=company_id,
            field=str(candidate.field),
            value=candidate.value,
            value_json=candidate.value_json,
            source=source,
            confidence=candidate.confidence if candidate.confidence is not None else confidence,
            collected_at=collected_at,
            expires_at=collected_at + timedelta(days=self._settings.ENRICHMENT_TTL_DAYS),
        )

    def _write_contacts(
        self, db: Session, company_id: int, contacts: Sequence[ContactCandidate]
    ) -> None:
        for contact in contacts:
            contact_repo.upsert_contact(
                db,
                company_id=company_id,
                channel=contact.channel,
                value=contact.value,
                display_value=contact.display_value,
                origin=contact.origin,
                confidence=contact.confidence,
                is_generic=contact.is_generic,
                is_primary=contact.is_primary,
            )

    # --- Une entreprise -------------------------------------------------

    def _build_context(self, db: Session, company: Company) -> ProviderContext:
        known_url = enrichment_repo.get_valid_fact(db, company.id, EnrichmentField.WEBSITE_URL)
        return ProviderContext(website_url=known_url.value if known_url is not None else None)

    def enrich_company(self, db: Session, company: Company) -> list[EnrichmentRun]:
        """Exécute TOUS les fournisseurs, dans l'ordre. Un run par fournisseur.

        Aucun fournisseur n'est sauté au motif que l'information serait déjà
        connue : la règle d'arrêt anticipé de la section 6.1 vise la redondance
        au sein d'une exécution, pas un verrou permanent. Un contact
        societeinfo obsolète, ou saisi à la main, empêcherait sinon
        définitivement toute extraction depuis le site. La redondance est
        traitée par l'unicité `uq_contact` et par le TTL des faits.
        """
        context = self._build_context(db, company)
        runs: list[EnrichmentRun] = []

        for provider in self._provider_factory():
            run = enrichment_repo.create_run(db, company_id=company.id, provider=provider.name)
            db.commit()
            runs.append(run)
            already_written = len(context.contacts)

            try:
                facts = provider.run(company, context)
                for candidate in facts:
                    self.write_fact(
                        db,
                        company_id=company.id,
                        candidate=candidate,
                        source=provider.name,
                        confidence=provider.confidence,
                    )
                self._write_contacts(db, company.id, context.contacts[already_written:])
                enrichment_repo.finish_run(
                    db,
                    run,
                    status=RUN_STATUS_SUCCESS,
                    facts_written=len(facts),
                    finished_at=self._now(),
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.error(
                    "enrichment.provider_failed", provider=provider.name, siren=company.siren
                )
                enrichment_repo.finish_run(
                    db,
                    run,
                    status=RUN_STATUS_FAILED,
                    error=traceback.format_exc(),
                    finished_at=self._now(),
                )
                db.commit()

        if any(run.status == RUN_STATUS_SUCCESS for run in runs):
            self._rescore(db, company)
        return runs

    def _rescore(self, db: Session, company: Company) -> None:
        """Recalcule le score après enrichissement (section 7.1).

        Un échec du scoring ne remet pas en cause l'enrichissement, déjà
        validé : il est tracé et l'appelant récupère ses runs.
        """
        try:
            rescore_company(db, company.id)
            db.commit()
        except Exception:
            db.rollback()
            logger.error("enrichment.rescore_failed", siren=company.siren)

    # --- Un lot ---------------------------------------------------------

    def select_batch(self, db: Session, size: int) -> list[Company]:
        """Sélection par priorité de la section 6.6, sans doublon.

        1. aucune donnée, 2. faits expirés des prospects actifs, 3. le reste.
        Les unités en diffusion partielle sont exclues en amont, dans
        `company_repo` : elles ne sont pas prospectables (R123-232-1).
        """
        selected: list[Company] = []
        seen: set[int] = set()

        for candidates in (
            company_repo.list_without_facts(db, size),
            company_repo.list_with_expired_facts(db, size, active_prospects_only=True),
            company_repo.list_with_expired_facts(db, size, active_prospects_only=False),
        ):
            for company in candidates:
                if len(selected) >= size:
                    return selected
                if company.id not in seen:
                    seen.add(company.id)
                    selected.append(company)
        return selected

    def _enrich_in_own_session(self, company_id: int) -> list[EnrichmentRun]:
        """Enrichit une entreprise dans une session dédiée au thread courant."""
        factory = self._session_factory or get_session_factory()
        session = factory()
        try:
            company = session.get(Company, company_id)
            if company is None:
                return []
            return self.enrich_company(session, company)
        finally:
            session.close()

    def enrich_batch(self, db: Session, size: int | None = None) -> BatchSummary:
        """Enrichit un lot, en parallèle, une session SQLAlchemy par thread."""
        if not self._settings.ENRICHMENT_ENABLED:
            logger.info("enrichment.disabled")
            return BatchSummary()

        batch_size = size or self._settings.ENRICHMENT_BATCH_SIZE
        company_ids = [company.id for company in self.select_batch(db, batch_size)]
        if not company_ids:
            return BatchSummary()

        runs: list[EnrichmentRun] = []
        workers = min(self._settings.ENRICHMENT_MAX_WORKERS, len(company_ids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for company_runs in pool.map(self._enrich_in_own_session, company_ids):
                runs.extend(company_runs)

        succeeded = sum(1 for run in runs if run.status == RUN_STATUS_SUCCESS)
        return BatchSummary(
            selected=len(company_ids),
            succeeded=succeeded,
            failed=len(runs) - succeeded,
            runs=runs,
        )
