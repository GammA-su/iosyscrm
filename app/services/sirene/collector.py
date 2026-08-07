"""Orchestration synchrone de la collecte SIRENE — section 5.2."""

import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.logging import get_logger
from app.models.collector import CollectorRun
from app.repositories import collector as collector_repo
from app.repositories import company as company_repo
from app.services.sirene.client import SireneClient
from app.services.sirene.parser import SIRENE_TIMEZONE, CompanyPayload, parse_etablissement

logger = get_logger(__name__)

LAST_DISPOSITION_WATERMARK: Final = "sirene.last_disposition"
LAST_TRAITEMENT_WATERMARK: Final = "sirene.last_traitement"
SYNC_RUN_KIND: Final = "sirene_sync"
BACKFILL_RUN_KIND: Final = "sirene_backfill"
COLLECTION_AVAILABLE: Final = "UP"
INITIAL_SYNC_DAYS: Final = 7
COLLECTOR_BATCH_SIZE: Final = 500


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalise un instant pour les watermarks et les requêtes SIRENE."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _format_query_datetime(value: datetime) -> str:
    """Horodatage local SIRENE, sans offset, avec les millisecondes de l'API."""
    local = _as_utc(value).astimezone(SIRENE_TIMEZONE).replace(tzinfo=None)
    return local.isoformat(timespec="milliseconds")


