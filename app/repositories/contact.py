"""Accès aux contacts. Aucune normalisation ni règle de conformité ici."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.contact import Contact


def demote_other_primaries(db: Session, *, company_id: int, channel: str, value: str) -> None:
    """Retire le drapeau `is_primary` des autres contacts du même canal.

    `idx_contact_primary` est un index unique partiel : sans cette bascule,
    désigner un nouveau contact principal violerait la contrainte.
    """
    db.execute(
        update(Contact)
        .where(
            Contact.company_id == company_id,
            Contact.channel == channel,
            Contact.value != value,
            Contact.is_primary.is_(True),
        )
        .values(is_primary=False)
    )


def upsert_contact(
    db: Session,
    *,
    company_id: int,
    channel: str,
    value: str,
    display_value: str,
    origin: str,
    confidence: float,
    is_generic: bool = False,
    is_primary: bool = False,
) -> None:
    """Crée le contact, ou le remplace si la nouvelle source est plus fiable.

    Un contact issu de societeinfo (0.90) ne doit jamais être dégradé par une
    extraction de site (0.60) : la mise à jour est conditionnée à la confiance.
    """
    if is_primary:
        demote_other_primaries(db, company_id=company_id, channel=channel, value=value)

    statement = insert(Contact).values(
        company_id=company_id,
        channel=channel,
        value=value,
        display_value=display_value,
        origin=origin,
        confidence=confidence,
        is_generic=is_generic,
        is_primary=is_primary,
    )
    db.execute(
        statement.on_conflict_do_update(
            constraint="uq_contact",
            set_={
                "display_value": statement.excluded.display_value,
                "origin": statement.excluded.origin,
                "confidence": statement.excluded.confidence,
                "is_generic": statement.excluded.is_generic,
                "is_primary": statement.excluded.is_primary,
            },
            where=statement.excluded.confidence > Contact.confidence,
        )
    )


def list_for_company(db: Session, company_id: int) -> list[Contact]:
    """Contacts d'une entreprise, du plus fiable au moins fiable."""
    statement = (
        select(Contact)
        .where(Contact.company_id == company_id)
        .order_by(Contact.confidence.desc(), Contact.id)
    )
    return list(db.execute(statement).scalars())
