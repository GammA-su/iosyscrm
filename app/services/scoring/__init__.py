"""Moteur de scoring — section 7.

Le score est piloté par les données : modifier une pondération se fait en base,
jamais par un déploiement. Il est recalculé par un job nocturne et à la fin de
chaque enrichissement (section 7.1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Row
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.company import Company
from app.repositories import scoring as scoring_repo
from app.services.scoring.engine import (
    RuleSpec,
    ScoreResult,
    ScoringContext,
    compile_rules,
    compute_score,
    ruleset_hash,
)

logger = get_logger(__name__)

DEFAULT_BATCH_SIZE: Final = 1000

ScoringRow = Row[tuple[Company, dict[str, Any], list[str]]]


@dataclass(frozen=True, slots=True)
class RescoreSummary:
    """Bilan d'un recalcul complet."""

    companies: int
    ruleset_hash: str


def load_rules(db: Session) -> list[RuleSpec]:
    """Règles actives, validées. Lève sur un `predicate` inconnu (section 7.2)."""
    return compile_rules(scoring_repo.list_active_rules(db))


def _context(row: ScoringRow, today: Any) -> ScoringContext:
    company, facts, channels = row
    return ScoringContext(
        company=company,
        facts=dict(facts or {}),
        contact_channels=frozenset(channels or ()),
        today=today,
    )


def _snapshot(
    company_id: int, result: ScoreResult, digest: str, computed_at: datetime
) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "score": result.score,
        "breakdown": result.breakdown,
        "ruleset_hash": digest,
        "computed_at": computed_at,
    }


def rescore_all(db: Session, batch_size: int = DEFAULT_BATCH_SIZE) -> RescoreSummary:
    """Recalcule tous les scores, par lots.

    Deux requêtes par lot : une lecture du contexte, une insertion groupée.
    Le nombre de requêtes ne dépend donc pas du nombre d'entreprises.
    """
    if batch_size <= 0:
        raise ValueError("La taille de lot doit être strictement positive.")

    rules = load_rules(db)
    digest = ruleset_hash(rules)
    computed_at = datetime.now(tz=UTC)
    today = computed_at.date()

    total = 0
    last_id = 0
    while True:
        rows = scoring_repo.list_scoring_rows(db, after_id=last_id, limit=batch_size)
        if not rows:
            break

        snapshots = [
            _snapshot(row[0].id, compute_score(_context(row, today), rules), digest, computed_at)
            for row in rows
        ]
        # Le curseur est relevé AVANT le commit : après, les instances sont
        # expirées et lire `id` déclencherait une requête de rafraîchissement.
        last_id = rows[-1][0].id
        total += len(rows)

        scoring_repo.bulk_insert_snapshots(db, snapshots)
        db.commit()

    logger.info("scoring.rescore_all", companies=total, ruleset_hash=digest)
    return RescoreSummary(companies=total, ruleset_hash=digest)


def rescore_company(db: Session, company_id: int) -> ScoreResult | None:
    """Recalcule le score d'une entreprise. `None` si elle n'existe pas.

    L'instantané est écrit dans la transaction courante, sans `commit` :
    l'appelant décide du moment où il valide.
    """
    row = scoring_repo.get_scoring_row(db, company_id)
    if row is None:
        logger.warning("scoring.company_not_found", company_id=company_id)
        return None

    rules = load_rules(db)
    computed_at = datetime.now(tz=UTC)
    result = compute_score(_context(row, computed_at.date()), rules)
    scoring_repo.bulk_insert_snapshots(
        db, [_snapshot(company_id, result, ruleset_hash(rules), computed_at)]
    )
    return result