def _parse_watermark(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(parsed)


def _department_clause(departments: list[str]) -> str | None:
    if not departments:
        return None
    prefixes = " OR ".join(f"codeCommuneEtablissement:{department}*" for department in departments)
    return f"({prefixes})"


def _sync_query(
    *,
    window_start: datetime,
    window_end: datetime,
    creation_start: date,
    creation_end: date,
    departments: list[str],
) -> str:
    clauses = [
        "periode(etatAdministratifEtablissement:A)",
        "etablissementSiege:true",
        "dateDernierTraitementEtablissement:"
        f"[{_format_query_datetime(window_start)} TO {_format_query_datetime(window_end)}]",
        f"dateCreationEtablissement:[{creation_start.isoformat()} TO {creation_end.isoformat()}]",
    ]
    department_clause = _department_clause(departments)
    if department_clause is not None:
        clauses.append(department_clause)
    return " AND ".join(clauses)


def _backfill_query(*, start: date, end: date, departments: list[str]) -> str:
    clauses = [
        "periode(etatAdministratifEtablissement:A)",
        "etablissementSiege:true",
        f"dateCreationEtablissement:[{start.isoformat()} TO {end.isoformat()}]",
    ]
    department_clause = _department_clause(departments)
    if department_clause is not None:
        clauses.append(department_clause)
    return " AND ".join(clauses)


class SireneCollector:
    """Collecteur incrémental et rattrapage SIRENE."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: SireneClient | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or SireneClient(self._settings)
        self._now = now

    def _is_excluded(self, payload: CompanyPayload) -> bool:
        activity = payload.activite_principale
        return activity is not None and activity[:2] in self._settings.SIRENE_NAF_EXCLUDE

    def _api_calls_since(self, request_count: int) -> int:
        return self._client.request_count - request_count

    def _finish_zero_run(
        self,
        db: Session,
        *,
        kind: str,
        reason: str,
    ) -> CollectorRun:
        run = collector_repo.create_run(db, kind=kind)
        collector_repo.finish_run(
            db,
            run,
            status="success",
            finished_at=self._now(),
        )
        db.commit()
        logger.info("sirene.collection_skipped", reason=reason, run_id=run.id)
        return run

    def _mark_failed(
        self,
        db: Session,
        *,
        kind: str,
        run_id: int | None,
        records_seen: int,
        records_new: int,
        records_updated: int,
        records_rejected: int = 0,
        api_calls: int,
        trace: str,
    ) -> None:
        db.rollback()
        run = collector_repo.get_run(db, run_id) if run_id is not None else None
        if run is None:
            run = collector_repo.create_run(db, kind=kind)
        collector_repo.finish_run(
            db,
            run,
            status="failed",
            records_seen=records_seen,
            records_new=records_new,
            records_updated=records_updated,
            records_rejected=records_rejected,
            api_calls=api_calls,
            error=trace,
            finished_at=self._now(),
        )
        db.commit()
        logger.error("sirene.collection_failed", run_id=run.id, kind=kind)

    def _collect(
        self,
        db: Session,
        *,
        run: CollectorRun,
        query: str,
        request_count: int,
        watermarks: tuple[str, str] | None,
    ) -> CollectorRun:
        run_id = run.id
        run_kind = run.kind
        records_seen = 0
        records_new = 0
        records_updated = 0
        records_rejected = 0
        pending: list[CompanyPayload] = []

        try:
            for raw in self._client.iter_siret(query):
                records_seen += 1
                payload = parse_etablissement(raw)
                if self._is_excluded(payload):
                    continue

                # Une ligne incomplète ne doit pas faire échouer l'INSERT des
                # 499 autres du lot. Elle est écartée, mais jamais en silence.
                missing = payload.missing_required_fields
                if missing:
                    records_rejected += 1
                    logger.warning(
                        "sirene.payload_rejected",
                        run_id=run_id,
                        siret=payload.siret_siege,
                        missing_fields=list(missing),
                    )
                    continue

                # Le lot précédent n'est validé qu'une fois qu'un prochain
                # payload éligible existe. Le dernier lot reste ainsi dans la
                # transaction qui recevra aussi les deux watermarks.
                if len(pending) == COLLECTOR_BATCH_SIZE:
                    result = company_repo.upsert_many(db, pending)
                    records_new += result.inserted
                    records_updated += result.updated
                    run.records_seen = records_seen
                    run.records_new = records_new
                    run.records_updated = records_updated
                    run.records_rejected = records_rejected
                    run.api_calls = self._api_calls_since(request_count)
                    db.commit()
                    pending = []

                pending.append(payload)

            if pending:
                result = company_repo.upsert_many(db, pending)
                records_new += result.inserted
                records_updated += result.updated

            if watermarks is not None:
                last_traitement, last_disposition = watermarks
                collector_repo.set_watermark(
                    db,
                    LAST_TRAITEMENT_WATERMARK,
                    last_traitement,
                )
                collector_repo.set_watermark(
                    db,
                    LAST_DISPOSITION_WATERMARK,
                    last_disposition,
                )

            collector_repo.finish_run(
                db,
                run,
                status="success",
                records_seen=records_seen,
                records_new=records_new,
                records_updated=records_updated,
                records_rejected=records_rejected,
                api_calls=self._api_calls_since(request_count),
                finished_at=self._now(),
            )
            db.commit()
            logger.info(
                "sirene.collection_succeeded",
                run_id=run.id,
                kind=run.kind,
                records_seen=records_seen,
                records_new=records_new,
                records_updated=records_updated,
                records_rejected=records_rejected,
            )
            return run
        except Exception:
            self._mark_failed(
                db,
                kind=run_kind,
                run_id=run_id,
                records_seen=records_seen,
                records_new=records_new,
                records_updated=records_updated,
                records_rejected=records_rejected,
                api_calls=self._api_calls_since(request_count),
                trace=traceback.format_exc(),
            )
            raise

    def sync(self, db: Session) -> CollectorRun:
        """Synchronise les établissements modifiés depuis le dernier watermark."""
        run_id: int | None = None
        try:
            informations = self._client.get_informations()
            request_count = self._client.request_count
            if informations.etat_collection != COLLECTION_AVAILABLE:
                return self._finish_zero_run(
                    db,
                    kind=SYNC_RUN_KIND,
                    reason=f"collection_state={informations.etat_collection!r}",
                )

            disposition = _serialize_datetime(informations.date_derniere_mise_a_disposition)
            if collector_repo.get_watermark(db, LAST_DISPOSITION_WATERMARK) == disposition:
                return self._finish_zero_run(
                    db,
                    kind=SYNC_RUN_KIND,
                    reason="unchanged_disposition",
                )

            run = collector_repo.create_run(db, kind=SYNC_RUN_KIND)
            run_id = run.id
            db.commit()

            now = _as_utc(self._now())
            upper_bound = informations.date_dernier_traitement_maximum or now
            upper_bound = _as_utc(upper_bound)
            last_traitement = collector_repo.get_watermark(db, LAST_TRAITEMENT_WATERMARK)
            window_start = (
                _parse_watermark(last_traitement) - timedelta(days=1)
                if last_traitement is not None
                else now - timedelta(days=INITIAL_SYNC_DAYS)
            )
            today = now.astimezone(SIRENE_TIMEZONE).date()
            query = _sync_query(
                window_start=window_start,
                window_end=upper_bound,
                creation_start=today - timedelta(days=self._settings.SIRENE_LOOKBACK_DAYS),
                creation_end=today,
                departments=self._settings.SIRENE_DEPARTEMENTS,
            )

            run.window_start = window_start
            run.window_end = upper_bound
            db.commit()
        except Exception:
            self._mark_failed(
                db,
                kind=SYNC_RUN_KIND,
                run_id=run_id,
                records_seen=0,
                records_new=0,
                records_updated=0,
                api_calls=0,
                trace=traceback.format_exc(),
            )
            raise

        return self._collect(
            db,
            run=run,
            query=query,
            request_count=request_count,
            watermarks=(_serialize_datetime(upper_bound), disposition),
        )

    def backfill(self, db: Session, days: int) -> CollectorRun:
        """Recharge une fenêtre de dates de création sans lire ni écrire de watermark."""
        if days <= 0:
            raise ValueError("Le nombre de jours du backfill doit être strictement positif.")

        now = _as_utc(self._now())
        end = now.astimezone(SIRENE_TIMEZONE).date()
        start = end - timedelta(days=days)
        run = collector_repo.create_run(
            db,
            kind=BACKFILL_RUN_KIND,
            window_start=datetime.combine(start, time.min, tzinfo=SIRENE_TIMEZONE),
            window_end=datetime.combine(end, time.min, tzinfo=SIRENE_TIMEZONE),
        )
        db.commit()
        query = _backfill_query(
            start=start,
            end=end,
            departments=self._settings.SIRENE_DEPARTEMENTS,
        )
        return self._collect(
            db,
            run=run,
            query=query,
            request_count=self._client.request_count,
            watermarks=None,
        )
