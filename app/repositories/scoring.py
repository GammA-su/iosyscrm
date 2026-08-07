"""Accès aux règles et aux historiques de score. Aucune règle métier ici."""

from typing import Any

from sqlalchemy import (
    Row,
    Select,
    String,
    TableClause,
    cast,
    column,
    func,
    insert,
    select,
    table,
    text,
)
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Contact
from app.models.scoring import ScoreSnapshot, ScoringRule

#: La vue `company_facts` n'est pas cartographiée dans `Base.metadata`
#: (Alembic ne sait pas la comparer) : on la référence explicitement.
company_facts: TableClause = table(
    "company_facts",
    column("company_id"),
    column("field"),
    column("value"),
    column("value_json"),
)


def list_active_rules(db: Session) -> list[ScoringRule]:
    """Règles actives, dans un ordre stable."""
    statement = select(ScoringRule).where(ScoringRule.is_active.is_(True)).order_by(ScoringRule.key)
    return list(db.execute(statement).scalars())


def _scoring_select() -> Select[tuple[Company, dict[str, Any], list[str]]]:
    """Entreprise, faits valides agrégés et canaux de contact, en une requête.

    Les faits et les canaux sont agrégés par des sous-requêtes corrélées :
    interroger `company_facts` entreprise par entreprise ferait s'effondrer le
    job nocturne.
    """
    facts = (
        select(
            func.coalesce(
                func.jsonb_object_agg(
                    company_facts.c.field,
                    func.coalesce(
                        company_facts.c.value_json,
                        func.to_jsonb(company_facts.c.value),
                        text("'null'::jsonb"),
                    ),
                ),
                text("'{}'::jsonb"),
            )
        )
        .select_from(company_facts)
        .where(company_facts.c.company_id == Company.id)
        .scalar_subquery()
    )
    channels = (
        select(
            func.coalesce(
                func.array_agg(cast(Contact.channel, String).distinct()),
                text("'{}'::text[]"),
            )
        )
        .select_from(Contact)
        .where(Contact.company_id == Company.id)
        .scalar_subquery()
    )

    return select(Company, facts.label("facts"), channels.label("channels"))


def list_scoring_rows(
    db: Session, *, after_id: int = 0, limit: int = 1000
) -> list[Row[tuple[Company, dict[str, Any], list[str]]]]:
    """Un lot d'entreprises prêtes à scorer. **Une seule requête.**

    La pagination est par clé (`id > after_id`) et non par `OFFSET` : le coût
    reste constant quel que soit le rang du lot.
    """
    statement = _scoring_select().where(Company.id > after_id).order_by(Company.id).limit(limit)
    return list(db.execute(statement).all())


def get_scoring_row(
    db: Session, company_id: int
) -> Row[tuple[Company, dict[str, Any], list[str]]] | None:
    """Contexte de scoring d'une seule entreprise, ou `None`."""
    statement = _scoring_select().where(Company.id == company_id)
    return db.execute(statement).one_or_none()


def bulk_insert_snapshots(db: Session, snapshots: list[dict[str, Any]]) -> int:
    """Insère les instantanés en une seule instruction. Renvoie leur nombre."""
    if not snapshots:
        return 0
    db.execute(insert(ScoreSnapshot), snapshots)
    return len(snapshots)


def latest_scores(db: Session, company_ids: list[int]) -> list[ScoreSnapshot]:
    """Dernier instantané de chaque entreprise, via `DISTINCT ON`."""
    if not company_ids:
        return []
    statement = (
        select(ScoreSnapshot)
        .distinct(ScoreSnapshot.company_id)
        .where(ScoreSnapshot.company_id.in_(company_ids))
        .order_by(ScoreSnapshot.company_id, ScoreSnapshot.computed_at.desc())
    )
    return list(db.execute(statement).scalars())
